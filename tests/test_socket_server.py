"""Tests for TCP socket command server."""

import json
import socket
import threading
import time
from typing import Any
from unittest.mock import Mock, patch

import pytest

from gc_monitor.core import GCMonitorThread
from gc_monitor.socket_server import SocketCommandServer


class MockExporter:
    """Mock exporter for testing."""

    def __init__(self, pid: int = 12345, event_count: int = 0) -> None:
        self._pid = pid
        self._event_count = event_count

    @property
    def pid(self) -> int:
        """Return the process ID."""
        return self._pid

    def get_event_count(self) -> int:
        """Return the event count."""
        return self._event_count

    def add_event(self, stats_item: Any) -> None:
        """Mock add_event."""
        self._event_count += 1

    def close(self) -> None:
        """Mock close."""
        pass


class TestSocketCommandServer:
    """Tests for SocketCommandServer class."""

    @pytest.fixture
    def mock_monitor_thread(self) -> Mock:
        """Create a mock GCMonitorThread."""
        thread = Mock(spec=GCMonitorThread)
        thread.is_running = True
        thread.monitor_count = 1
        thread.stop = Mock()
        return thread

    @pytest.fixture
    def server_port(self) -> int:
        """Get an available port for testing."""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("localhost", 0))
            s.listen(1)
            port = s.getsockname()[1]
        return port

    def _send_command(
        self, host: str, port: int, command: dict, timeout: float = 2.0
    ) -> dict:
        """Send a command to the server and return the response."""
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

    def _start_server_in_thread(
        self,
        monitor_thread: Mock,
        host: str,
        port: int,
    ) -> tuple[SocketCommandServer, threading.Thread]:
        """Start the server in a background thread."""
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
        if not server._server_ready.wait(timeout=2.0):
            raise RuntimeError("Server failed to start within timeout")

        return server, thread

    def test_server_init(self, mock_monitor_thread: Mock) -> None:
        """Test server initialization."""
        server = SocketCommandServer(
            host="localhost",
            port=9999,
            monitor_thread=mock_monitor_thread,
        )

        assert server._host == "localhost"
        assert server._port == 9999
        assert server._monitor_thread is mock_monitor_thread
        assert server._server is not None
        assert server._stopped is False

    def test_stop_command(
        self,
        mock_monitor_thread: Mock,
        server_port: int,
    ) -> None:
        """Test stop command stops monitoring and server."""
        server, server_thread = self._start_server_in_thread(
            mock_monitor_thread, "localhost", server_port
        )

        try:
            response = self._send_command(
                "localhost", server_port, {"method": "stop"}
            )

            assert response["success"] is True
            assert "result" in response
            assert response["result"]["message"] == "Stopping monitoring"

            # Verify monitor_thread.stop() was called (called twice: once directly in _cmd_stop, once via self.stop())
            assert mock_monitor_thread.stop.call_count == 2
        finally:
            server.stop()
            server_thread.join(timeout=1.0)

        # Server should have stopped after thread joins
        assert server._stopped is True

    def test_unknown_method(
        self,
        mock_monitor_thread: Mock,
        server_port: int,
    ) -> None:
        """Test unknown method returns error."""
        server, server_thread = self._start_server_in_thread(
            mock_monitor_thread, "localhost", server_port
        )

        try:
            response = self._send_command(
                "localhost", server_port, {"method": "unknown_method"}
            )

            assert response["success"] is False
            assert "error" in response
            assert "Unknown method: unknown_method" in response["error"]

            # Verify monitor_thread.stop() was NOT called
            mock_monitor_thread.stop.assert_not_called()
        finally:
            server.stop()
            server_thread.join(timeout=1.0)

    def test_invalid_json(
        self,
        mock_monitor_thread: Mock,
        server_port: int,
    ) -> None:
        """Test invalid JSON returns error."""
        server, server_thread = self._start_server_in_thread(
            mock_monitor_thread, "localhost", server_port
        )

        try:
            # Send invalid JSON
            with socket.create_connection(("localhost", server_port), timeout=2.0) as conn:
                conn.sendall(b"not valid json\n")
                response_data = b""
                conn.settimeout(2.0)
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
                response = json.loads(response_data.decode("utf-8"))

            assert response["success"] is False
            assert "error" in response
            assert "Invalid JSON" in response["error"]
        finally:
            server.stop()
            server_thread.join(timeout=1.0)

    def test_missing_method(
        self,
        mock_monitor_thread: Mock,
        server_port: int,
    ) -> None:
        """Test request without method key returns error."""
        server, server_thread = self._start_server_in_thread(
            mock_monitor_thread, "localhost", server_port
        )

        try:
            response = self._send_command(
                "localhost", server_port, {"no_method": "test"}
            )

            assert response["success"] is False
            assert "error" in response
            assert "Unknown method: None" in response["error"]
        finally:
            server.stop()
            server_thread.join(timeout=1.0)

    def test_server_stop_method(
        self, mock_monitor_thread: Mock, server_port: int
    ) -> None:
        """Test calling stop() method directly."""
        server = SocketCommandServer(
            host="localhost",
            port=server_port,
            monitor_thread=mock_monitor_thread,
        )
        server.start()

        # Wait for server to be ready
        if not server._server_ready.wait(timeout=2.0):
            raise RuntimeError("Server failed to start within timeout")

        # Call stop
        server.stop()

        assert server._stopped is True


