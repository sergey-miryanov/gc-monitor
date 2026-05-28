"""Tests for child-side control plane API."""

import os
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _reset_conn():
    """Reset the module-level connection state before and after each test."""
    import gc_monitor.control.control_client as cc
    before = cc._conn
    cc._conn = None
    yield
    cc._conn = None


@pytest.fixture
def mock_conn() -> MagicMock:
    return MagicMock()


@pytest.fixture
def patch_create_connection(mock_conn: MagicMock) -> None:
    with patch("gc_monitor.control.control_client._create_connection", return_value=mock_conn):
        yield


class TestStartMonitoring:
    def test_sends_start_message(self, patch_create_connection, mock_conn: MagicMock) -> None:
        from gc_monitor.control.control_client import start_monitoring

        start_monitoring()

        mock_conn.send.assert_called_once()
        call_args = mock_conn.send.call_args[0][0]
        assert call_args["msg"] == "start"
        assert call_args["pid"] == os.getpid()


class TestStopMonitoring:
    def test_sends_stop_message(self, patch_create_connection, mock_conn: MagicMock) -> None:
        from gc_monitor.control.control_client import stop_monitoring

        stop_monitoring()

        mock_conn.send.assert_called_once()
        call_args = mock_conn.send.call_args[0][0]
        assert call_args["msg"] == "stop"
        assert call_args["pid"] == os.getpid()


class TestPauseMonitoring:
    def test_sends_stop_then_start(self, patch_create_connection, mock_conn: MagicMock) -> None:
        from gc_monitor.control.control_client import pause_monitoring

        with pause_monitoring():
            pass

        assert mock_conn.send.call_count == 2
        stop_msg = mock_conn.send.call_args_list[0][0][0]
        start_msg = mock_conn.send.call_args_list[1][0][0]
        assert stop_msg["msg"] == "stop"
        assert start_msg["msg"] == "start"

    def test_resumes_on_exception(self, patch_create_connection, mock_conn: MagicMock) -> None:
        from gc_monitor.control.control_client import pause_monitoring

        with pytest.raises(RuntimeError):
            with pause_monitoring():
                raise RuntimeError("test error")

        assert mock_conn.send.call_count == 2
        stop_msg = mock_conn.send.call_args_list[0][0][0]
        start_msg = mock_conn.send.call_args_list[1][0][0]
        assert stop_msg["msg"] == "stop"
        assert start_msg["msg"] == "start"

class TestCreateConnection:
    def test_returns_none_without_env(self) -> None:
        from gc_monitor.control.control_client import _create_connection

        with patch.dict(os.environ, {}, clear=True):
            result = _create_connection()
            assert result is None

    def test_returns_none_with_invalid_address(self) -> None:
        from gc_monitor.control.control_client import _create_connection

        with patch.dict(os.environ, {
            "GC_MONITOR_CONTROL_ADDRESS": "not-json",
            "GC_MONITOR_CONTROL_FAMILY": "AF_INET",
        }, clear=True):
            result = _create_connection()
            assert result is None

    def test_returns_none_on_connection_failure(self) -> None:
        from gc_monitor.control.control_client import _create_connection

        with patch.dict(os.environ, {
            "GC_MONITOR_CONTROL_ADDRESS": '["localhost", 99999]',
            "GC_MONITOR_CONTROL_FAMILY": "AF_INET",
        }, clear=True):
            result = _create_connection()
            assert result is None


class TestEnsureConnected:
    def test_creates_connection_once(self, mock_conn: MagicMock) -> None:
        from gc_monitor.control.control_client import _ensure_connected

        with patch("gc_monitor.control.control_client._create_connection", return_value=mock_conn) as mock_create:
            result1 = _ensure_connected()
            result2 = _ensure_connected()

            assert result1 is mock_conn
            assert result2 is mock_conn
            mock_create.assert_called_once()


class TestSend:
    def test_noop_when_not_connected(self) -> None:
        from gc_monitor.control.control_client import _send

        with patch("gc_monitor.control.control_client._ensure_connected", return_value=None):
            _send({"msg": "test"})  # should not raise

    def test_sends_message_when_connected(self, mock_conn: MagicMock) -> None:
        from gc_monitor.control.control_client import _send

        with patch("gc_monitor.control.control_client._ensure_connected", return_value=mock_conn):
            _send({"msg": "test"})

        mock_conn.send.assert_called_once()
