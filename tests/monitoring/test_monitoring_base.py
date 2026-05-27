"""Tests for the shared monitoring_base module."""

from unittest.mock import MagicMock

import pytest

from gc_monitor.stats_output import TableFormat


class TestRunMonitoringLoop:
    """Tests for run_monitoring_loop."""

    @pytest.fixture
    def mock_process(self) -> MagicMock:
        process = MagicMock()
        process.pid = 12345
        return process

    @pytest.fixture
    def mock_wait_policy(self) -> MagicMock:
        return MagicMock()

    @pytest.fixture
    def mock_control_server(self) -> MagicMock:
        return MagicMock()

    def test_success(
        self, caplog: pytest.LogCaptureFixture, mock_process: MagicMock, mock_wait_policy: MagicMock,
        monitoring_options: MagicMock, mock_monitoring_base_deps: dict,
        mock_control_server: MagicMock,
    ) -> None:
        from gc_monitor.commands.monitoring_base import run_monitoring_loop

        result = run_monitoring_loop(mock_process, mock_wait_policy, monitoring_options(), control_server=mock_control_server)

        assert result == 0
        assert "Monitoring complete" in caplog.text
        assert "Total events: 5" in caplog.text

    def test_exception_returns_1(
        self, caplog: pytest.LogCaptureFixture, mock_process: MagicMock, mock_wait_policy: MagicMock,
        monitoring_options: MagicMock, mock_monitoring_base_deps: dict,
        mock_control_server: MagicMock,
    ) -> None:
        from gc_monitor.commands.monitoring_base import run_monitoring_loop

        mock_monitoring_base_deps["RunnerFactory"].side_effect = RuntimeError("test error")

        assert run_monitoring_loop(mock_process, mock_wait_policy, monitoring_options(), control_server=mock_control_server) == 1
        assert "Failed to run GC monitor" in caplog.text

    def test_calls_cleanup(
        self, mock_process: MagicMock, mock_wait_policy: MagicMock,
        monitoring_options: MagicMock, mock_monitoring_base_deps: dict,
        mock_control_server: MagicMock,
    ) -> None:
        from gc_monitor.commands.monitoring_base import run_monitoring_loop

        mock_monitoring_base_deps["StreamingStats"].return_value.count.return_value = 0
        cleanup = MagicMock()

        run_monitoring_loop(mock_process, mock_wait_policy, monitoring_options(), control_server=mock_control_server, cleanup=cleanup)

        cleanup.assert_called_once()

    def test_does_not_call_cleanup_on_exception(
        self, mock_process: MagicMock, mock_wait_policy: MagicMock,
        monitoring_options: MagicMock, mock_monitoring_base_deps: dict,
        mock_control_server: MagicMock,
    ) -> None:
        from gc_monitor.commands.monitoring_base import run_monitoring_loop

        mock_monitoring_base_deps["RunnerFactory"].side_effect = RuntimeError("test error")
        cleanup = MagicMock()

        result = run_monitoring_loop(mock_process, mock_wait_policy, monitoring_options(), control_server=mock_control_server, cleanup=cleanup)

        assert result == 1
        cleanup.assert_not_called()

    def test_stdout_format_no_trace_path(
        self, caplog: pytest.LogCaptureFixture, mock_process: MagicMock, mock_wait_policy: MagicMock,
        monitoring_options: MagicMock, mock_monitoring_base_deps: dict,
        mock_control_server: MagicMock,
    ) -> None:
        from gc_monitor.commands.monitoring_base import run_monitoring_loop

        mock_monitoring_base_deps["StreamingStats"].return_value.count.return_value = 0

        run_monitoring_loop(mock_process, mock_wait_policy, monitoring_options(output_format="stdout"), control_server=mock_control_server)

        assert "Trace saved to" not in caplog.text
