"""TCP socket server for remote control of GC monitoring."""

import io
import json
import logging
import socketserver
import threading
from typing import TYPE_CHECKING, Any, override

from .jsonl_exporter import JsonlExporter
from .stdout_exporter import StdoutExporter
from .chrome_trace_exporter import TraceExporter
from .core import connect

if TYPE_CHECKING:
    from .core import GCMonitorThread



logger = logging.getLogger("gc_monitor")

__all__ = ["SocketCommandServer"]


class SocketCommandServer:
    """
    TCP socket server for remote control of GC monitoring.

    Accepts JSON-RPC style commands over TCP and controls the monitor thread.

    Example:
        ```python
        # Start server
        server = SocketCommandServer(
            host="localhost",
            port=9999,
            monitor_thread=thread,
            exporter=exporter,
        )
        server.start()  # Blocks until stop command
        ```

    Command Protocol:
        Request:  {"method": "command_name", ...}
        Response: {"success": true/false, "result"/"error": ...}

    Supported Commands:
        - stop: Stop monitoring and shutdown server
        - help: List available commands
        - status: Return current monitoring status
    """

    def __init__(
        self,
        host: str,
        port: int,
        monitor_thread: "GCMonitorThread"
    ) -> None:
        """
        Initialize the socket command server.

        Args:
            host: Host address to bind to (default: "localhost")
            port: Port number to listen on (default: 9999)
            monitor_thread: GCMonitorThread instance to control
            exporter: GCMonitorExporter instance for status queries
        """
        server_instance = self
        class RequestHandler(socketserver.StreamRequestHandler):
            """Handle incoming client connections."""

            @override
            def handle(self) -> None:
                """Process client requests."""
                server_instance._handle_client(self.rfile, self.wfile)

        self._host = host
        self._port = port
        self._monitor_thread = monitor_thread
        self._lock = threading.Lock()
        self._server_ready = threading.Event()
        self._stop_requested = threading.Event()
        self._stopped = False

        # Create server with allow_reuse_address for quick restarts
        socketserver.TCPServer.allow_reuse_address = True
        self._server = socketserver.TCPServer(
            (self._host, self._port),
            RequestHandler,
        )
        # Set timeout so we can check stop_requested periodically
        self._server.timeout = 0.5
        self._server_thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        """
        Start the socket server.

        Blocks until a stop command is received or an error occurs.
        """

        try:

            # Signal that server is ready
            self._server_ready.set()

            # Serve until stop_requested is set
            while not self._stop_requested.is_set():
                self._server.handle_request()
                self._stop_requested.wait(timeout=0.01)
                print(".", sep="", end="")

        except OSError as e:
            logger.error("Socket server error: %s", e)
            # Signal server ready even on error (with exception)
            self._server_ready.set()
            raise
        finally:
            self._shutdown_server()

    def start(self) -> None:
        """Start the server thread."""
        self._server_thread.start()

    def stop(self) -> None:
        """
        Request server shutdown.

        Sets the stop flag and shuts down the server socket.
        """
        self._stop_requested.set()
        # Don't join if we're already in the server thread
        if threading.current_thread() != self._server_thread:
            self._server_thread.join(5.0)


    def _shutdown_server(self) -> None:
        """Clean up server resources."""
        if not self._stopped:
            try:
                self._server.server_close()
            except OSError:
                pass
        self._stopped = True

    def _handle_client(
        self,
        rfile: io.BufferedIOBase,
        wfile: io.BufferedIOBase,
    ) -> None:
        """
        Handle a client connection.

        Reads JSON commands line by line and sends responses.

        Args:
            rfile: Input file-like object for reading requests
            wfile: Output file-like object for writing responses
        """
        try:
            line = rfile.readline().decode("utf-8").strip()
            if not line:
                return

            try:
                request: dict[str, Any] = json.loads(line)
            except json.JSONDecodeError as e:
                response: dict[str, Any] = {
                    "success": False,
                    "error": f"Invalid JSON: {e}",
                }
                self._send_response(wfile, response)
            else:
                # Process the command
                response = self._process_command(request)
                self._send_response(wfile, response)

        except OSError as e:
            logger.warning("Client connection error: %s", e)
        finally:
            # Close client connection
            try:
                rfile.close()
                wfile.close()
            except OSError:
                pass

    def _send_response(
        self,
        wfile: io.BufferedIOBase,
        response: dict[str, Any],
    ) -> None:
        """
        Send a JSON response to the client.

        Args:
            wfile: Output file-like object for writing response
            response: Response dictionary to send
        """
        response_str = json.dumps(response) + "\n"
        wfile.write(response_str.encode("utf-8"))
        wfile.flush()

    def _process_command(self, request: dict[str, Any]) -> dict[str, Any]:
        """
        Process a JSON command and return the response.

        Args:
            request: Request dictionary with "method" key

        Returns:
            Response dictionary with "success" and "result"/"error" keys
        """
        method = request.get("method")

        if method == "stop":
            return self._cmd_stop()
        elif method == "monitor":
            return self._cmd_monitor(request)
        else:
            return {
                "success": False,
                "error": f"Unknown method: {method}",
            }

    def _cmd_stop(self) -> dict[str, Any]:
        """
        Execute the stop command.

        Stops the monitor thread and requests server shutdown.

        Returns:
            Response dictionary
        """
        logger.info("Stop command received, shutting down...")

        # Stop the monitor thread
        self._monitor_thread.stop()

        # Request server shutdown
        self.stop()

        return {
            "success": True,
            "result": {"message": "Stopping monitoring"},
        }

    def _cmd_monitor(self, request: dict[str, Any]) -> dict[str, Any]:
        try:
            pid = int(request.get("pid", -1))
            output = request.get("output", "")
            output_format = request.get("format", "").lower()
            thread_id = int(request.get("thread-id", -1))
            thread_name = request.get("thread-name", "")
            rate = float(request.get("rate", 0.1))
            fallback = request.get("fallback", "").lower()
            use_fallback = fallback in ("1", "true", "yes", "on")

            if output_format == "stdout":
                exporter = StdoutExporter(pid=pid)
            elif output_format == "jsonl":
                exporter = JsonlExporter(pid=pid, output_path=output, thread_id=thread_id, flush_threshold=2)
            else:
                exporter = TraceExporter(pid=pid, output_path=output, thread_name=thread_name)

            monitor = connect(pid, exporter=exporter, rate=rate, use_fallback=use_fallback)
            self._monitor_thread.add_monitor(monitor)

            return {
                "success": True,
                "result": {"message": f"monitor for {pid} started"},
            }

        except Exception as e:
            return {
                "success": True,
                "result": {"message": str(e)},
            }
