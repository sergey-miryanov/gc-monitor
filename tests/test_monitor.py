"""Tests for GCMonitor."""

import logging
from collections.abc import Callable, Generator
from unittest.mock import MagicMock, patch

import pytest

from gcmon.events_reader import TargetUnavailable
from gcmon.monitor import EventsMonitor
from gcmon.poll_status import PollStatus
from gcmon.protocol import TGCStatsInfo
from gcmon.stats import StreamingStats
from tests.helpers import FakeEventsReader, MockExporter, create_mock_stats_item

NO_RECORDS: list[TGCStatsInfo] = []

# =============================================================================
# Local fixtures
# =============================================================================


@pytest.fixture
def mock_read(reader: FakeEventsReader) -> MagicMock:
    """What the injected reader answers, with ``Mock``'s semantics.

    Set ``return_value`` for a fixed answer, or ``side_effect`` for a scripted
    sequence or a failure. Raise :class:`TargetUnavailable` to play a target
    gcmon cannot read: the platform's own exception types stop at the reader, so
    a double impersonating them would be testing the wrong seam.
    """
    mock = MagicMock(name="read")
    reader.reads = mock
    return mock


@pytest.fixture
def mock_monotonic() -> Generator[MagicMock]:
    """Patch the clock used to measure read time, in nanoseconds."""
    with patch("gcmon.monitor.time.monotonic_ns") as mock:
        yield mock


# =============================================================================
# GCMonitor tests
# =============================================================================


