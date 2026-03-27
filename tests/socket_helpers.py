"""Socket server test utilities for gc-monitor tests."""

from __future__ import annotations

import json
import socket
import threading
import time
from typing import Any

from gc_monitor.core import GCMonitorThread
from gc_monitor.socket_server import SocketCommandServer


__all__ = [
    "wait_for_port",
    "send_command",
    "start_server_in_thread",
]


def wait_for_port(port: int, timeout: float = 5.0) -> bool:
    """Wait for a TCP port to become available.

    Args:
        port: Port number to check.
        timeout: Maximum time to wait in seconds.

    Returns:
        True if port is available, False if timeout.
    """
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(0.1)
                result = s.connect_ex(("127.0.0.1", port))
                if result == 0:
                    return True
        except OSError:
            pass
        time.sleep(0.05)
    return False


def send_command(
    host: str, port: int, command: dict[str, Any], timeout: float = 2.0
) -> dict[str, Any]:
    """Send a JSON command to a socket server and return the response.

    Args:
        host: Server hostname.
        port: Server port.
        command: Command dictionary to send.
        timeout: Socket timeout in seconds.

    Returns:
        Response dictionary from the server.

    Raises:
        json.JSONDecodeError: If response is not valid JSON.
        socket.error: If connection fails.
    """
    with socket.create_connection((host, port), timeout=timeout) as conn:
        request = json.dumps(command) + "\n"
        conn.sendall(request.encode("utf-8"))
        response_data = b""
        conn.settimeout(timeout)
        while True:
            try:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                response_data += chunk
                if b"\n" in response_data:
                    break
            except socket.timeout:
                break
        return json.loads(response_data.decode("utf-8"))


def start_server_in_thread(
    monitor_thread: GCMonitorThread | Any,
    host: str,
    port: int,
) -> tuple[SocketCommandServer, threading.Thread]:
    """Start a SocketCommandServer in a background thread.

    Args:
        monitor_thread: The GCMonitorThread instance to use.
        host: Server hostname.
        port: Server port.

    Returns:
        Tuple of (server instance, thread object).

    Raises:
        RuntimeError: If server fails to start within timeout.
    """
    server = SocketCommandServer(
        host=host,
        port=port,
        monitor_thread=monitor_thread,
    )

    def run_server() -> None:
        server.start()

    thread = threading.Thread(target=run_server, daemon=True)
    thread.start()

    # Wait for server to be ready (with timeout)
    if not server._server_ready.wait(timeout=2.0):  # pyright: ignore[reportPrivateUsage]
        raise RuntimeError("Server failed to start within timeout")

    return server, thread
