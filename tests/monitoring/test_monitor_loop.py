import itertools
import threading
import time
from collections.abc import Callable, Generator
from unittest.mock import MagicMock, Mock, call, patch

import pytest

from gcmon.monitor_loop import MonitorLoop
from gcmon.poll_status import PollStatus
from gcmon.rss_sampler import RssSampler
from gcmon.run_policy import InfinityRunner, Runner
from gcmon.wait_policy import StartupTimeoutPolicy, WaitPolicy


@pytest.fixture
def mock_monitor() -> MagicMock:
    monitor = MagicMock()
    monitor.pid = 12345
    monitor.get_child_pids.return_value = list[int]()
    monitor.poll.return_value = PollStatus.OK
    monitor.__enter__.return_value = monitor
    monitor.__exit__.return_value = None
    return monitor


@pytest.fixture
def mock_runner() -> Mock:
    runner = Mock(spec=Runner)
    runner.run.return_value = iter([None])
    return runner


@pytest.fixture
def mock_wait_policy() -> Mock:
    policy = Mock(spec=WaitPolicy)
    policy.wait.return_value = True
    return policy


@pytest.fixture
def wait_policy_factory(mock_wait_policy: Mock) -> Callable[[], Mock]:
    return lambda: mock_wait_policy


@pytest.fixture
def loop(mock_monitor: MagicMock, mock_runner: Mock, wait_policy_factory: Callable[[], Mock]) -> MonitorLoop:
    return MonitorLoop(mock_monitor, mock_runner, wait_policy_factory, rate=0.01)


class TestMonitorLoopInit:
    def test_close_sets_stop_event(self, loop: MonitorLoop) -> None:
        assert not loop._stop_event.is_set()
        loop.close()
        assert loop._stop_event.is_set()

    def test_close_idempotent(self, loop: MonitorLoop) -> None:
        loop.close()
        loop.close()
        assert loop._stop_event.is_set()


class TestMonitorLoopRun:
    def test_polls_pid(self, loop: MonitorLoop, mock_monitor: MagicMock, mock_wait_policy: Mock) -> None:
        loop.run()

        mock_monitor.poll.assert_called_once_with(12345)
        mock_wait_policy.wait.assert_called_once_with(PollStatus.OK)

    def test_polls_child_pids(self, loop: MonitorLoop, mock_monitor: MagicMock) -> None:
        mock_monitor.get_child_pids.return_value = [999, 888]
        mock_monitor.poll.reset_mock()

        loop.run()

        assert mock_monitor.poll.call_args_list == [
            call(12345),
            call(999),
            call(888),
        ]

    def test_stop_before_run_skips_loop(self, mock_monitor: MagicMock) -> None:
        runner = Mock(spec=Runner)
        runner.run.return_value = iter([])
        loop = MonitorLoop(mock_monitor, runner, lambda: Mock(spec=WaitPolicy), rate=0.01)
        loop._stop_event.set()

        loop.run()

        mock_monitor.poll.assert_not_called()

    def test_break_when_wait_returns_false(self, loop: MonitorLoop, mock_wait_policy: Mock) -> None:
        mock_wait_policy.wait.return_value = False

        loop.run()

        mock_wait_policy.wait.assert_called_once()

    def test_continues_looping(self, loop: MonitorLoop, mock_runner: Mock, mock_wait_policy: Mock) -> None:
        mock_runner.run.return_value = iter([None, None, None])

        loop.run()

        assert mock_wait_policy.wait.call_count == 3

    def test_close_during_run(self, mock_monitor: MagicMock, wait_policy_factory: Callable[[], Mock]) -> None:
        loop = MonitorLoop(mock_monitor, InfinityRunner(), wait_policy_factory, rate=0.01)

        t = threading.Thread(target=loop.run, daemon=True)
        t.start()
        time.sleep(0.05)
        loop.close()
        t.join(timeout=2)

        assert mock_monitor.poll.called
        assert loop._stop_event.is_set()

    def test_stop_event_set_after_normal_exit(self, loop: MonitorLoop) -> None:
        assert not loop._stop_event.is_set()
        loop.run()
        assert loop._stop_event.is_set()


