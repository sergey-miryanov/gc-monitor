"""Tests for child-side control plane API."""

import os
from unittest.mock import MagicMock, patch

import pytest

from gc_monitor.control.control_client import ControlClient, _default_connect, connect_with_retry


def assert_payload(mock_conn, expected_msg, *, call_index=0):
    payload = mock_conn.send.call_args_list[call_index][0][0]
    assert payload["msg"] == expected_msg
    assert payload["pid"] == os.getpid()
    assert isinstance(payload["ts"], int)
    return payload


@pytest.fixture
def mock_conn():
    return MagicMock()


@pytest.fixture
def mock_connection_factory(mock_conn):
    return MagicMock(return_value=mock_conn)


@pytest.fixture
def client(mock_connection_factory):
    return ControlClient("test-address", connection_factory=mock_connection_factory)


@pytest.fixture
def disconnected_client():
    return ControlClient("addr", connection_factory=MagicMock(return_value=None))


@pytest.fixture
def patched_client_factory():
    with (
        patch("gc_monitor.control.control_client.Client") as mock_client,
        patch("gc_monitor.control.control_client.time.sleep"),
    ):
        yield mock_client


class TestPublicAPI:
    @pytest.mark.parametrize("method, args, expected_msg", [
        ("start_monitoring", (), "start"),
        ("stop_monitoring", (), "stop"),
        ("instant_msg", ("custom event",), "custom event"),
    ])
    def test_sends_payload(self, client, mock_conn, method, args, expected_msg):
        getattr(client, method)(*args)
        mock_conn.send.assert_called_once()
        assert_payload(mock_conn, expected_msg)

    @pytest.mark.parametrize("raises", [False, True])
    def test_pause_monitoring(self, client, mock_conn, raises):
        if raises:
            with pytest.raises(RuntimeError):
                with client.pause_monitoring():
                    raise RuntimeError()
        else:
            with client.pause_monitoring():
                pass
        assert mock_conn.send.call_count == 2
        assert mock_conn.send.call_args_list[0][0][0]["msg"] == "stop"
        assert mock_conn.send.call_args_list[1][0][0]["msg"] == "start"


class TestSend:
    def test_uses_monotonic_ns(self, client, mock_conn):
        with patch("gc_monitor.control.control_client.time.monotonic_ns", return_value=98765):
            client._send("test")
        mock_conn.send.assert_called_once()
        assert mock_conn.send.call_args[0][0]["ts"] == 98765

    def test_noop_when_not_connected(self, disconnected_client):
        disconnected_client._send("test")

    def test_clears_stale_connection_on_failure(self, client, mock_conn):
        mock_conn.send.side_effect = OSError("broken pipe")
        client._send("test")
        assert client._conn is None
        mock_conn.close.assert_called_once()

    def test_reconnects_after_cleared_connection(self, mock_connection_factory, mock_conn):
        mock_conn.send.side_effect = OSError("broken pipe")
        client = ControlClient("test-address", connection_factory=mock_connection_factory)
        client._send("test")
        assert mock_connection_factory.call_count == 1
        mock_conn.send.side_effect = None
        client._send("retry")
        assert mock_connection_factory.call_count == 2


class TestConnectionLifecycle:
    def test_ensure_connected_creates_once(self, mock_connection_factory, mock_conn):
        client = ControlClient("test-address", connection_factory=mock_connection_factory)
        result1 = client._ensure_connected()
        result2 = client._ensure_connected()
        assert result1 is mock_conn
        assert result2 is mock_conn
        mock_connection_factory.assert_called_once_with("test-address")

    def test_ensure_connected_returns_none_without_address(self, monkeypatch):
        monkeypatch.delenv("GC_MONITOR_CONTROL_ADDRESS", raising=False)
        client = ControlClient(connection_factory=MagicMock())
        assert client._ensure_connected() is None

    def test_ensure_connected_falls_back_to_env_var(self, monkeypatch, mock_connection_factory, mock_conn):
        monkeypatch.setenv("GC_MONITOR_CONTROL_ADDRESS", "env-address")
        client = ControlClient(connection_factory=mock_connection_factory)
        assert client._ensure_connected() is mock_conn
        mock_connection_factory.assert_called_once_with("env-address")

    def test_close_closes_connection(self, client, mock_conn):
        client._ensure_connected()
        client.close()
        mock_conn.close.assert_called_once()
        assert client._conn is None

    def test_close_safe_to_call_multiple_times(self, client, mock_conn):
        client._ensure_connected()
        client.close()
        client.close()
        mock_conn.close.assert_called_once()

    def test_close_noop_when_not_connected(self, disconnected_client):
        disconnected_client.close()

    def test_context_manager_closes_on_exit(self, client, mock_conn):
        client._ensure_connected()
        with client:
            pass
        mock_conn.close.assert_called_once()
        assert client._conn is None


class TestConnectWithRetry:
    def test_connects_on_first_attempt(self, patched_client_factory):
        mock_conn = MagicMock()
        patched_client_factory.return_value = mock_conn
        result = connect_with_retry("test-address")
        assert result is mock_conn
        patched_client_factory.assert_called_once_with("test-address")

    def test_retries_on_failure_and_succeeds(self, patched_client_factory):
        mock_conn = MagicMock()
        patched_client_factory.side_effect = [OSError("conn refused"), mock_conn]
        result = connect_with_retry("test-address")
        assert result is mock_conn
        assert patched_client_factory.call_count == 2

    def test_returns_none_after_timeout(self, patched_client_factory):
        patched_client_factory.side_effect = OSError("conn refused")
        result = connect_with_retry("test-address", timeout=0.1)
        assert result is None


class TestDefaultConnect:
    def test_returns_none_on_connection_failure(self):
        with patch("gc_monitor.control.control_client.time.sleep"):
            assert _default_connect("/nonexistent/control/socket") is None
