"""Tests for child-side control plane API."""

import os
from unittest.mock import MagicMock, patch

import pytest

from gc_monitor.control.control_client import ControlClient, _default_connect, connect_with_retry


@pytest.fixture
def mock_conn():
    return MagicMock()


@pytest.fixture
def mock_connection_factory(mock_conn):
    return MagicMock(return_value=mock_conn)


@pytest.fixture
def client(mock_connection_factory):
    return ControlClient("test-address", connection_factory=mock_connection_factory)


class TestStartMonitoring:
    def test_sends_start_message(self, client, mock_conn):
        client.start_monitoring()
        mock_conn.send.assert_called_once_with({"msg": "start", "pid": os.getpid()})


class TestStopMonitoring:
    def test_sends_stop_message(self, client, mock_conn):
        client.stop_monitoring()
        mock_conn.send.assert_called_once_with({"msg": "stop", "pid": os.getpid()})


class TestPauseMonitoring:
    def test_sends_stop_then_start(self, client, mock_conn):
        with client.pause_monitoring():
            pass
        assert mock_conn.send.call_count == 2
        assert mock_conn.send.call_args_list[0][0][0]["msg"] == "stop"
        assert mock_conn.send.call_args_list[1][0][0]["msg"] == "start"

    def test_resumes_on_exception(self, client, mock_conn):
        with pytest.raises(RuntimeError):
            with client.pause_monitoring():
                raise RuntimeError()
        assert mock_conn.send.call_count == 2
        assert mock_conn.send.call_args_list[0][0][0]["msg"] == "stop"
        assert mock_conn.send.call_args_list[1][0][0]["msg"] == "start"


class TestSend:
    def test_noop_when_not_connected(self):
        client = ControlClient(connection_factory=MagicMock(return_value=None))
        client._send({"msg": "test"})

    def test_sends_message_when_connected(self, client, mock_conn):
        client._send({"msg": "test"})
        mock_conn.send.assert_called_once_with({"msg": "test", "pid": os.getpid()})


class TestEnsureConnected:
    def test_creates_connection_once(self, mock_connection_factory, mock_conn):
        client = ControlClient("test-address", connection_factory=mock_connection_factory)
        result1 = client._ensure_connected()
        result2 = client._ensure_connected()
        assert result1 is mock_conn
        assert result2 is mock_conn
        mock_connection_factory.assert_called_once_with("test-address")

    def test_returns_none_without_address(self, monkeypatch):
        monkeypatch.delenv("GC_MONITOR_CONTROL_ADDRESS", raising=False)
        client = ControlClient(connection_factory=MagicMock())
        assert client._ensure_connected() is None

    def test_falls_back_to_env_var(self, monkeypatch, mock_connection_factory, mock_conn):
        monkeypatch.setenv("GC_MONITOR_CONTROL_ADDRESS", "env-address")
        client = ControlClient(connection_factory=mock_connection_factory)
        assert client._ensure_connected() is mock_conn
        mock_connection_factory.assert_called_once_with("env-address")


class TestDefaultConnect:
    def test_returns_none_on_connection_failure(self):
        with patch("gc_monitor.control.control_client.time.sleep"):
            assert _default_connect("/nonexistent/control/socket") is None


class TestConnectWithRetry:
    def test_connects_on_first_attempt(self):
        mock_conn = MagicMock()
        factory = MagicMock(return_value=mock_conn)
        with patch("gc_monitor.control.control_client.Client", factory):
            result = connect_with_retry("test-address")
        assert result is mock_conn
        factory.assert_called_once_with("test-address")

    def test_retries_on_failure_and_succeeds(self):
        mock_conn = MagicMock()
        factory = MagicMock(side_effect=[OSError("conn refused"), mock_conn])
        with (
            patch("gc_monitor.control.control_client.Client", factory),
            patch("gc_monitor.control.control_client.time.sleep"),
        ):
            result = connect_with_retry("test-address")
        assert result is mock_conn
        assert factory.call_count == 2

    def test_returns_none_after_timeout(self):
        factory = MagicMock(side_effect=OSError("conn refused"))
        with (
            patch("gc_monitor.control.control_client.Client", factory),
            patch("gc_monitor.control.control_client.time.sleep"),
        ):
            result = connect_with_retry("test-address", timeout=0.1)
        assert result is None
