"""Tests for the control plane (ControlServer)."""

import sys
import time
import threading
from multiprocessing.connection import Client
from unittest.mock import MagicMock, patch

import pytest

from gc_monitor.control.control_server import (
    CONTROL_ADDRESS_ENV,
    ControlMsg,
    ControlServer,
    set_control_env,
)


@pytest.fixture
def control_server() -> ControlServer:
    server = ControlServer()
    try:
        server.start()
        yield server
    finally:
        server.close()


@pytest.fixture
def server_not_started() -> ControlServer:
    return ControlServer()


@pytest.fixture
def mock_conn():
    return MagicMock()


@pytest.fixture
def mock_exporter():
    m = MagicMock()
    return m


def _send_msg(server: ControlServer, msg: str, pid: int) -> None:
    address = server.address
    conn = Client(address)
    try:
        conn.send({"msg": msg, "pid": pid})
    finally:
        conn.close()


def _wait_msg(control_server: ControlServer, pid: int, expected: bool, timeout: int = 1) -> bool:
    ts = time.monotonic()
    while control_server.is_enabled(pid) is not expected:
        time.sleep(0)
        if time.monotonic() - ts > timeout:
            return False
    return True


# =============================================================================
# Init tests
# =============================================================================


class TestControlServerInit:
    def test_address_returns_string(self, control_server) -> None:
        addr = control_server.address
        assert isinstance(addr, str)

    def test_is_enabled_defaults_to_true(self, control_server) -> None:
        assert control_server.is_enabled(99999) is True
        assert control_server.is_enabled(0) is True

    def test_init_with_custom_name(self) -> None:
        server = ControlServer(address="my-name")
        try:
            assert "gc-monitor-my-name" in server.address
        finally:
            server.close()

    def test_init_listener_not_none(self, server_not_started) -> None:
        assert server_not_started._listener is not None

    def test_init_connections_empty(self, server_not_started) -> None:
        assert server_not_started._connections == set()

    def test_init_enabled_empty(self, server_not_started) -> None:
        assert server_not_started._enabled == {}

    def test_init_not_running(self, server_not_started) -> None:
        assert server_not_started._running is False

    def test_init_exporter_none(self, server_not_started) -> None:
        assert server_not_started._exporter is None

    def test_init_threads_not_alive(self, server_not_started) -> None:
        assert not server_not_started._accept_thread.is_alive()
        assert not server_not_started._reader_thread.is_alive()


# =============================================================================
# Start tests
# =============================================================================


class TestControlServerStart:
    def test_start_starts_threads(self, control_server: ControlServer) -> None:
        assert control_server._accept_thread.is_alive()
        assert control_server._reader_thread.is_alive()

    def test_accepts_connection(self, control_server: ControlServer) -> None:
        _send_msg(control_server, msg="stop", pid=42)
        assert _wait_msg(control_server, pid=42, expected=False)

    def test_start_sets_running(self, server_not_started) -> None:
        server_not_started.start()
        try:
            assert server_not_started.is_running()
        finally:
            server_not_started.close()

    def test_start_clears_stop_event(self, server_not_started) -> None:
        server_not_started._stop_event.set()
        server_not_started.start()
        try:
            assert not server_not_started._stop_event.is_set()
        finally:
            server_not_started.close()

    def test_start_twice_raises(self, control_server) -> None:
        with pytest.raises(AssertionError):
            control_server.start()


# =============================================================================
# Enabled tests
# =============================================================================