class TestMonitorLoopContextManager:
    def test_exit_calls_close(self, mock_monitor: MagicMock) -> None:
        loop = MonitorLoop(mock_monitor, Mock(spec=Runner), lambda: Mock(spec=WaitPolicy))
        assert not loop._stop_event.is_set()
        with loop:
            pass
        assert loop._stop_event.is_set()


class TestLivePidsTracking:
    """Only PIDs that returned ``PollStatus.OK`` are passed to ``tick()``."""

    def test_all_ok_pids_tracked(self, mock_monitor: MagicMock) -> None:
        mock_monitor.get_child_pids.return_value = [999, 888]
        mock_monitor.poll.side_effect = [PollStatus.OK, PollStatus.OK, PollStatus.OK]
        rss_sampler = Mock(spec=RssSampler)
        runner = Mock(spec=Runner)
        runner.run.return_value = iter([None])
        loop = MonitorLoop(mock_monitor, runner, lambda: Mock(spec=WaitPolicy), rate=0.01, rss_sampler=rss_sampler)

        loop.run()

        rss_sampler.tick.assert_called_once()
        _now, live_pids = rss_sampler.tick.call_args[0]
        assert live_pids == {12345, 999, 888}

    def test_failing_pids_excluded(self, mock_monitor: MagicMock) -> None:
        mock_monitor.get_child_pids.return_value = [999]
        mock_monitor.poll.side_effect = [PollStatus.OK, PollStatus.FAIL]
        rss_sampler = Mock(spec=RssSampler)
        runner = Mock(spec=Runner)
        runner.run.return_value = iter([None])

        loop = MonitorLoop(mock_monitor, runner, lambda: Mock(spec=WaitPolicy), rate=0.01, rss_sampler=rss_sampler)
        loop.run()

        rss_sampler.tick.assert_called_once()
        _now, live_pids = rss_sampler.tick.call_args[0]
        assert live_pids == {12345}

    def test_invalid_pids_excluded(self, mock_monitor: MagicMock) -> None:
        mock_monitor.get_child_pids.return_value = [999]
        mock_monitor.poll.side_effect = [PollStatus.INVALID_PROCESS, PollStatus.OK]
        rss_sampler = Mock(spec=RssSampler)
        runner = Mock(spec=Runner)
        runner.run.return_value = iter([None])

        loop = MonitorLoop(mock_monitor, runner, lambda: Mock(spec=WaitPolicy), rate=0.01, rss_sampler=rss_sampler)
        loop.run()

        rss_sampler.tick.assert_called_once()
        _now, live_pids = rss_sampler.tick.call_args[0]
        assert live_pids == {999}

    def test_cleared_between_iterations(self, mock_monitor: MagicMock) -> None:
        mock_monitor.get_child_pids.side_effect = [[999], [888]]
        mock_monitor.poll.side_effect = [PollStatus.OK, PollStatus.OK, PollStatus.OK, PollStatus.OK]
        rss_sampler = Mock(spec=RssSampler)
        runner = Mock(spec=Runner)
        runner.run.return_value = iter([None, None])

        loop = MonitorLoop(mock_monitor, runner, lambda: Mock(spec=WaitPolicy), rate=0.01, rss_sampler=rss_sampler)
        loop.run()

        # After two iterations: first tick got {12345, 999}, second got {12345, 888}.
        # The set is local to each iteration, so stale PIDs don't leak across.
        assert rss_sampler.tick.call_count == 2
        first_live = rss_sampler.tick.call_args_list[0][0][1]
        second_live = rss_sampler.tick.call_args_list[1][0][1]
        assert first_live == {12345, 999}
        assert second_live == {12345, 888}


