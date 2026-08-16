"""The loop, which is timing and shutdown and nothing else.

One tick is one call on the monitor, which answers a `PollReport`. What is
left to test here is what the loop does with that report and with the clock:
who it hands the live set to, when it breaks, and that a stop reaches the
monitor mid-tick. Everything behind the report -- discovery, the prune, the
policies, the enable check -- is the monitor's, and is tested at
`test_monitor.py` where it lives.
"""

import threading
import time
from unittest.mock import MagicMock, Mock, patch

import pytest

from gcmon.monitor import PollReport
from gcmon.monitor_loop import MonitorLoop
from gcmon.rss_sampler import RssSampler
from gcmon.run_policy import InfinityRunner, Runner


def _report(*live: int, keep_running: bool = True) -> PollReport:
    return PollReport(live_pids=frozenset(live), keep_running=keep_running)


@pytest.fixture
def mock_monitor() -> MagicMock:
    monitor = MagicMock()
    monitor.pid = 12345
    monitor.tick.return_value = _report(12345)
    monitor.__enter__.return_value = monitor
    monitor.__exit__.return_value = None
    return monitor


@pytest.fixture
def mock_runner() -> Mock:
    runner = Mock(spec=Runner)
    runner.run.return_value = iter([None])
    return runner


def _runner(ticks: int) -> Mock:
    runner = Mock(spec=Runner)
    runner.run.return_value = iter([None] * ticks)
    return runner


@pytest.fixture
def loop(mock_monitor: MagicMock, mock_runner: Mock) -> MonitorLoop:
    return MonitorLoop(mock_monitor, mock_runner, rate=0.01)


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
    def test_ticks_the_monitor(self, loop: MonitorLoop, mock_monitor: MagicMock) -> None:
        loop.run()

        mock_monitor.tick.assert_called_once()

    def test_ticks_once_per_iteration(self, mock_monitor: MagicMock) -> None:
        loop = MonitorLoop(mock_monitor, _runner(3), rate=0.01)

        loop.run()

        assert mock_monitor.tick.call_count == 3

    def test_stop_before_run_skips_the_loop(self, mock_monitor: MagicMock) -> None:
        runner = Mock(spec=Runner)
        runner.run.return_value = iter([])
        loop = MonitorLoop(mock_monitor, runner, rate=0.01)
        loop._stop_event.set()

        loop.run()

        mock_monitor.tick.assert_not_called()

    def test_breaks_when_the_report_says_to_stop(self, mock_monitor: MagicMock) -> None:
        """The wait policies live in the monitor; `keep_running` is their
        verdict arriving as one answer."""
        mock_monitor.tick.return_value = _report(12345, keep_running=False)
        loop = MonitorLoop(mock_monitor, _runner(3), rate=0.01)

        loop.run()

        mock_monitor.tick.assert_called_once()

    def test_the_stop_event_reaches_the_monitor(self, loop: MonitorLoop, mock_monitor: MagicMock) -> None:
        """A tick polls a whole process tree, so shutdown has to be able to
        interrupt one. The loop owns the event and lends the monitor a read of
        it -- nothing more."""
        loop.run()

        _now_ns, stop = mock_monitor.tick.call_args[0]
        assert stop == loop._stop_event.is_set

    def test_close_during_run(self, mock_monitor: MagicMock) -> None:
        loop = MonitorLoop(mock_monitor, InfinityRunner(), rate=0.01)

        t = threading.Thread(target=loop.run, daemon=True)
        t.start()
        time.sleep(0.05)
        loop.close()
        t.join(timeout=2)

        assert mock_monitor.tick.called
        assert loop._stop_event.is_set()

    def test_stop_event_set_after_normal_exit(self, loop: MonitorLoop) -> None:
        assert not loop._stop_event.is_set()
        loop.run()
        assert loop._stop_event.is_set()


class TestMonitorLoopContextManager:
    def test_exit_calls_close(self, mock_monitor: MagicMock) -> None:
        loop = MonitorLoop(mock_monitor, Mock(spec=Runner))
        assert not loop._stop_event.is_set()
        with loop:
            pass
        assert loop._stop_event.is_set()