class TestControlServerEnabled:
    def test_unknown_pid_defaults_to_true(self, control_server: ControlServer) -> None:
        assert control_server.is_enabled(999) is True

    def test_stop_sets_enabled_false(self, control_server: ControlServer) -> None:
        _send_msg(control_server, "stop", 42)
        assert _wait_msg(control_server, pid=42, expected=False)
        assert control_server.is_enabled(42) is False

    def test_start_sets_enabled_true(self, control_server: ControlServer) -> None:
        _send_msg(control_server, "stop", 42)
        assert _wait_msg(control_server, 42, False)
        assert control_server.is_enabled(42) is False

        _send_msg(control_server, "start", 42)
        assert _wait_msg(control_server, 42, True)
        assert control_server.is_enabled(42) is True

    def test_multiple_pids_independent(self, control_server: ControlServer) -> None:
        _send_msg(control_server, "stop", 1)
        _send_msg(control_server, "stop", 2)
        assert _wait_msg(control_server, 1, False)
        assert _wait_msg(control_server, 2, False)
        assert control_server.is_enabled(1) is False
        assert control_server.is_enabled(2) is False
        assert control_server.is_enabled(3) is True

        _send_msg(control_server, "start", 1)
        assert _wait_msg(control_server, 1, True)
        assert control_server.is_enabled(1) is True
        assert control_server.is_enabled(2) is False

    def test_start_after_stop_removes_pid(self, control_server) -> None:
        _send_msg(control_server, "stop", 42)
        assert _wait_msg(control_server, 42, False)
        _send_msg(control_server, "start", 42)
        assert _wait_msg(control_server, 42, True)
        assert 42 not in control_server._enabled


# =============================================================================
# Exporter tests
# =============================================================================


class TestControlServerExporter:
    def test_set_exporter_receives_instant_events(self, control_server: ControlServer) -> None:
        from tests.helpers import MockExporter

        exporter = MockExporter()
        control_server.set_exporter(exporter)

        _send_msg(control_server, "stop", 42)
        assert _wait_msg(control_server, 42, False)

        assert len(exporter.instant_events) >= 1
        pid, msg = exporter.instant_events[0]
        assert pid == 42
        assert msg.name == "stop GC monitor"
        assert msg.type == "i"

    def test_exporter_receives_multiple_events(self, control_server: ControlServer) -> None:
        from tests.helpers import MockExporter

        exporter = MockExporter()
        control_server.set_exporter(exporter)

        _send_msg(control_server, "stop", 1)
        assert _wait_msg(control_server, 1, False)
        _send_msg(control_server, "start", 1)
        assert _wait_msg(control_server, 1, True)

        assert len(exporter.instant_events) == 2
        assert exporter.instant_events[0][1].name == "stop GC monitor"
        assert exporter.instant_events[1][1].name == "start GC monitor"

    def test_set_exporter_replace(self, control_server) -> None:
        from tests.helpers import MockExporter

        exporter1 = MockExporter()
        exporter2 = MockExporter()
        control_server.set_exporter(exporter1)
        control_server.set_exporter(exporter2)

        _send_msg(control_server, "stop", 1)
        assert _wait_msg(control_server, 1, False)

        assert len(exporter1.instant_events) == 0
        assert len(exporter2.instant_events) >= 1


# =============================================================================
# Internal method tests
# =============================================================================


