import threading
import time
from unittest.mock import MagicMock, Mock, call

import pytest

from gcmon.monitor_loop import MonitorLoop
from gcmon.poll_status import PollStatus
from gcmon.run_policy import InfinityRunner, Runner
from gcmon.wait_policy import WaitPolicy


@pytest.fixture
def mock_monitor():
    monitor = MagicMock()
    monitor.pid = 12345
    monitor.get_child_pids.return_value = []
    monitor.poll.return_value = PollStatus.OK
    monitor.__enter__.return_value = monitor
    monitor.__exit__.return_value = None
    return monitor


@pytest.fixture
def mock_runner():
    runner = Mock(spec=Runner)
    runner.run.return_value = iter([None])
    return runner


@pytest.fixture
def mock_wait_policy():
    policy = Mock(spec=WaitPolicy)
    policy.wait.return_value = True
    return policy


@pytest.fixture
def wait_policy_factory(mock_wait_policy):
    return lambda: mock_wait_policy


@pytest.fixture
def loop(mock_monitor, mock_runner, wait_policy_factory):
    return MonitorLoop(mock_monitor, mock_runner, wait_policy_factory, rate=0.01)


class TestMonitorLoopInit:
    def test_close_sets_stop_event(self, loop):
        assert not loop._stop_event.is_set()
        loop.close()
        assert loop._stop_event.is_set()

    def test_close_idempotent(self, loop):
        loop.close()
        loop.close()
        assert loop._stop_event.is_set()


class TestMonitorLoopRun:
    def test_polls_pid(self, loop, mock_monitor, mock_wait_policy):
        loop.run()

        mock_monitor.poll.assert_called_once_with(12345)
        mock_wait_policy.wait.assert_called_once_with(PollStatus.OK)

    def test_polls_child_pids(self, loop, mock_monitor):
        mock_monitor.get_child_pids.return_value = [999, 888]
        mock_monitor.poll.reset_mock()

        loop.run()

        assert mock_monitor.poll.call_args_list == [
            call(12345),
            call(999),
            call(888),
        ]

    def test_stop_before_run_skips_loop(self, mock_monitor):
        runner = Mock(spec=Runner)
        runner.run.return_value = iter([])
        loop = MonitorLoop(mock_monitor, runner, lambda: Mock(spec=WaitPolicy), rate=0.01)
        loop._stop_event.set()

        loop.run()

        mock_monitor.poll.assert_not_called()

    def test_break_when_wait_returns_false(self, loop, mock_wait_policy):
        mock_wait_policy.wait.return_value = False

        loop.run()

        mock_wait_policy.wait.assert_called_once()

    def test_continues_looping(self, loop, mock_runner, mock_wait_policy):
        mock_runner.run.return_value = iter([None, None, None])

        loop.run()

        assert mock_wait_policy.wait.call_count == 3

    def test_close_during_run(self, mock_monitor, wait_policy_factory):
        loop = MonitorLoop(mock_monitor, InfinityRunner(), wait_policy_factory, rate=0.01)

        t = threading.Thread(target=loop.run, daemon=True)
        t.start()
        time.sleep(0.05)
        loop.close()
        t.join(timeout=2)

        assert mock_monitor.poll.called
        assert loop._stop_event.is_set()

    def test_stop_event_set_after_normal_exit(self, loop):
        assert not loop._stop_event.is_set()
        loop.run()
        assert loop._stop_event.is_set()


class TestMonitorLoopContextManager:
    def test_exit_calls_close(self, mock_monitor):
        loop = MonitorLoop(mock_monitor, Mock(spec=Runner), lambda: Mock(spec=WaitPolicy))
        assert not loop._stop_event.is_set()
        with loop:
            pass
        assert loop._stop_event.is_set()