class TestRssSamplerInLoop:
    """The sampler is driven once per tick, off the live set the report
    carried and the instant the tick was stamped with (ADR-0013)."""

    def test_tick_called_with_the_live_set(self, mock_monitor: MagicMock) -> None:
        mock_monitor.tick.return_value = _report(12345, 999)
        rss_sampler = Mock(spec=RssSampler)

        MonitorLoop(mock_monitor, _runner(1), rate=0.01, rss_sampler=rss_sampler).run()

        rss_sampler.tick.assert_called_once()
        _now, live_pids = rss_sampler.tick.call_args[0]
        assert live_pids == frozenset({12345, 999})

    def test_no_sampler_is_not_an_error(self, mock_monitor: MagicMock) -> None:
        MonitorLoop(mock_monitor, _runner(1), rate=0.01).run()

        # No error -- tick is simply not called.

    def test_tick_receives_monotonic_now(self, mock_monitor: MagicMock) -> None:
        """``tick`` still takes seconds, per ADR-0013, but the loop derives
        them from the one nanosecond read it takes per tick."""
        rss_sampler = Mock(spec=RssSampler)

        with patch("time.monotonic_ns", return_value=42_000_000_000):
            MonitorLoop(mock_monitor, _runner(1), rate=0.01, rss_sampler=rss_sampler).run()

        rss_sampler.tick.assert_called_once_with(42.0, frozenset({12345}))

    def test_tick_called_each_iteration(self, mock_monitor: MagicMock) -> None:
        rss_sampler = Mock(spec=RssSampler)

        MonitorLoop(mock_monitor, _runner(3), rate=0.01, rss_sampler=rss_sampler).run()

        assert rss_sampler.tick.call_count == 3

    def test_tick_called_even_with_nothing_live(self, mock_monitor: MagicMock) -> None:
        """Unlike liveness, the sampler is called on an empty set and decides
        for itself -- its own interval bookkeeping runs either way."""
        mock_monitor.tick.return_value = _report()
        rss_sampler = Mock(spec=RssSampler)

        MonitorLoop(mock_monitor, _runner(1), rate=0.01, rss_sampler=rss_sampler).run()

        rss_sampler.tick.assert_called_once()
        _now, live_pids = rss_sampler.tick.call_args[0]
        assert live_pids == frozenset()


class TestTheTickInstant:
    """One clock read per tick, shared by everything that needs it.

    The tick stamps its own liveness with the instant it is given (ADR-0011)
    and the sampler paces off the same one in seconds (ADR-0013), so a sample
    and a liveness observation from one tick agree.
    """

    def test_the_tick_is_given_the_instant(self, mock_monitor: MagicMock) -> None:
        with patch("time.monotonic_ns", return_value=42_000_000_000):
            MonitorLoop(mock_monitor, _runner(1), rate=0.01).run()

        now_ns, _stop = mock_monitor.tick.call_args[0]
        assert now_ns == 42_000_000_000

    def test_one_clock_read_per_tick_shared_with_the_sampler(self, mock_monitor: MagicMock) -> None:
        rss_sampler = Mock(spec=RssSampler)

        with patch("time.monotonic_ns", side_effect=[1_500_000_000, 2_500_000_000]) as monotonic_ns:
            MonitorLoop(mock_monitor, _runner(2), rate=0.01, rss_sampler=rss_sampler).run()

        assert monotonic_ns.call_count == 2, "one clock read per tick"
        tick_ns = [c[0][0] for c in mock_monitor.tick.call_args_list]
        sampler_now = [c[0][0] for c in rss_sampler.tick.call_args_list]
        assert tick_ns == [1_500_000_000, 2_500_000_000]
        assert sampler_now == [1.5, 2.5]

    def test_the_sampler_gets_the_same_instant_in_seconds(self, mock_monitor: MagicMock) -> None:
        rss_sampler = Mock(spec=RssSampler)

        with patch("time.monotonic_ns", return_value=1_500_000_000):
            MonitorLoop(mock_monitor, _runner(1), rate=0.01, rss_sampler=rss_sampler).run()

        now_ns, _stop = mock_monitor.tick.call_args[0]
        sampler_now, _pids = rss_sampler.tick.call_args[0]
        assert sampler_now == now_ns / 1e9


class TestTheLoopDoesNotTouchTheExporter:
    def test_it_never_reaches_through_the_monitor(self, mock_monitor: MagicMock) -> None:
        """`EventsMonitor.exporter` existed for the loop's liveness call and
        nothing else. The call is inside the tick now, and the property is
        gone -- so the only code emitting to the exporter is the code that
        owns what it emits."""
        MonitorLoop(mock_monitor, _runner(2), rate=0.01).run()

        assert "exporter" not in mock_monitor._mock_children


class TestTheLoopHoldsNoPerPidState:
    """Spec 0038's structural claim, stated where it can regress.

    A second home for any of this is a second prune waiting to be written, and
    the two disagreeing is what fabricates a loss window.
    """

    @pytest.mark.parametrize("attribute", ["_wait_policy_factory", "_enabled", "_pid_policies"])
    def test_the_attribute_is_gone(self, loop: MonitorLoop, attribute: str) -> None:
        assert not hasattr(loop, attribute)

    def test_the_constructor_takes_neither_policies_nor_an_enable_predicate(self) -> None:
        import inspect

        parameters = set(inspect.signature(MonitorLoop.__init__).parameters)

        assert parameters == {"self", "monitor", "runner", "rate", "rss_sampler"}


class TestADeadTargetDoesNotExtendTheRun:
    def test_the_loop_stops_when_every_policy_gives_up(self) -> None:
        """The regression a policy deletion once caused, now expressed against
        the report: a target that dies while a child is still alive must not
        keep the loop polling until a fresh startup timeout expires."""
        monitor = MagicMock()
        monitor.pid = 12345
        monitor.tick.side_effect = [
            _report(12345, 999),
            _report(999),
            _report(keep_running=False),
        ]

        loop = MonitorLoop(monitor, _runner(50), rate=0.001)
        loop.run()

        assert monitor.tick.call_count == 3, "the loop kept ticking a finished run"