class TestControlServerInternal:
    def test_add_event_with_exporter(self, server_not_started, mock_exporter) -> None:
        server_not_started.set_exporter(mock_exporter)
        server_not_started._add_event("test event", 42)
        mock_exporter.add_instant_event.assert_called_once()
        args = mock_exporter.add_instant_event.call_args[0]
        assert args[0] == 42
        assert args[1].name == "test event"
        assert args[1].type == "i"

    def test_add_event_without_exporter(self, server_not_started) -> None:
        server_not_started._add_event("test event", 42)

    def test_remove_connections_closes_and_removes(self, server_not_started, mock_conn) -> None:
        server_not_started._connections.add(mock_conn)
        server_not_started._remove_connections([mock_conn])
        assert mock_conn not in server_not_started._connections
        mock_conn.close.assert_called_once()

    def test_remove_connections_multiple(self, server_not_started) -> None:
        c1, c2 = MagicMock(), MagicMock()
        server_not_started._connections.update([c1, c2])
        server_not_started._remove_connections([c1, c2])
        assert server_not_started._connections == set()
        c1.close.assert_called_once()
        c2.close.assert_called_once()

    def test_remove_nonexistent_connection(self, server_not_started, mock_conn) -> None:
        server_not_started._remove_connections([mock_conn])
        mock_conn.close.assert_called_once()

    def test_clear_connections_removes_all(self, server_not_started) -> None:
        c1, c2 = MagicMock(), MagicMock()
        server_not_started._connections.update([c1, c2])
        server_not_started._clear_connections()
        assert server_not_started._connections == set()
        c1.close.assert_called_once()
        c2.close.assert_called_once()

    def test_clear_connections_empty(self, server_not_started) -> None:
        server_not_started._clear_connections()

    def test_close_connections(self, server_not_started) -> None:
        c1, c2 = MagicMock(), MagicMock()
        server_not_started._close_connections([c1, c2])
        c1.close.assert_called_once()
        c2.close.assert_called_once()

    def test_close_connections_suppresses_exception(self, server_not_started) -> None:
        bad_conn = MagicMock()
        bad_conn.close.side_effect = OSError("connection broken")
        server_not_started._close_connections([bad_conn])

    def test_close_connections_empty(self, server_not_started) -> None:
        server_not_started._close_connections([])

    def test_recv_returns_control_msg(self, server_not_started, mock_conn) -> None:
        mock_conn.recv.return_value = {"msg": "start", "pid": 42}
        to_remove = []
        result = server_not_started._recv(mock_conn, to_remove)
        assert result is not None
        assert result.msg == "start"
        assert result.pid == 42
        assert to_remove == []

    def test_recv_eof_removes_conn(self, server_not_started, mock_conn) -> None:
        mock_conn.recv.side_effect = EOFError()
        to_remove = []
        result = server_not_started._recv(mock_conn, to_remove)
        assert result is None
        assert mock_conn in to_remove

    def test_recv_oserror_removes_conn(self, server_not_started, mock_conn) -> None:
        mock_conn.recv.side_effect = OSError("pipe broken")
        to_remove = []
        result = server_not_started._recv(mock_conn, to_remove)
        assert result is None
        assert mock_conn in to_remove

    def test_recv_connection_error_removes_conn(self, server_not_started, mock_conn) -> None:
        mock_conn.recv.side_effect = ConnectionError("connection reset")
        to_remove = []
        result = server_not_started._recv(mock_conn, to_remove)
        assert result is None
        assert mock_conn in to_remove

    def test_recv_generic_exception_removes_conn(self, server_not_started, mock_conn) -> None:
        mock_conn.recv.side_effect = ValueError("bad data")
        to_remove = []
        result = server_not_started._recv(mock_conn, to_remove)
        assert result is None
        assert mock_conn in to_remove

    def test_recv_existing_to_remove_appended(self, server_not_started, mock_conn) -> None:
        mock_conn.recv.side_effect = EOFError()
        other = MagicMock()
        to_remove = [other]
        result = server_not_started._recv(mock_conn, to_remove)
        assert result is None
        assert len(to_remove) == 2
        assert other in to_remove
        assert mock_conn in to_remove

    def test_safe_wait_returns_ready(self, server_not_started, mock_conn) -> None:
        with patch("gc_monitor.control.control_server._wait", return_value=[mock_conn]):
            result = server_not_started._safe_wait([mock_conn])
        assert result == [mock_conn]

    def test_safe_wait_exception_polls_conns(self, server_not_started) -> None:
        c1, c2 = MagicMock(), MagicMock()
        c1.poll.return_value = True
        c2.poll.return_value = True
        with patch("gc_monitor.control.control_server._wait", side_effect=Exception("wait failed")):
            result = server_not_started._safe_wait([c1, c2])
        assert result == []
        c1.poll.assert_called_once_with(timeout=0)
        c2.poll.assert_called_once_with(timeout=0)

    def test_safe_wait_exception_removes_broken(self, server_not_started) -> None:
        c1, c2 = MagicMock(), MagicMock()
        c1.poll.side_effect = OSError("broken")
        c2.poll.return_value = True
        server_not_started._connections.update([c1, c2])

        with patch("gc_monitor.control.control_server._wait", side_effect=Exception("wait failed")):
            result = server_not_started._safe_wait([c1, c2])

        assert result == []
        assert c1 not in server_not_started._connections
        assert c2 in server_not_started._connections
        c1.close.assert_called_once()

    def test_safe_wait_exception_all_bad(self, server_not_started) -> None:
        c1, c2 = MagicMock(), MagicMock()
        c1.poll.side_effect = OSError("broken")
        c2.poll.side_effect = ConnectionError("reset")
        server_not_started._connections.update([c1, c2])

        with patch("gc_monitor.control.control_server._wait", side_effect=Exception("wait failed")):
            result = server_not_started._safe_wait([c1, c2])

        assert result == []
        assert server_not_started._connections == set()
        c1.close.assert_called_once()
        c2.close.assert_called_once()