class TestGCMonitor:
    def test_init(self, monitor: EventsMonitor) -> None:
        assert monitor.is_enabled
        assert monitor.pid == 12345

    def test_poll(self, exporter: MockExporter, monitor: EventsMonitor, mock_read: MagicMock) -> None:
        item = create_mock_stats_item(ts_start=1_000_000_000, ts_stop=1_005_000_000)

        mock_read.return_value = [item]
        result = monitor.poll(12345)

        assert result == PollStatus.OK
        assert len(exporter.events) == 1

    def test_poll_duplicate_records(self, exporter: MockExporter, monitor: EventsMonitor, mock_read: MagicMock) -> None:
        """The monitor identifies a record by `collections`, so two slots
        reporting the same value are one collection seen twice."""
        item1 = create_mock_stats_item(collections=50, ts_start=1_000_000_000, ts_stop=1_005_000_000)
        item2 = create_mock_stats_item(collections=50, ts_start=1_000_000_000, ts_stop=1_006_000_000)
        item3 = create_mock_stats_item(collections=51, ts_start=2_000_000_000, ts_stop=2_005_000_000)

        mock_read.return_value = [item1, item2, item3]
        monitor.poll(12345)

        assert len(exporter.events) == 2
        assert exporter.events[0].ts_start == 1_000_000_000
        assert exporter.events[1].ts_start == 2_000_000_000

    def test_poll_unreadable_target(self, monitor: EventsMonitor, mock_read: MagicMock) -> None:
        """One case, not five. Which CPython failures mean "unreadable" is the
        reader's question and `tests/test_events_reader.py` asks it; all the
        monitor decides is what an unreadable target does to a poll."""
        mock_read.side_effect = TargetUnavailable("PID 12345 is not readable: gone")

        assert monitor.poll(12345) == PollStatus.INVALID_PROCESS

    def test_poll_of_a_departed_target_writes_no_warning(
        self, monitor: EventsMonitor, mock_read: MagicMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A target exiting is the ordinary end of a run, so it stays at debug
        level. This is the regression the swap to ``GCMonitor`` most invites: a
        traceback on stderr every time a monitored process finishes."""
        mock_read.side_effect = TargetUnavailable("PID 12345 is not readable: [Errno 3] No such process")

        with caplog.at_level(logging.DEBUG, logger="gcmon"):
            assert monitor.poll(12345) == PollStatus.INVALID_PROCESS

        assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []
        assert any("12345" in r.getMessage() for r in caplog.records if r.levelno == logging.DEBUG)

    def test_poll_general_exception(self, monitor: EventsMonitor, mock_read: MagicMock) -> None:
        mock_read.side_effect = ValueError("Unexpected error")
        result = monitor.poll(12345)

        assert result == PollStatus.FAIL

    def test_poll_general_exception_is_warned_with_a_traceback(
        self, monitor: EventsMonitor, mock_read: MagicMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The other half of the case above: a failure gcmon does not recognise
        must be loud, or the two arms could be swapped without a test noticing.
        """
        mock_read.side_effect = ValueError("Unexpected error")

        assert monitor.poll(12345) == PollStatus.FAIL

        warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert len(warnings) == 1
        assert warnings[0].exc_info is not None

    def test_poll_after_stop(self, monitor: EventsMonitor) -> None:
        monitor.stop()
        assert monitor.poll(12345) == PollStatus.FAIL

    def test_stop(self, exporter: MockExporter, monitor: EventsMonitor) -> None:
        monitor.stop()
        assert not monitor.is_enabled
        assert exporter._close_called

    def test_stop_idempotent(self, monitor: EventsMonitor) -> None:
        monitor.stop()
        monitor.stop()
        assert not monitor.is_enabled

    def test_stop_lets_go_of_every_attachment(
        self, monitor: EventsMonitor, mock_read: MagicMock, reader: FakeEventsReader
    ) -> None:
        """An attachment is a handle on somebody else's process -- on Windows it
        holds the pid reserved -- so a monitor that has stopped must not still be
        holding one. Leaving it to garbage collection is not the same thing."""
        mock_read.return_value = NO_RECORDS
        monitor.poll(12345)
        monitor.poll(999)
        assert reader.attached == {12345, 999}

        monitor.stop()

        assert reader.attached == set()

    def test_stopping_twice_still_holds_nothing(
        self, monitor: EventsMonitor, mock_read: MagicMock, reader: FakeEventsReader
    ) -> None:
        mock_read.return_value = NO_RECORDS
        monitor.poll(12345)

        monitor.stop()
        monitor.stop()

        assert reader.attached == set()


class TestGCMonitorReadTime:
    """Tests for read time tracking around the reader."""

    def test_poll_records_read_time(
        self, monitor: EventsMonitor, stats: StreamingStats, mock_read: MagicMock, mock_monotonic: MagicMock
    ) -> None:
        mock_monotonic.side_effect = [1_000_000_000, 1_002_500_000]
        mock_read.return_value = [create_mock_stats_item()]

        assert monitor.poll(12345) == PollStatus.OK

        # 2.5 ms between the two monotonic_ns readings, stored as nanoseconds
        assert stats.read_time.count() == 1
        assert stats.read_time.sum() == 2_500_000

    def test_poll_records_read_time_without_events(
        self, monitor: EventsMonitor, stats: StreamingStats, mock_read: MagicMock
    ) -> None:
        mock_read.return_value = NO_RECORDS

        assert monitor.poll(12345) == PollStatus.OK

        assert stats.count() == 0
        assert stats.read_time.count() == 1

    def test_poll_keeps_sub_microsecond_read_time(
        self, monitor: EventsMonitor, stats: StreamingStats, mock_read: MagicMock, mock_monotonic: MagicMock
    ) -> None:
        mock_monotonic.side_effect = [1_000_000_000, 1_000_000_750]
        mock_read.return_value = NO_RECORDS

        assert monitor.poll(12345) == PollStatus.OK

        assert stats.read_time.sum() == 750

    def test_read_time_accumulates_over_polls(
        self, monitor: EventsMonitor, stats: StreamingStats, mock_read: MagicMock, mock_monotonic: MagicMock
    ) -> None:
        mock_monotonic.side_effect = [0, 1_000_000, 5_000_000, 8_000_000]
        mock_read.return_value = NO_RECORDS

        monitor.poll(12345)
        monitor.poll(12345)

        assert stats.read_time.count() == 2
        assert stats.read_time.sum() == 4_000_000
        assert stats.read_time.average() == 2_000_000

    def test_read_time_shared_across_pids(
        self,
        make_monitor: Callable[..., EventsMonitor],
        stats: StreamingStats,
        mock_read: MagicMock,
    ) -> None:
        mock_read.return_value = NO_RECORDS

        make_monitor(pid=111).poll(111)
        make_monitor(pid=222).poll(222)

        assert stats.read_time.count() == 2

    def test_read_time_not_recorded_on_failed_read(
        self, monitor: EventsMonitor, stats: StreamingStats, mock_read: MagicMock
    ) -> None:
        mock_read.side_effect = TargetUnavailable("PID 12345 is not readable: not started yet")

        assert monitor.poll(12345) == PollStatus.INVALID_PROCESS

        assert stats.read_time.count() == 0

    def test_read_time_not_recorded_after_stop(
        self, monitor: EventsMonitor, stats: StreamingStats, mock_read: MagicMock
    ) -> None:
        mock_read.return_value = NO_RECORDS
        monitor.stop()

        assert monitor.poll(12345) == PollStatus.FAIL

        assert stats.read_time.count() == 0
        mock_read.assert_not_called()
