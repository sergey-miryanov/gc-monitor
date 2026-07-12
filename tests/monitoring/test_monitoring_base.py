"""Tests for the shared monitoring_base module."""

from unittest.mock import ANY, MagicMock

import pytest


class TestRunMonitoringLoop:
    """Tests for run_monitoring_loop."""

    @pytest.fixture
    def mock_factory(self) -> MagicMock:
        factory = MagicMock()
        runner = MagicMock()
        runner.start.return_value = MagicMock(pid=12345)
        runner.returncode = None
        factory.return_value = runner
        return factory

    @pytest.fixture
    def mock_wait_policy_factory(self) -> MagicMock:
        return MagicMock()

    def test_success(
        self,
        caplog: pytest.LogCaptureFixture,
        mock_factory: MagicMock,
        mock_wait_policy_factory: MagicMock,
        monitoring_options: MagicMock,
        mock_monitoring_base_deps: dict[str, MagicMock],
    ) -> None:
        from gcmon.commands.monitoring_base import run_monitoring_loop

        result = run_monitoring_loop(mock_factory, mock_wait_policy_factory, monitoring_options())

        assert result == 0
        assert "Monitoring complete" in caplog.text
        assert "Total events: 5" in caplog.text

    def test_exception_returns_1(
        self,
        caplog: pytest.LogCaptureFixture,
        mock_factory: MagicMock,
        mock_wait_policy_factory: MagicMock,
        monitoring_options: MagicMock,
        mock_monitoring_base_deps: dict[str, MagicMock],
    ) -> None:
        from gcmon.commands.monitoring_base import run_monitoring_loop

        mock_monitoring_base_deps["RunnerFactory"].side_effect = RuntimeError("test error")

        result = run_monitoring_loop(mock_factory, mock_wait_policy_factory, monitoring_options())
        assert result == 1
        assert "Failed to run GC monitor" in caplog.text

    def test_loop_run_exception_returns_1(
        self,
        caplog: pytest.LogCaptureFixture,
        mock_factory: MagicMock,
        mock_wait_policy_factory: MagicMock,
        monitoring_options: MagicMock,
        mock_monitoring_base_deps: dict[str, MagicMock],
    ) -> None:
        from gcmon.commands.monitoring_base import run_monitoring_loop

        mock_monitoring_base_deps["MonitorLoop"].return_value.run.side_effect = RuntimeError("runtime error")

        result = run_monitoring_loop(mock_factory, mock_wait_policy_factory, monitoring_options())
        assert result == 1
        assert "Failed to run GC monitor" in caplog.text

    def test_returns_child_returncode(
        self,
        mock_factory: MagicMock,
        mock_wait_policy_factory: MagicMock,
        monitoring_options: MagicMock,
        mock_monitoring_base_deps: dict[str, MagicMock],
    ) -> None:
        from gcmon.commands.monitoring_base import run_monitoring_loop

        runner = mock_factory.return_value
        runner.returncode = 42

        result = run_monitoring_loop(mock_factory, mock_wait_policy_factory, monitoring_options())
        assert result == 42

    def test_stdout_format_no_trace_path(
        self,
        caplog: pytest.LogCaptureFixture,
        mock_factory: MagicMock,
        mock_wait_policy_factory: MagicMock,
        monitoring_options: MagicMock,
        mock_monitoring_base_deps: dict[str, MagicMock],
    ) -> None:
        from gcmon.commands.monitoring_base import run_monitoring_loop

        mock_monitoring_base_deps["StreamingStats"].return_value.count.return_value = 0

        run_monitoring_loop(mock_factory, mock_wait_policy_factory, monitoring_options(output_format="stdout"))

        assert "Trace saved to" not in caplog.text

    def test_show_stats_calls_print_stats(
        self,
        caplog: pytest.LogCaptureFixture,
        mock_factory: MagicMock,
        mock_wait_policy_factory: MagicMock,
        monitoring_options: MagicMock,
        mock_monitoring_base_deps: dict[str, MagicMock],
    ) -> None:
        from gcmon.commands.monitoring_base import run_monitoring_loop

        mock_monitoring_base_deps["StreamingStats"].return_value.count.return_value = 0

        result = run_monitoring_loop(mock_factory, mock_wait_policy_factory, monitoring_options(show_stats=True))

        assert result == 0
        mock_monitoring_base_deps["print_stats"].assert_called_once()

    def test_factory_called_with_control_address(
        self,
        mock_factory: MagicMock,
        mock_wait_policy_factory: MagicMock,
        monitoring_options: MagicMock,
        mock_monitoring_base_deps: dict[str, MagicMock],
    ) -> None:
        from gcmon.commands.monitoring_base import run_monitoring_loop

        run_monitoring_loop(mock_factory, mock_wait_policy_factory, monitoring_options())

        mock_factory.assert_called_once_with("/tmp/test-address")

    def test_runner_entered_as_context(
        self,
        mock_factory: MagicMock,
        mock_wait_policy_factory: MagicMock,
        monitoring_options: MagicMock,
        mock_monitoring_base_deps: dict[str, MagicMock],
    ) -> None:
        from gcmon.commands.monitoring_base import run_monitoring_loop

        runner = mock_factory.return_value

        run_monitoring_loop(mock_factory, mock_wait_policy_factory, monitoring_options())

        runner.__enter__.assert_called_once()
        runner.__exit__.assert_called_once()

    def test_control_server_started(
        self,
        mock_factory: MagicMock,
        mock_wait_policy_factory: MagicMock,
        monitoring_options: MagicMock,
        mock_monitoring_base_deps: dict[str, MagicMock],
    ) -> None:
        from gcmon.commands.monitoring_base import run_monitoring_loop

        mock_control_instance = mock_monitoring_base_deps["ControlServer"].return_value

        run_monitoring_loop(mock_factory, mock_wait_policy_factory, monitoring_options())

        mock_control_instance.start.assert_called_once()
        mock_control_instance.__enter__.assert_called_once()
        mock_control_instance.__exit__.assert_called_once()

    def test_enabled_uses_control_server(
        self,
        mock_factory: MagicMock,
        mock_wait_policy_factory: MagicMock,
        monitoring_options: MagicMock,
        mock_monitoring_base_deps: dict[str, MagicMock],
    ) -> None:
        from gcmon.commands.monitoring_base import run_monitoring_loop

        mock_control_instance = mock_monitoring_base_deps["ControlServer"].return_value

        run_monitoring_loop(mock_factory, mock_wait_policy_factory, monitoring_options())

        mock_monitoring_base_deps["MonitorLoop"].assert_called_once_with(
            ANY,
            ANY,
            ANY,
            rate=0.1,
            enabled=mock_control_instance.is_enabled,
        )
