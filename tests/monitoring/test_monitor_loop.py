import threading
import time
from unittest.mock import MagicMock, Mock, call, patch

import pytest

from gcmon.monitor import EventsMonitor
from gcmon.monitor_loop import MonitorLoop
from gcmon.poll_status import PollStatus, ProcessLifecycle
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


class TestMonitorLoopVanishedPids:
    """Tests for the ``MonitorLoop`` mechanism that detects child pids
    which disappeared from the parent's child list between poll cycles
    (process died without a final ``poll()`` returning ``INVALID_PROCESS``)."""

    def test_vanished_child_marks_died(
        self, mock_monitor, mock_runner, wait_policy_factory,
    ) -> None:
        # Iteration 0: parent + two children. Iteration 1: parent + one
        # child (888 vanished). Iteration 2: parent only (999 vanished).
        mock_monitor.get_child_pids.side_effect = [
            [999, 888],
            [999],
            [],
        ]
        mock_monitor.mark_pid_died.return_value = True
        mock_runner.run.return_value = iter([None, None, None])
        loop = MonitorLoop(mock_monitor, mock_runner, wait_policy_factory, rate=0.01)

        loop.run()

        # Both 888 and 999 vanished across iterations and were marked died.
        mock_monitor.mark_pid_died.assert_any_call(888)
        mock_monitor.mark_pid_died.assert_any_call(999)
        assert mock_monitor.mark_pid_died.call_args_list == [call(888), call(999)]

    def test_no_vanished_call_when_children_unchanged(
        self, mock_monitor, mock_runner, wait_policy_factory,
    ) -> None:
        mock_monitor.get_child_pids.return_value = [999]
        mock_runner.run.return_value = iter([None, None])
        loop = MonitorLoop(mock_monitor, mock_runner, wait_policy_factory, rate=0.01)

        loop.run()

        mock_monitor.mark_pid_died.assert_not_called()

    def test_no_vanished_call_when_monitor_returns_false(
        self, mock_monitor, mock_runner, wait_policy_factory,
    ) -> None:
        # mark_pid_died returns False when the pid was never marked
        # alive in the monitor (e.g. it was only ever in the children
        # list but never successfully polled).
        mock_monitor.get_child_pids.side_effect = [[999], []]
        mock_monitor.mark_pid_died.return_value = False
        mock_runner.run.return_value = iter([None, None])
        loop = MonitorLoop(mock_monitor, mock_runner, wait_policy_factory, rate=0.01)

        loop.run()

        mock_monitor.mark_pid_died.assert_called_once_with(999)

    def test_vanished_pid_removed_from_pid_policies(
        self, mock_monitor, mock_runner, wait_policy_factory,
    ) -> None:
        # If 888 vanishes, it must not be polled again in subsequent
        # iterations. After iteration 1, children = [999], so 888
        # should not appear in the remaining poll() calls.
        mock_monitor.get_child_pids.side_effect = [
            [999, 888],
            [999],
            [999],
        ]
        mock_runner.run.return_value = iter([None, None, None])
        loop = MonitorLoop(mock_monitor, mock_runner, wait_policy_factory, rate=0.01)

        loop.run()

        poll_pids = [c.args[0] for c in mock_monitor.poll.call_args_list]
        # 888 appears only in iteration 0.
        assert poll_pids == [12345, 999, 888, 12345, 999, 12345, 999]

    def test_vanished_pid_re_emits_died_on_real_monitor(
        self, exporter, mock_runner, wait_policy_factory, process, stats,
    ) -> None:
        """End-to-end: a real ``EventsMonitor`` driving the real
        ``mark_pid_died`` produces the right lifecycle events when the
        ``MonitorLoop`` notices the vanished child."""
        monitor = EventsMonitor(process, exporter, stats)
        # Iteration 0: parent + child; iteration 1: parent only.
        children_iter = iter([[777], []])

        def _child_pids() -> list[int]:
            return list(next(children_iter, []))

        monitor.get_child_pids = _child_pids  # type: ignore[method-assign]

        with patch("gcmon.monitor.get_gc_stats", return_value=[]):
            runner = Mock(spec=Runner)
            runner.run.return_value = iter([None, None])
            loop = MonitorLoop(monitor, runner, wait_policy_factory, rate=0.01)
            loop.run()

        # pid 12345 (parent) and 777 (child) got STARTED, then 777 got
        # DIED via the vanished-pid path. The parent never got DIED
        # because it was always in the children list and never returned
        # INVALID_PROCESS.
        pids_kinds = [(pid, kind) for pid, kind, _ in exporter.lifecycle_events]
        assert pids_kinds == [
            (12345, ProcessLifecycle.STARTED),
            (777, ProcessLifecycle.STARTED),
            (777, ProcessLifecycle.DIED),
        ]


class TestMonitorLoopContextManager:
    def test_enter_returns_self(self, mock_monitor):
        loop = MonitorLoop(mock_monitor, Mock(spec=Runner), lambda: Mock(spec=WaitPolicy))
        with loop as l:
            assert l is loop

    def test_exit_calls_close(self, mock_monitor):
        loop = MonitorLoop(mock_monitor, Mock(spec=Runner), lambda: Mock(spec=WaitPolicy))
        assert not loop._stop_event.is_set()
        with loop:
            pass
        assert loop._stop_event.is_set()