# =============================================================================
# Accept loop tests
# =============================================================================


class TestControlServerAcceptLoop:
    def test_accept_loop_stops_on_stop_event(self, server_not_started) -> None:
        server_not_started._stop_event.set()
        server_not_started._accept_loop()

    def test_accept_loop_stops_on_listener_none(self, server_not_started) -> None:
        server_not_started._listener = None
        server_not_started._accept_loop()

    def test_accept_loop_accept_exception_breaks(self, server_not_started) -> None:
        with patch("gc_monitor.control.control_server._accept", side_effect=OSError("accept failed")):
            server_not_started._accept_loop()
        assert len(server_not_started._connections) == 0

    def test_accept_loop_adds_connection(self, server_not_started, mock_conn) -> None:
        server_not_started._listener = MagicMock()
        with patch("gc_monitor.control.control_server._accept", return_value=mock_conn):
            t = threading.Thread(target=server_not_started._accept_loop, daemon=True)
            t.start()
            time.sleep(0.05)
            server_not_started._stop_event.set()
            t.join(timeout=1)

        assert mock_conn in server_not_started._connections

    def test_accept_loop_closes_orphaned_conn_on_exception(self, server_not_started, mock_conn) -> None:
        mock_listener = MagicMock()
        mock_listener.address = "/tmp/gc-monitor-test"
        server_not_started._listener = mock_listener

        call_count = [0]
        mock_conn2 = MagicMock()

        def _accept_side(*args):
            call_count[0] += 1
            if call_count[0] == 1:
                return mock_conn
            raise OSError("second accept fails")

        with patch("gc_monitor.control.control_server._accept", side_effect=_accept_side):
            server_not_started._accept_loop()

        assert not mock_conn.close.called
        assert mock_conn in server_not_started._connections


# =============================================================================
# Reader loop tests
# =============================================================================


class TestControlServerReaderLoop:
    def test_reader_loop_stops_on_stop_event(self, server_not_started) -> None:
        server_not_started._stop_event.set()
        server_not_started._reader_loop()

    def test_reader_loop_processes_start_msg(self, server_not_started, mock_conn) -> None:
        mock_conn.recv.return_value = {"msg": "start", "pid": 42}
        server_not_started._connections.add(mock_conn)
        server_not_started._enabled[42] = False

        with (
            patch("gc_monitor.control.control_server._wait", return_value=[mock_conn]),
            patch.object(server_not_started._stop_event, "wait", side_effect=lambda t: server_not_started._stop_event.set()),
        ):
            server_not_started._reader_loop()

        assert 42 not in server_not_started._enabled

    def test_reader_loop_processes_stop_msg(self, server_not_started, mock_conn) -> None:
        mock_conn.recv.return_value = {"msg": "stop", "pid": 42}
        server_not_started._connections.add(mock_conn)

        with (
            patch("gc_monitor.control.control_server._wait", return_value=[mock_conn]),
            patch.object(server_not_started._stop_event, "wait", side_effect=lambda t: server_not_started._stop_event.set()),
        ):
            server_not_started._reader_loop()

        assert server_not_started._enabled.get(42) is False

    def test_reader_loop_removes_bad_connection(self, server_not_started, mock_conn) -> None:
        mock_conn.recv.side_effect = EOFError()
        server_not_started._connections.add(mock_conn)

        with (
            patch("gc_monitor.control.control_server._wait", return_value=[mock_conn]),
            patch.object(server_not_started._stop_event, "wait", side_effect=lambda t: server_not_started._stop_event.set()),
        ):
            server_not_started._reader_loop()

        assert mock_conn not in server_not_started._connections
        mock_conn.close.assert_called_once()

    def test_reader_loop_handles_malformed_msg(self, server_not_started, mock_conn) -> None:
        mock_conn.recv.return_value = {"bad": "data"}
        server_not_started._connections.add(mock_conn)

        with (
            patch("gc_monitor.control.control_server._wait", return_value=[mock_conn]),
            patch.object(server_not_started._stop_event, "wait", side_effect=lambda t: server_not_started._stop_event.set()),
        ):
            server_not_started._reader_loop()

        assert mock_conn not in server_not_started._connections
        mock_conn.close.assert_called_once()

    def test_reader_loop_no_connections(self, server_not_started) -> None:
        with (
            patch("gc_monitor.control.control_server._wait", return_value=[]),
            patch.object(server_not_started._stop_event, "wait", side_effect=lambda t: server_not_started._stop_event.set()),
        ):
            server_not_started._reader_loop()


