"""Tests for the shared monitoring_base module."""

from unittest.mock import ANY, MagicMock

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
        control = MagicMock()
        def _exit(*args):
            control.close()
            return None
        control.__exit__.side_effect = _exit
        return control

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
        mock_control_server.close.assert_called_once()

    def test_exception_returns_1(
        self, caplog: pytest.LogCaptureFixture, mock_process: MagicMock, mock_wait_policy: MagicMock,
        monitoring_options: MagicMock, mock_monitoring_base_deps: dict,
        mock_control_server: MagicMock,
    ) -> None:
        from gc_monitor.commands.monitoring_base import run_monitoring_loop

        mock_monitoring_base_deps["RunnerFactory"].side_effect = RuntimeError("test error")

        result = run_monitoring_loop(mock_process, mock_wait_policy, monitoring_options(), control_server=mock_control_server)
        assert result == 1
        assert "Failed to run GC monitor" in caplog.text
        mock_control_server.close.assert_called_once()
        mock_monitoring_base_deps["create_monitor"].return_value.__exit__.assert_called_once()

    def test_loop_run_exception_returns_1(
        self, caplog: pytest.LogCaptureFixture, mock_process: MagicMock, mock_wait_policy: MagicMock,
        monitoring_options: MagicMock, mock_monitoring_base_deps: dict,
        mock_control_server: MagicMock,
    ) -> None:
        from gc_monitor.commands.monitoring_base import run_monitoring_loop

        mock_monitoring_base_deps["MonitorLoop"].return_value.run.side_effect = RuntimeError("runtime error")

        result = run_monitoring_loop(mock_process, mock_wait_policy, monitoring_options(), control_server=mock_control_server)
        assert result == 1
        assert "Failed to run GC monitor" in caplog.text
        mock_control_server.close.assert_called_once()

    def test_control_server_enter_exception_returns_1(
        self, caplog: pytest.LogCaptureFixture, mock_process: MagicMock, mock_wait_policy: MagicMock,
        monitoring_options: MagicMock, mock_monitoring_base_deps: dict,
        mock_control_server: MagicMock,
    ) -> None:
        from gc_monitor.commands.monitoring_base import run_monitoring_loop

        mock_control_server.__enter__.side_effect = RuntimeError("enter error")

        result = run_monitoring_loop(mock_process, mock_wait_policy, monitoring_options(), control_server=mock_control_server)
        assert result == 1
        assert "Failed to run GC monitor" in caplog.text
        mock_control_server.close.assert_not_called()
        mock_monitoring_base_deps["create_monitor"].return_value.__exit__.assert_called_once()

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

    def test_show_stats_calls_print_stats(
        self, caplog: pytest.LogCaptureFixture, mock_process: MagicMock, mock_wait_policy: MagicMock,
        monitoring_options: MagicMock, mock_monitoring_base_deps: dict,
        mock_control_server: MagicMock,
    ) -> None:
        from gc_monitor.commands.monitoring_base import run_monitoring_loop

        mock_monitoring_base_deps["StreamingStats"].return_value.count.return_value = 0

        result = run_monitoring_loop(mock_process, mock_wait_policy, monitoring_options(show_stats=True), control_server=mock_control_server)

        assert result == 0
        mock_monitoring_base_deps["print_stats"].assert_called_once()

    def test_enabled_callback_forwarded(
        self, caplog: pytest.LogCaptureFixture, mock_process: MagicMock, mock_wait_policy: MagicMock,
        monitoring_options: MagicMock, mock_monitoring_base_deps: dict,
        mock_control_server: MagicMock,
    ) -> None:
        from gc_monitor.commands.monitoring_base import run_monitoring_loop

        enabled_cb = MagicMock()
        enabled_cb.return_value = True

        result = run_monitoring_loop(mock_process, mock_wait_policy, monitoring_options(), control_server=mock_control_server, enabled=enabled_cb)

        assert result == 0
        mock_monitoring_base_deps["MonitorLoop"].assert_called_once_with(
            ANY, ANY, ANY, rate=0.1, enabled=enabled_cb,
        )