class TestForgettingDeadPids:
    """The wait policy decides when a PID is finished, and the loop drops the
    cursors when it does. A cursor tracks a counter owned by one process and
    means nothing for whatever the OS gives that PID to next."""

    def _policy(self, *verdicts: bool) -> Mock:
        return Mock(spec=WaitPolicy, **{"wait.side_effect": list(verdicts)})

    def _run(self, monitor: MagicMock, factory: Mock, ticks: int) -> None:
        runner = Mock(spec=Runner)
        runner.run.return_value = iter([None] * ticks)
        MonitorLoop(monitor, runner, factory, rate=0.01).run()

    def test_forgets_a_pid_the_policy_gives_up_on(self, mock_monitor: MagicMock) -> None:
        mock_monitor.get_child_pids.return_value = [999]
        mock_monitor.poll.side_effect = [
            PollStatus.OK,
            PollStatus.OK,  # tick 1: both answer
            PollStatus.OK,
            PollStatus.INVALID_PROCESS,  # tick 2: the child is gone
        ]
        factory = Mock(side_effect=[self._policy(True, True), self._policy(True, False)])

        self._run(mock_monitor, factory, ticks=2)

        mock_monitor.forget.assert_called_once_with(999)

    def test_keeps_state_while_the_policy_still_waits(self, mock_monitor: MagicMock) -> None:
        """A failed poll from a process that has yet to initialise is one the
        policy answers True to, and nothing is dropped on its account."""
        mock_monitor.get_child_pids.return_value = [999]
        mock_monitor.poll.side_effect = [
            PollStatus.OK,
            PollStatus.INVALID_PROCESS,
            PollStatus.OK,
            PollStatus.INVALID_PROCESS,
        ]
        factory = Mock(side_effect=[self._policy(True, True), self._policy(True, True)])

        self._run(mock_monitor, factory, ticks=2)

        mock_monitor.forget.assert_not_called()

    def test_a_dead_pid_keeps_its_policy(self, mock_monitor: MagicMock) -> None:
        """Only the cursors go. A replacement policy would not have seen the
        pid alive, so it would answer True to every further invalid poll and
        hold the loop open for a whole startup timeout."""
        mock_monitor.get_child_pids.return_value = [999]
        mock_monitor.poll.side_effect = [
            PollStatus.OK,
            PollStatus.OK,
            PollStatus.OK,
            PollStatus.INVALID_PROCESS,
            PollStatus.OK,
            PollStatus.INVALID_PROCESS,
        ]
        factory = Mock(side_effect=[self._policy(True, True, True), self._policy(True, False, False)])

        self._run(mock_monitor, factory, ticks=3)

        # One policy each for 12345 and 999, and none built to replace 999.
        assert factory.call_count == 2
        assert [c.args for c in mock_monitor.forget.call_args_list] == [(999,), (999,)]

    def test_forgets_pids_that_leave_the_process_tree(self, mock_monitor: MagicMock) -> None:
        """The path no wait policy can see: a child that exits between ticks
        is never polled again, so its absence from the tree is all there is."""
        mock_monitor.get_child_pids.side_effect = [[999], list[int]()]
        factory = Mock(side_effect=[self._policy(True, True), self._policy(True)])

        self._run(mock_monitor, factory, ticks=2)

        assert mock_monitor.retain.call_args_list == [call({12345, 999}), call({12345})]

    def test_a_failed_listing_prunes_nothing(self, mock_monitor: MagicMock) -> None:
        """get_child_pids reports None when it could not ask the OS. Treating
        that as an empty tree would drop the cursors of every live child and
        re-export their whole ring on the next poll."""
        mock_monitor.get_child_pids.side_effect = [[999], None]
        factory = Mock(side_effect=[self._policy(True, True), self._policy(True)])

        self._run(mock_monitor, factory, ticks=2)

        assert mock_monitor.retain.call_args_list == [call({12345, 999})]

    def test_a_returning_pid_gets_a_fresh_policy(self, mock_monitor: MagicMock) -> None:
        """Once a pid has left the tree, whatever comes back under it is a
        new process and starts its startup grace over."""
        mock_monitor.get_child_pids.side_effect = [[999], list[int](), [999]]
        factory = Mock(side_effect=[self._policy(True, True, True), self._policy(True), self._policy(True)])

        self._run(mock_monitor, factory, ticks=3)

        # 12345 once, then 999 twice: once on first sight, once on return.
        assert factory.call_count == 3

    def test_a_dead_target_does_not_extend_the_run(self) -> None:
        """The regression the policy deletion caused, against the real policy:
        a target that dies while a child is still alive used to keep the loop
        polling until a fresh startup timeout expired."""
        monitor = MagicMock()
        monitor.pid = 12345
        monitor.get_child_pids.return_value = [999]
        ticks = itertools.count()
        state = {"tick": 0}

        def poll(pid: int) -> PollStatus:
            if state["tick"] == 0:
                return PollStatus.OK
            if pid == 12345:
                return PollStatus.INVALID_PROCESS
            return PollStatus.OK if state["tick"] == 1 else PollStatus.INVALID_PROCESS

        monitor.poll.side_effect = poll

        class CountingRunner:
            def run(self, stop: Callable[[], bool]) -> Generator[None]:
                while not stop() and next(ticks) < 50:
                    yield None
                    state["tick"] += 1

        loop = MonitorLoop(monitor, CountingRunner(), lambda: StartupTimeoutPolicy(2), rate=0.001)
        loop.run()

        assert state["tick"] == 2, "the loop kept polling a dead target"


