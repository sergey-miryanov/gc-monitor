"""Tests for the control plane (ControlServer)."""

import json
import time
from multiprocessing.connection import Client

import pytest

from gc_monitor.control.control_server import ControlServer, set_control_env, CONTROL_ADDRESS_ENV, CONTROL_FAMILY_ENV


@pytest.fixture
def control_server() -> ControlServer:
    server = ControlServer()
    try:
        server.start()
        yield server
    finally:
        server.close()


def _send_msg(server: ControlServer, msg: str, pid: int) -> None:
    address = server.address
    conn = Client(address, family="AF_INET")
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


class TestControlServerInit:
    def test_address_returns_tuple(self, control_server) -> None:
        addr = control_server.address
        assert isinstance(addr, tuple)
        assert len(addr) == 2
        assert isinstance(addr[0], str)
        assert isinstance(addr[1], int)

    def test_is_enabled_defaults_to_true(self, control_server) -> None:
        assert control_server.is_enabled(99999) is True
        assert control_server.is_enabled(0) is True


class TestControlServerStart:
    def test_start_starts_threads(self, control_server: ControlServer) -> None:
        assert control_server._accept_thread.is_alive()
        assert control_server._reader_thread.is_alive()

    def test_accepts_connection(self, control_server: ControlServer) -> None:
        _send_msg(control_server, msg="stop", pid=42)
        assert _wait_msg(control_server, pid=42, expected=False)


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
        assert _wait_msg(control_server, 2, False)
        assert control_server.is_enabled(1) is False
        assert control_server.is_enabled(2) is False
        assert control_server.is_enabled(3) is True  # unknown, default True

        _send_msg(control_server, "start", 1)
        assert _wait_msg(control_server, 1, True)
        assert control_server.is_enabled(1) is True
        assert control_server.is_enabled(2) is False


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


class TestControlServerClose:
    def test_close_stops_accepting(self) -> None:
        server = ControlServer()
        server.start()
        server.close()
        assert server._listener is None

    def test_close_clears_enabled(self) -> None:
        server = ControlServer()
        server.start()
        _send_msg(server, "stop", 42)
        _wait_msg(server, 42, False)
        server.close()
        # After close, is_enabled still works but returns default
        assert server.is_enabled(42) is True

    def test_close_is_idempotent(self) -> None:
        server = ControlServer()
        server.start()
        server.close()
        server.close()  # should not raise


class TestSetControlEnv:
    def test_sets_address_and_family(self) -> None:
        env: dict[str, str] = {}
        set_control_env(env, ("localhost", 9999))
        assert json.loads(env[CONTROL_ADDRESS_ENV]) == ["localhost", 9999]
        assert env[CONTROL_FAMILY_ENV] == "AF_INET"

    def test_sets_string_address(self) -> None:
        env: dict[str, str] = {}
        set_control_env(env, "/tmp/control.sock")
        assert json.loads(env[CONTROL_ADDRESS_ENV]) == "/tmp/control.sock"