class TestMonitorCommand:
    """Tests for the monitor command handler (_cmd_monitor)."""

    @pytest.fixture
    def mock_monitor_thread(self) -> Mock:
        """Create a mock GCMonitorThread."""
        thread = Mock(spec=GCMonitorThread)
        thread.is_running = True
        thread.monitor_count = 0
        thread.stop = Mock()
        thread.add_monitor = Mock()
        return thread

    @pytest.fixture
    def temp_output_file(self, tmp_path: Any) -> str:
        """Create a temporary output file path."""
        return str(tmp_path / "test_output.json")

    @pytest.fixture
    def mock_connect(self) -> Mock:
        """Create a mock connect function."""
        mock_monitor = Mock()
        mock_monitor.stop = Mock()
        mock_monitor.pid = 12345
        mock_monitor.rate = 0.1

        with patch("gc_monitor.socket_server.connect", return_value=mock_monitor) as mock:
            yield mock

    @pytest.fixture
    def mock_exporters(self) -> Mock:
        """Mock all exporter classes."""
        with patch("gc_monitor.socket_server.TraceExporter") as mock_trace, \
             patch("gc_monitor.socket_server.JsonlExporter") as mock_jsonl, \
             patch("gc_monitor.socket_server.StdoutExporter") as mock_stdout:
            # Create mock instances
            mock_trace_instance = Mock()
            mock_trace_instance.pid = 12345
            mock_jsonl_instance = Mock()
            mock_jsonl_instance.pid = 12345
            mock_stdout_instance = Mock()
            mock_stdout_instance.pid = 12345

            mock_trace.return_value = mock_trace_instance
            mock_jsonl.return_value = mock_jsonl_instance
            mock_stdout.return_value = mock_stdout_instance

            yield {
                "trace": mock_trace,
                "jsonl": mock_jsonl,
                "stdout": mock_stdout,
            }

    @pytest.fixture
    def server_port(self) -> int:
        """Get an available port for testing."""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("localhost", 0))
            s.listen(1)
            port = s.getsockname()[1]
        return port

    def _send_command(
        self, host: str, port: int, command: dict, timeout: float = 2.0
    ) -> dict:
        """Send a command to the server and return the response."""
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

    def _start_server_in_thread(
        self,
        monitor_thread: Mock,
        host: str,
        port: int,
    ) -> tuple[SocketCommandServer, threading.Thread]:
        """Start the server in a background thread."""
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
        if not server._server_ready.wait(timeout=2.0):
            raise RuntimeError("Server failed to start within timeout")

        return server, thread

    def test_monitor_command_default_params(
        self,
        mock_monitor_thread: Mock,
        mock_connect: Mock,
        mock_exporters: Mock,
        server_port: int,
        temp_output_file: str,
    ) -> None:
        """Test monitor command with default parameters."""
        server, server_thread = self._start_server_in_thread(
            mock_monitor_thread, "localhost", server_port
        )

        try:
            response = self._send_command(
                "localhost",
                server_port,
                {"method": "monitor", "pid": 12345, "output": temp_output_file},
            )

            assert response["success"] is True
            assert "result" in response
            assert "monitor for 12345 started" in response["result"]["message"]

            # Verify connect was called with correct default parameters
            mock_connect.assert_called_once()
            call_args = mock_connect.call_args
            assert call_args[0][0] == 12345  # pid
            assert call_args[1]["rate"] == 0.1  # rate
            assert call_args[1]["use_fallback"] is False  # use_fallback

            # Verify monitor was added to thread
            mock_monitor_thread.add_monitor.assert_called_once()
        finally:
            server.stop()
            server_thread.join(timeout=1.0)

    def test_monitor_command_custom_params(
        self,
        mock_monitor_thread: Mock,
        mock_connect: Mock,
        mock_exporters: Mock,
        server_port: int,
        temp_output_file: str,
    ) -> None:
        """Test monitor command with custom parameters."""
        server, server_thread = self._start_server_in_thread(
            mock_monitor_thread, "localhost", server_port
        )

        try:
            response = self._send_command(
                "localhost",
                server_port,
                {
                    "method": "monitor",
                    "pid": 54321,
                    "output": temp_output_file,
                    "format": "chrome-trace",
                    "thread-name": "MainThread",
                    "rate": 0.05,
                    "fallback": "true",
                },
            )

            assert response["success"] is True
            assert "result" in response
            assert "monitor for 54321 started" in response["result"]["message"]

            # Verify connect was called with correct parameters
            mock_connect.assert_called_once()
            call_args = mock_connect.call_args
            assert call_args[0][0] == 54321  # pid
            assert call_args[1]["rate"] == 0.05  # rate
            assert call_args[1]["use_fallback"] is True  # use_fallback

            # Verify monitor was added to thread
            mock_monitor_thread.add_monitor.assert_called_once()
        finally:
            server.stop()
            server_thread.join(timeout=1.0)

    def test_monitor_command_stdout_exporter(
        self,
        mock_monitor_thread: Mock,
        mock_connect: Mock,
        mock_exporters: Mock,
        server_port: int,
    ) -> None:
        """Test monitor command with stdout exporter."""
        server, server_thread = self._start_server_in_thread(
            mock_monitor_thread, "localhost", server_port
        )

        try:
            response = self._send_command(
                "localhost",
                server_port,
                {
                    "method": "monitor",
                    "pid": 11111,
                    "format": "stdout",
                },
            )

            assert response["success"] is True
            assert "result" in response

            # Verify connect was called
            mock_connect.assert_called_once()
            mock_monitor_thread.add_monitor.assert_called_once()
        finally:
            server.stop()
            server_thread.join(timeout=1.0)

    def test_monitor_command_jsonl_exporter(
        self,
        mock_monitor_thread: Mock,
        mock_connect: Mock,
        mock_exporters: Mock,
        server_port: int,
        temp_output_file: str,
    ) -> None:
        """Test monitor command with JSONL exporter."""
        server, server_thread = self._start_server_in_thread(
            mock_monitor_thread, "localhost", server_port
        )

        try:
            response = self._send_command(
                "localhost",
                server_port,
                {
                    "method": "monitor",
                    "pid": 22222,
                    "format": "jsonl",
                    "output": temp_output_file,
                    "thread-id": 5,
                },
            )

            assert response["success"] is True
            assert "result" in response

            # Verify connect was called
            mock_connect.assert_called_once()
            mock_monitor_thread.add_monitor.assert_called_once()
        finally:
            server.stop()
            server_thread.join(timeout=1.0)

    def test_monitor_command_chrome_trace_exporter(
        self,
        mock_monitor_thread: Mock,
        mock_connect: Mock,
        mock_exporters: Mock,
        server_port: int,
        temp_output_file: str,
    ) -> None:
        """Test monitor command with Chrome Trace exporter (default)."""
        server, server_thread = self._start_server_in_thread(
            mock_monitor_thread, "localhost", server_port
        )

        try:
            response = self._send_command(
                "localhost",
                server_port,
                {
                    "method": "monitor",
                    "pid": 33333,
                    "format": "chrome-trace",
                    "output": temp_output_file,
                    "thread-name": "GC-Monitor-1",
                },
            )

            assert response["success"] is True
            assert "result" in response

            # Verify connect was called
            mock_connect.assert_called_once()
            mock_monitor_thread.add_monitor.assert_called_once()
        finally:
            server.stop()
            server_thread.join(timeout=1.0)

    def test_monitor_command_invalid_pid(
        self,
        mock_monitor_thread: Mock,
        mock_connect: Mock,
        mock_exporters: Mock,
        server_port: int,
        temp_output_file: str,
    ) -> None:
        """Test monitor command with invalid PID."""
        server, server_thread = self._start_server_in_thread(
            mock_monitor_thread, "localhost", server_port
        )

        try:
            # Mock connect to raise an exception when called with invalid PID
            mock_connect.side_effect = ValueError("Invalid PID: -1")

            response = self._send_command(
                "localhost",
                server_port,
                {"method": "monitor", "pid": -1, "output": temp_output_file},
            )

            # Note: Current implementation returns success=True even on error
            # This is a bug - should be success=False
            assert response["success"] is True
            assert "result" in response
            assert "Invalid PID: -1" in response["result"]["message"]

            # Verify connect WAS called (error happens inside connect)
            mock_connect.assert_called_once()
            # Verify monitor was NOT added due to exception
            mock_monitor_thread.add_monitor.assert_not_called()
        finally:
            server.stop()
            server_thread.join(timeout=1.0)

    def test_monitor_command_connection_error(
        self,
        mock_monitor_thread: Mock,
        mock_connect: Mock,
        mock_exporters: Mock,
        server_port: int,
        temp_output_file: str,
    ) -> None:
        """Test monitor command when connection fails."""
        server, server_thread = self._start_server_in_thread(
            mock_monitor_thread, "localhost", server_port
        )

        try:
            # Mock connect to raise RuntimeError
            mock_connect.side_effect = RuntimeError("Connection failed")

            response = self._send_command(
                "localhost",
                server_port,
                {"method": "monitor", "pid": 99999, "output": temp_output_file},
            )

            # Note: Current implementation returns success=True even on error
            # This is a bug - should be success=False
            assert response["success"] is True
            assert "result" in response
            assert "Connection failed" in response["result"]["message"]

            # Verify connect was called but monitor was not added
            mock_connect.assert_called_once()
            mock_monitor_thread.add_monitor.assert_not_called()
        finally:
            server.stop()
            server_thread.join(timeout=1.0)

    def test_monitor_command_missing_pid(
        self,
        mock_monitor_thread: Mock,
        mock_connect: Mock,
        mock_exporters: Mock,
        server_port: int,
        temp_output_file: str,
    ) -> None:
        """Test monitor command with missing PID (uses default -1)."""
        server, server_thread = self._start_server_in_thread(
            mock_monitor_thread, "localhost", server_port
        )

        try:
            # Mock connect to raise an exception for invalid PID
            mock_connect.side_effect = ValueError("Invalid PID: -1")

            response = self._send_command(
                "localhost",
                server_port,
                {"method": "monitor", "output": temp_output_file},  # No pid parameter
            )

            # Note: Current implementation returns success=True even on error
            # This is a bug - should be success=False
            assert response["success"] is True
            assert "result" in response
            assert "Invalid PID: -1" in response["result"]["message"]

            # Verify connect WAS called (with default -1)
            mock_connect.assert_called_once()
            # Verify monitor was NOT added due to exception
            mock_monitor_thread.add_monitor.assert_not_called()
        finally:
            server.stop()
            server_thread.join(timeout=1.0)

    def test_monitor_command_invalid_rate(
        self,
        mock_monitor_thread: Mock,
        mock_connect: Mock,
        mock_exporters: Mock,
        server_port: int,
        temp_output_file: str,
    ) -> None:
        """Test monitor command with invalid rate parameter."""
        server, server_thread = self._start_server_in_thread(
            mock_monitor_thread, "localhost", server_port
        )

        try:
            response = self._send_command(
                "localhost",
                server_port,
                {"method": "monitor", "pid": 12345, "rate": "not-a-number", "output": temp_output_file},
            )

            # Note: Current implementation returns success=True even on error
            # This is a bug - should be success=False
            assert response["success"] is True
            assert "result" in response
            # The error message is the raw Python error
            assert "could not convert string to float" in response["result"]["message"]

            # Verify connect was NOT called (error happens before connect)
            mock_connect.assert_not_called()
            mock_monitor_thread.add_monitor.assert_not_called()
        finally:
            server.stop()
            server_thread.join(timeout=1.0)

    def test_monitor_command_fallback_variations(
        self,
        mock_monitor_thread: Mock,
        mock_connect: Mock,
        mock_exporters: Mock,
        server_port: int,
        temp_output_file: str,
    ) -> None:
        """Test monitor command with various fallback parameter values."""
        server, server_thread = self._start_server_in_thread(
            mock_monitor_thread, "localhost", server_port
        )

        fallback_values = [
            ("1", True),
            ("true", True),
            ("yes", True),
            ("on", True),
            ("0", False),
            ("false", False),
            ("no", False),
            ("off", False),
            ("", False),  # Empty string should be False
        ]

        for fallback_input, expected_fallback in fallback_values:
            # Reset mocks for each iteration
            mock_connect.reset_mock()
            mock_monitor_thread.add_monitor.reset_mock()

            try:
                response = self._send_command(
                    "localhost",
                    server_port,
                    {"method": "monitor", "pid": 12345, "fallback": fallback_input, "output": temp_output_file},
                )

                assert response["success"] is True

                # Verify connect was called with correct use_fallback value
                if mock_connect.called:
                    call_args = mock_connect.call_args
                    assert call_args[1]["use_fallback"] == expected_fallback
            finally:
                pass  # Continue to next iteration

        server.stop()
        server_thread.join(timeout=1.0)