class TestRssSamplerInLoop:
    """RSS sampler is called with correct live PIDs and timestamp."""

    def test_tick_called_with_live_pids(self, mock_monitor: MagicMock) -> None:
        mock_monitor.get_child_pids.return_value = [999]
        mock_monitor.poll.side_effect = [PollStatus.OK, PollStatus.OK]
        rss_sampler = Mock(spec=RssSampler)
        runner = Mock(spec=Runner)
        runner.run.return_value = iter([None])
        loop = MonitorLoop(
            mock_monitor,
            runner,
            lambda: Mock(spec=WaitPolicy),
            rate=0.01,
            rss_sampler=rss_sampler,
        )

        loop.run()

        rss_sampler.tick.assert_called_once()
        _args, _kwargs = rss_sampler.tick.call_args
        _now, live_pids = _args
        assert live_pids == {12345, 999}

    def test_tick_not_called_when_no_sampler(self, mock_monitor: MagicMock) -> None:
        runner = Mock(spec=Runner)
        runner.run.return_value = iter([None])
        loop = MonitorLoop(
            mock_monitor,
            runner,
            lambda: Mock(spec=WaitPolicy),
            rate=0.01,
        )

        loop.run()

        # No error — tick is simply not called.

    def test_tick_receives_monotonic_now(self, mock_monitor: MagicMock) -> None:
        """``tick`` still takes seconds, per ADR-0013, but the loop now
        derives them from the one nanosecond read it takes per tick."""
        mock_monitor.poll.return_value = PollStatus.OK
        rss_sampler = Mock(spec=RssSampler)
        runner = Mock(spec=Runner)
        runner.run.return_value = iter([None])
        loop = MonitorLoop(
            mock_monitor,
            runner,
            lambda: Mock(spec=WaitPolicy),
            rate=0.01,
            rss_sampler=rss_sampler,
        )

        with patch("time.monotonic_ns", return_value=42_000_000_000):
            loop.run()

        rss_sampler.tick.assert_called_once_with(42.0, {12345})

    def test_tick_called_each_iteration(self, mock_monitor: MagicMock) -> None:
        mock_monitor.poll.return_value = PollStatus.OK
        rss_sampler = Mock(spec=RssSampler)
        runner = Mock(spec=Runner)
        runner.run.return_value = iter([None, None, None])

        loop = MonitorLoop(
            mock_monitor,
            runner,
            lambda: Mock(spec=WaitPolicy),
            rate=0.01,
            rss_sampler=rss_sampler,
        )

        loop.run()

        assert rss_sampler.tick.call_count == 3

    def test_tick_skipped_when_no_live_pids(self, mock_monitor: MagicMock) -> None:
        mock_monitor.poll.return_value = PollStatus.FAIL
        rss_sampler = Mock(spec=RssSampler)
        runner = Mock(spec=Runner)
        runner.run.return_value = iter([None])
        loop = MonitorLoop(
            mock_monitor,
            runner,
            lambda: Mock(spec=WaitPolicy),
            rate=0.01,
            rss_sampler=rss_sampler,
        )

        loop.run()

        rss_sampler.tick.assert_called_once()
        _args, _kwargs = rss_sampler.tick.call_args
        _now, live_pids = _args
        assert live_pids == set()