# =============================================================================
# Close tests
# =============================================================================


class TestControlServerClose:
    def test_close_stops_accepting(self) -> None:
        server = ControlServer()
        server.start()
        server.close()
        assert not server.is_running()

    def test_close_clears_enabled(self) -> None:
        server = ControlServer()
        server.start()
        _send_msg(server, "stop", 42)
        _wait_msg(server, 42, False)
        server.close()
        assert server.is_enabled(42) is True

    def test_close_is_idempotent(self) -> None:
        server = ControlServer()
        server.start()
        server.close()
        server.close()

    def test_close_not_started(self) -> None:
        server = ControlServer()
        server.close()
        assert not server.is_running()

    def test_close_closes_listener(self) -> None:
        server = ControlServer()
        server.start()
        server.close()
        assert server._listener is None

    def test_close_clears_connections(self) -> None:
        server = ControlServer()
        server.start()
        server.close()
        assert len(server._connections) == 0

    def test_close_twice_not_started(self) -> None:
        server = ControlServer()
        server.close()
        server.close()


# =============================================================================
# set_control_env tests
# =============================================================================


class TestSetControlEnv:
    def test_sets_address(self) -> None:
        env: dict[str, str] = {}
        set_control_env(env, "/tmp/gc-monitor-test")
        assert env[CONTROL_ADDRESS_ENV] == "/tmp/gc-monitor-test"


# =============================================================================
# Platform-specific tests
# =============================================================================


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-specific test")
class TestPlatformWindows:
    def test_make_address_windows(self) -> None:
        from gc_monitor.control.control_server import _make_address

        result = _make_address("test-name")
        assert result == r"\\.\pipe\gc-monitor-test-name"

    def test_tconnection_is_pipe_connection(self) -> None:
        from gc_monitor.control.control_server import TConnection
        from multiprocessing.connection import PipeConnection

        assert TConnection is PipeConnection


@pytest.mark.skipif(sys.platform == "win32", reason="Unix-specific test")
class TestPlatformUnix:
    def test_make_address_unix(self) -> None:
        from gc_monitor.control.control_server import _make_address

        result = _make_address("test-name")
        assert result == "/tmp/gc-monitor-test-name"

    def test_tconnection_is_connection(self) -> None:
        from gc_monitor.control.control_server import TConnection
        from multiprocessing.connection import Connection

        assert TConnection is Connection


# =============================================================================
# Thread safety tests
# =============================================================================


class TestControlServerThreadSafety:
    def test_concurrent_is_enabled(self, control_server) -> None:
        errors = []

        def access_enabled():
            try:
                for _ in range(50):
                    control_server.is_enabled(42)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=access_enabled) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert len(errors) == 0

    def test_concurrent_set_exporter_and_add_event(self, control_server) -> None:
        from tests.helpers import MockExporter

        barrier = threading.Barrier(2, timeout=5)
        errors = []

        def set_exporter_loop():
            try:
                barrier.wait()
                for _ in range(20):
                    control_server.set_exporter(MockExporter())
            except Exception as e:
                errors.append(e)

        def add_event_loop():
            try:
                barrier.wait()
                for _ in range(20):
                    control_server._add_event("test", 1)
            except Exception as e:
                errors.append(e)

        t1 = threading.Thread(target=set_exporter_loop, daemon=True)
        t2 = threading.Thread(target=add_event_loop, daemon=True)
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)

        assert len(errors) == 0