class TestProcessLiveness:
    """Every tick reports the pids that answered to the exporter, which
    is the only evidence a process gcmon never saw collect existed at
    all. See ADR-0011."""

    def _loop(
        self,
        mock_monitor: MagicMock,
        ticks: int = 1,
        enabled: Callable[[int], bool] | None = None,
        rss_sampler: Mock | None = None,
    ) -> MonitorLoop:
        runner = Mock(spec=Runner)
        runner.run.return_value = iter([None] * ticks)
        return MonitorLoop(
            mock_monitor,
            runner,
            lambda: Mock(spec=WaitPolicy),
            rate=0.01,
            enabled=enabled,
            rss_sampler=rss_sampler,
        )

    def test_reported_once_per_tick_with_the_whole_live_set(self, mock_monitor: MagicMock) -> None:
        """One call and one lock acquisition per tick, not per pid."""
        mock_monitor.get_child_pids.return_value = [999, 888]
        mock_monitor.poll.return_value = PollStatus.OK
        liveness = mock_monitor.exporter.add_process_liveness

        self._loop(mock_monitor).run()

        liveness.assert_called_once()
        pids, _ts_ns = liveness.call_args[0]
        assert pids == {12345, 999, 888}

    def test_reported_every_tick(self, mock_monitor: MagicMock) -> None:
        mock_monitor.get_child_pids.side_effect = [[999], [888]]
        mock_monitor.poll.return_value = PollStatus.OK

        self._loop(mock_monitor, ticks=2).run()

        assert [c[0][0] for c in mock_monitor.exporter.add_process_liveness.call_args_list] == [
            {12345, 999},
            {12345, 888},
        ]

    def test_failing_pid_is_not_live(self, mock_monitor: MagicMock) -> None:
        """A pid that could not be read is not evidence of anything.
        Only ``PollStatus.OK`` is."""
        mock_monitor.get_child_pids.return_value = [999, 888]
        mock_monitor.poll.side_effect = [PollStatus.OK, PollStatus.FAIL, PollStatus.INVALID_PROCESS]

        self._loop(mock_monitor).run()

        pids, _ts_ns = mock_monitor.exporter.add_process_liveness.call_args[0]
        assert pids == {12345}

    def test_suppressed_pid_is_not_live(self, mock_monitor: MagicMock) -> None:
        """A pid the control server disabled is never polled, so it is
        never observed. Its span keeps whatever it had."""
        mock_monitor.get_child_pids.return_value = [999]
        mock_monitor.poll.return_value = PollStatus.OK

        self._loop(mock_monitor, enabled=lambda pid: pid != 999).run()

        pids, _ts_ns = mock_monitor.exporter.add_process_liveness.call_args[0]
        assert pids == {12345}

    def test_not_reported_when_nothing_answered(self, mock_monitor: MagicMock) -> None:
        """An empty set would widen no span, so the call is skipped
        rather than made with nothing in it."""
        mock_monitor.poll.return_value = PollStatus.FAIL

        self._loop(mock_monitor).run()

        mock_monitor.exporter.add_process_liveness.assert_not_called()

    def test_reported_with_the_tick_timestamp_in_nanoseconds(self, mock_monitor: MagicMock) -> None:
        mock_monitor.poll.return_value = PollStatus.OK

        with patch("time.monotonic_ns", return_value=42_000_000_000):
            self._loop(mock_monitor).run()

        mock_monitor.exporter.add_process_liveness.assert_called_once_with({12345}, 42_000_000_000)

    def test_one_clock_read_per_tick_shared_with_the_sampler(self, mock_monitor: MagicMock) -> None:
        """The loop reads the clock once and hands the same instant to
        both phases: nanoseconds to liveness, seconds to the sampler."""
        mock_monitor.poll.return_value = PollStatus.OK
        rss_sampler = Mock(spec=RssSampler)

        with patch("time.monotonic_ns", side_effect=[1_500_000_000, 2_500_000_000]) as monotonic_ns:
            self._loop(mock_monitor, ticks=2, rss_sampler=rss_sampler).run()

        assert monotonic_ns.call_count == 2, "one clock read per tick"
        liveness_ts = [c[0][1] for c in mock_monitor.exporter.add_process_liveness.call_args_list]
        sampler_now = [c[0][0] for c in rss_sampler.tick.call_args_list]
        assert liveness_ts == [1_500_000_000, 2_500_000_000]
        assert sampler_now == [1.5, 2.5]
