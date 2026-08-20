"""Tests for Chrome trace exporter."""

from collections.abc import Callable
from pathlib import Path

import pytest

from gcmon.data import GCStatsInfo, ts_to_us
from gcmon.events_reader import TargetUnavailable
from gcmon.exporters import TraceExporter
from gcmon.monitor import EventsMonitor
from gcmon.stats import StreamingStats
from gcmon.target_process import ExternalProcess
from gcmon.trace_event import loss_tid
from gcmon.wait_policy import no_wait_policy
from tests.conftest import DEFAULT_PID
from tests.data_helpers import create_instant_msg
from tests.exporters.conftest import ExporterFactory
from tests.helpers import (
    ChromeTraceValue,
    FakeEventsReader,
    assert_is_begin,
    assert_is_counter,
    assert_is_instant_event,
    assert_is_process_meta,
    assert_is_thread_meta,
    assert_valid_chrome_trace_format,
    create_mock_stats_item,
)


class TestTraceExporter:
    def test_init(self, trace_exporter: ExporterFactory) -> None:
        exporter, path = trace_exporter()
        assert isinstance(exporter, TraceExporter)
        assert exporter._flush_threshold == 100
        assert exporter._buffer == []
        assert exporter._output_path == path

    def test_init_with_flush_threshold(self, trace_exporter: ExporterFactory) -> None:
        exporter, path = trace_exporter(threshold=500)
        assert isinstance(exporter, TraceExporter)
        assert exporter._flush_threshold == 500
        assert exporter._buffer == []
        assert exporter._output_path == path

    def _verify_events(self, data: list[dict[str, ChromeTraceValue]], num_items: int) -> None:
        begins = [e for e in data if e["ph"] == "B"]
        counters = [e for e in data if e["ph"] == "C"]
        metas = [e for e in data if e["ph"] == "M"]
        assert len(begins) == num_items
        # per-gen counter (with duration folded in) + shared heap_size
        assert len(counters) == 2 * num_items
        assert all(e["name"] == "GC Pause(0)" for e in begins)
        per_gen_counters = [e for e in counters if e["name"] == "G0"]
        # The shared heap_size counter: the JSON encoder rewrites its event
        # name to "" so the trace processor produces a single track named
        # " heap_size" instead of "heap_size heap_size".
        shared_counters = [e for e in counters if e["name"] == ""]
        assert len(per_gen_counters) == num_items
        assert len(shared_counters) == num_items
        heap_counters = [
            c for c in shared_counters if isinstance(c["args"], dict) and set(c["args"].keys()) == {"heap_size"}
        ]
        assert len(heap_counters) == num_items
        # The per-gen counter now includes `duration` alongside the other
        # per-gen metrics.
        for c in per_gen_counters:
            assert isinstance(c["args"], dict) and "duration" in c["args"]
        assert_is_process_meta(
            next(e for e in metas if e["name"] == "process_name"), pid=12345, args={"name": "Process 12345"}
        )
        assert_is_thread_meta(
            next(e for e in metas if e["name"] == "thread_name"), pid=12345, tid=0, args={"name": "Thread 0"}
        )

    def test_flushes_at_threshold(self, mock_stats_item: GCStatsInfo, trace_exporter: ExporterFactory) -> None:
        exporter, path = trace_exporter(threshold=10)
        for _ in range(10):
            exporter.add_event(DEFAULT_PID, mock_stats_item)
        assert path.exists()
        exporter.close()
        data = assert_valid_chrome_trace_format(path)
        self._verify_events(data, 10)

    def test_flush_multiple_times(self, mock_stats_item: GCStatsInfo, trace_exporter: ExporterFactory) -> None:
        exporter, path = trace_exporter(threshold=5)
        for _ in range(15):
            exporter.add_event(DEFAULT_PID, mock_stats_item)
        assert path.exists()
        exporter.close()
        data = assert_valid_chrome_trace_format(path)
        self._verify_events(data, 15)

    def test_close_writes_file(self, mock_stats_item: GCStatsInfo, trace_exporter: ExporterFactory) -> None:
        exporter, path = trace_exporter()
        exporter.add_event(DEFAULT_PID, mock_stats_item)
        exporter.close()
        assert path.exists()
        data = assert_valid_chrome_trace_format(path)
        for event in data:
            if event["ph"] == "B":
                assert_is_begin(
                    event,
                    name="GC Pause(0)",
                    cat="gc.pause(gen=0)",
                    ts=1_500_000,
                    pid=12345,
                    tid=0,
                    args={
                        "generation": 0,
                        "iid": 0,
                        "collections": 50,
                        "heap_size": 52428800,
                        "collected": 200,
                        "uncollectable": 10,
                        "candidates": 40,
                    },
                )
            elif event["ph"] == "C" and event["name"] == "G0":
                assert_is_counter(
                    event,
                    name="G0",
                    ts=1_500_000,
                    pid=12345,
                    tid=0,
                    args={"collected": 200, "uncollectable": 10, "candidates": 40, "duration": 0.005},
                )
            elif (
                event["ph"] == "C"
                and event["name"] == ""
                and isinstance(event["args"], dict)
                and event["args"].keys() == {"heap_size"}
            ):
                assert_is_counter(event, name="", ts=1_500_000, pid=12345, tid=0, args={"heap_size": 52428800})
            elif event["ph"] == "M" and event["name"] == "process_name":
                assert_is_process_meta(event, pid=12345, args={"name": "Process 12345"})
            elif event["ph"] == "M" and event["name"] == "thread_name":
                assert_is_thread_meta(event, pid=12345, tid=0, args={"name": "Thread 0"})

    def test_close_writes_all_events(self, mock_stats_item: GCStatsInfo, trace_exporter: ExporterFactory) -> None:
        exporter, path = trace_exporter(threshold=5)
        for _ in range(15):
            exporter.add_event(DEFAULT_PID, mock_stats_item)
        exporter.close()
        data = assert_valid_chrome_trace_format(path)
        self._verify_events(data, 15)

    def test_timestamp_conversion(self, mock_stats_item: GCStatsInfo, trace_exporter: ExporterFactory) -> None:
        exporter, path = trace_exporter()
        exporter.add_event(DEFAULT_PID, mock_stats_item)
        exporter.close()

        data = assert_valid_chrome_trace_format(path)
        events = [e for e in data if e.get("ph") != "M"]
        assert events[0]["ts"] == 1_500_000  # begin
        assert events[1]["ts"] == 1_505_000  # end

    def test_complete_event_structure(self, mock_stats_item: GCStatsInfo, trace_exporter: ExporterFactory) -> None:
        exporter, path = trace_exporter()
        exporter.add_event(DEFAULT_PID, mock_stats_item)
        exporter.close()

        data = assert_valid_chrome_trace_format(path)
        event = next(e for e in data if e["ph"] == "B")
        assert_is_begin(
            event,
            name="GC Pause(0)",
            cat="gc.pause(gen=0)",
            ts=1_500_000,
            pid=12345,
            tid=0,
            args={
                "generation": 0,
                "iid": 0,
                "collections": 50,
                "heap_size": 52428800,
                "collected": 200,
                "uncollectable": 10,
                "candidates": 40,
            },
        )

    def test_counter_event_structure(self, mock_stats_item: GCStatsInfo, trace_exporter: ExporterFactory) -> None:
        exporter, path = trace_exporter()
        exporter.add_event(DEFAULT_PID, mock_stats_item)
        exporter.close()

        data = assert_valid_chrome_trace_format(path)
        counters = [e for e in data if e["ph"] == "C"]
        # per-gen counter (with duration folded in) + shared heap_size
        assert len(counters) == 2
        per_gen = next(e for e in counters if e["name"] == "G0")
        assert_is_counter(
            per_gen,
            name="G0",
            ts=1_500_000,
            pid=12345,
            tid=0,
            args={"collected": 200, "uncollectable": 10, "candidates": 40, "duration": 0.005},
        )
        heap = next(c for c in counters if c["name"] == "" and isinstance(c["args"], dict) and "heap_size" in c["args"])
        assert_is_counter(heap, name="", ts=1_500_000, pid=12345, tid=0, args={"heap_size": 52428800})

    def test_close_adds_metadata(self, mock_stats_item: GCStatsInfo, trace_exporter: ExporterFactory) -> None:
        exporter, path = trace_exporter()
        exporter.add_event(DEFAULT_PID, mock_stats_item)
        exporter.close()

        data = assert_valid_chrome_trace_format(path)
        assert_is_process_meta(
            next(e for e in data if e["ph"] == "M" and e["name"] == "process_name"),
            pid=12345,
            args={"name": "Process 12345"},
        )
        assert_is_thread_meta(
            next(e for e in data if e["ph"] == "M" and e["name"] == "thread_name"),
            pid=12345,
            tid=0,
            args={"name": "Thread 0"},
        )

    def test_multiple_close_calls(self, mock_stats_item: GCStatsInfo, trace_exporter: ExporterFactory) -> None:
        exporter, path = trace_exporter()
        exporter.add_event(DEFAULT_PID, mock_stats_item)
        exporter.close()
        exporter.close()

        data = assert_valid_chrome_trace_format(path)
        # Second close should not duplicate events
        self._verify_events(data, 1)

    def test_different_generation_events(self, trace_exporter: ExporterFactory) -> None:
        exporter, path = trace_exporter()
        for gen in range(3):
            item = create_mock_stats_item(gen=gen)
            exporter.add_event(DEFAULT_PID, item)
        exporter.close()

        data = assert_valid_chrome_trace_format(path)
        begin_events = [e for e in data if e["ph"] == "B"]
        assert len(begin_events) == 3
        assert {e["args"]["generation"] for e in begin_events if isinstance(e["args"], dict)} == {0, 1, 2}

    def test_add_instant_event_writes_instant_event(self, trace_exporter: ExporterFactory) -> None:
        exporter, path = trace_exporter()
        instant = create_instant_msg(name="start GC monitor", ts=1_500_000_000)
        exporter.add_instant_event(DEFAULT_PID, instant)
        exporter.close()

        data = assert_valid_chrome_trace_format(path)
        instants = [e for e in data if e["ph"] == "I"]
        assert len(instants) == 1
        assert_is_instant_event(
            instants[0],
            pid=DEFAULT_PID,
            name=instant.name,
            ts=ts_to_us(instant.ts),
        )

    def test_add_instant_event_alongside_add_event(
        self, mock_stats_item: GCStatsInfo, trace_exporter: ExporterFactory
    ) -> None:
        exporter, path = trace_exporter()
        exporter.add_event(DEFAULT_PID, mock_stats_item)
        instant = create_instant_msg(name="stop GC monitor", ts=2_000_000_000)
        exporter.add_instant_event(DEFAULT_PID, instant)
        exporter.close()

        data = assert_valid_chrome_trace_format(path)
        assert any(e["ph"] == "I" for e in data)
        assert any(e["ph"] == "B" for e in data)
        assert any(e["ph"] == "C" for e in data)
        assert any(e["ph"] == "M" for e in data)

        instants = [e for e in data if e["ph"] == "I"]
        assert len(instants) == 1
        assert_is_instant_event(
            instants[0],
            pid=DEFAULT_PID,
            name=instant.name,
            ts=ts_to_us(instant.ts),
        )

    def test_multiple_add_instant_event(self, trace_exporter: ExporterFactory) -> None:
        exporter, path = trace_exporter()
        for name in ("start GC monitor", "stop GC monitor"):
            exporter.add_instant_event(DEFAULT_PID, create_instant_msg(name=name, ts=1_500_000_000))
        exporter.close()

        data = assert_valid_chrome_trace_format(path)
        instants = [e for e in data if e["ph"] == "I"]
        assert len(instants) == 2
        assert [e["name"] for e in instants] == ["start GC monitor", "stop GC monitor"]

    def test_close_with_no_events_writes_empty_trace(self, trace_exporter: ExporterFactory) -> None:
        exporter, path = trace_exporter()
        exporter.close()
        assert path.exists()
        assert path.read_text() == "[]\n"


@pytest.fixture
def mock_read_events() -> Callable[[int, bool], list[GCStatsInfo]]:
    """Generate incrementing event batches to simulate real GC monitoring data."""
    read_count = [0]

    def side_effect(pid: int, all_interpreters: bool = True) -> list[GCStatsInfo]:
        base_ts = read_count[0] * 100 + 1_500_000_000
        # `collections` rises with each collection, so a batch carrying a new
        # timestamp carries a new counter value too.
        nth = read_count[0]
        read_count[0] += 1
        item1 = create_mock_stats_item(
            gen=0,
            ts_start=base_ts,
            ts_stop=base_ts + 5_000_000,
            collections=10 + nth,
            collected=50,
            uncollectable=1,
            candidates=20,
            heap_size=1000000,
            duration=0.001,
        )
        item2 = create_mock_stats_item(
            gen=1,
            ts_start=base_ts + 1_000_000,
            ts_stop=base_ts + 6_000_000,
            collections=20 + nth,
            collected=100,
            uncollectable=2,
            candidates=40,
            heap_size=2000000,
            duration=0.002,
        )
        return [item1, item2]

    return side_effect


@pytest.fixture
def mock_lossy_read_events() -> Callable[[int, bool], list[GCStatsInfo]]:
    """The same shape, with the counter skipping three records per poll.

    `mock_read_events` advances `collections` by one, so it can never produce
    a gap. A generation gcmon lost records on returns a counter further
    along than the one before it, and `duration` further along by the pause of
    everything in between, which is what makes the loss measurable.
    """
    read_count = [0]
    pause_ns = 5_000_000
    per_poll = 4

    def side_effect(pid: int, all_interpreters: bool = True) -> list[GCStatsInfo]:
        nth = read_count[0]
        read_count[0] += 1
        collections = 10 + per_poll * nth
        base_ts = 1_500_000_000 + nth * 100_000_000
        return [
            create_mock_stats_item(
                gen=0,
                ts_start=base_ts,
                ts_stop=base_ts + pause_ns,
                collections=collections,
                collected=50,
                uncollectable=1,
                candidates=20,
                heap_size=1000000,
                duration=collections * pause_ns / 1e9,
            )
        ]

    return side_effect


@pytest.fixture
def monitor_with_exporter(trace_exporter: ExporterFactory, reader: FakeEventsReader) -> tuple[EventsMonitor, Path]:
    """Create an EventsMonitor wired to a TraceExporter."""
    exporter, path = trace_exporter()
    assert isinstance(exporter, TraceExporter)
    process = ExternalProcess(pid=12345)
    monitor = EventsMonitor(process, exporter, StreamingStats(), reader=reader, wait_policy_factory=no_wait_policy)
    return monitor, path


@pytest.fixture
def mock_gc_stats(reader: FakeEventsReader, mock_read_events: Callable[..., list[GCStatsInfo]]) -> None:
    reader.reads = mock_read_events


@pytest.fixture
def mock_lossy_gc_stats(reader: FakeEventsReader, mock_lossy_read_events: Callable[..., list[GCStatsInfo]]) -> None:
    reader.reads = mock_lossy_read_events


def _ts(event: dict[str, ChromeTraceValue]) -> int:
    """A Chrome event's timestamp, which the format's value union hides."""
    value = event["ts"]
    assert isinstance(value, int)
    return value


class TestGCMonitorStreamsLoss:
    """The whole chain on one path: records missed between two polls have
    to come out of the exporter as a slice, not just as a number in the stats
    table. Everything between the accumulator and the file is exercised only
    here.
    """

    def trace(
        self,
        monitor_with_exporter: tuple[EventsMonitor, Path],
        polls: int = 3,
    ) -> list[dict[str, ChromeTraceValue]]:
        monitor, path = monitor_with_exporter
        for _ in range(polls):
            monitor._poll(DEFAULT_PID)
        monitor.stop()
        return assert_valid_chrome_trace_format(path)

    def losses(self, data: list[dict[str, ChromeTraceValue]]) -> list[dict[str, ChromeTraceValue]]:
        return [e for e in data if e["name"] == "GC Loss(0)" and e["ph"] == "B"]

    def test_a_missed_run_draws_a_slice(
        self,
        mock_lossy_gc_stats: None,
        monitor_with_exporter: tuple[EventsMonitor, Path],
    ) -> None:
        data = self.trace(monitor_with_exporter)

        # The first poll seeds the cursor; the two after it each find a gap.
        assert len(self.losses(data)) == 2

    def test_the_slice_reports_what_the_counters_say(
        self,
        mock_lossy_gc_stats: None,
        monitor_with_exporter: tuple[EventsMonitor, Path],
    ) -> None:
        data = self.trace(monitor_with_exporter)

        args = self.losses(data)[0]["args"]
        assert isinstance(args, dict)
        assert args["lost_count"] == 3
        assert args["lost_pause_ns"] == 15_000_000
        assert args["gen0"] == {
            "observed_count": 1,
            "lost_collections": "11..13",
            "lost_count": 3,
            "lost_pause": "15ms",
            "lost_pause_ns": 15_000_000,
        }

    def test_it_lands_on_the_loss_track(
        self,
        mock_lossy_gc_stats: None,
        monitor_with_exporter: tuple[EventsMonitor, Path],
    ) -> None:
        data = self.trace(monitor_with_exporter)

        assert {e["tid"] for e in self.losses(data)} == {loss_tid(0)}

    def test_it_spans_the_interval_between_two_polls(
        self,
        mock_lossy_gc_stats: None,
        monitor_with_exporter: tuple[EventsMonitor, Path],
    ) -> None:
        """The edges come off the monitor's clock, not off the records, so the
        one thing a test can name here is that consecutive spans meet."""
        data = self.trace(monitor_with_exporter)

        begins = [_ts(e) for e in self.losses(data)]
        ends = [_ts(e) for e in data if e["name"] == "GC Loss(0)" and e["ph"] == "E"]

        assert len(begins) == len(ends) == 2
        assert ends[0] == begins[1]
        assert all(begin < end for begin, end in zip(begins, ends, strict=True))

    def test_a_run_that_lost_nothing_draws_none(
        self, mock_gc_stats: None, monitor_with_exporter: tuple[EventsMonitor, Path]
    ) -> None:
        """The contiguous fixture is the control: no gap, no track, no slice.
        A lossless capture looks as it did before loss existed."""
        data = self.trace(monitor_with_exporter, polls=4)

        assert self.losses(data) == []
        assert all(tid >= 0 for tid in (e["tid"] for e in data if "tid" in e) if isinstance(tid, int))


class TestGCMonitorStreaming:
    def test_streams_to_exporter(self, mock_gc_stats: None, monitor_with_exporter: tuple[EventsMonitor, Path]) -> None:
        monitor, path = monitor_with_exporter
        for _ in range(4):
            monitor._poll(12345)
        monitor.stop()
        assert path.exists()
        data = assert_valid_chrome_trace_format(path)
        assert_is_process_meta(
            next(e for e in data if e["ph"] == "M" and e["name"] == "process_name"),
            pid=12345,
            args={"name": "Process 12345"},
        )
        assert any(e["name"] == "GC Pause(1)" for e in data)
        # At least one begin event per poll
        assert len([e for e in data if e["ph"] == "B"]) >= 4
        assert len([e for e in data if e["ph"] == "C"]) >= 4

    def test_streams_events_individually(
        self, mock_gc_stats: None, monitor_with_exporter: tuple[EventsMonitor, Path]
    ) -> None:
        monitor, path = monitor_with_exporter
        for _ in range(3):
            monitor._poll(12345)
        monitor.stop()
        data = assert_valid_chrome_trace_format(path)
        assert len([e for e in data if e["ph"] == "B"]) >= 4

    def test_stop_closes_exporter(self, mock_gc_stats: None, monitor_with_exporter: tuple[EventsMonitor, Path]) -> None:
        monitor, path = monitor_with_exporter
        for _ in range(3):
            monitor._poll(12345)
        monitor.stop()
        assert path.exists()
        data = assert_valid_chrome_trace_format(path)
        assert_is_process_meta(
            next(e for e in data if e["ph"] == "M" and e["name"] == "process_name"),
            pid=12345,
            args={"name": "Process 12345"},
        )
        assert next((e for e in data if e["ph"] == "M" and e["name"] == "thread_name"), None) is not None
        assert len([e for e in data if e["ph"] == "B"]) >= 3
        assert len([e for e in data if e["ph"] == "C"]) >= 3

    def test_handles_read_error_gracefully(
        self, monitor_with_exporter: tuple[EventsMonitor, Path], reader: FakeEventsReader
    ) -> None:
        monitor, path = monitor_with_exporter
        item = create_mock_stats_item(
            gen=0,
            ts_start=1_500_000_000,
            ts_stop=1_505_000_000,
            collections=10,
            collected=50,
            uncollectable=1,
            candidates=20,
            heap_size=1000000,
            duration=0.001,
        )
        call_count = [0]

        def side_effect(pid: int) -> list[GCStatsInfo]:
            call_count[0] += 1
            if call_count[0] == 1:
                return [item]
            raise TargetUnavailable("Connection broken")

        reader.reads = side_effect

        from gcmon.poll_status import PollStatus

        assert monitor._poll(12345) == PollStatus.OK
        result = monitor._poll(12345)
        assert result in (PollStatus.INVALID_PROCESS, PollStatus.FAIL)

        monitor.stop()
        assert path.exists()
        data = assert_valid_chrome_trace_format(path)
        # 1 successful poll = 1 GC event (B/E + C) + metadata
        assert_is_process_meta(
            next(e for e in data if e["ph"] == "M" and e["name"] == "process_name"),
            pid=12345,
            args={"name": "Process 12345"},
        )
        assert_is_thread_meta(
            next(e for e in data if e["ph"] == "M" and e["name"] == "thread_name"),
            pid=12345,
            tid=0,
            args={"name": "Thread 0"},
        )
        assert len([e for e in data if e["ph"] == "B"]) == 1
        # per-gen counter (with duration folded in) + shared heap_size
        assert len([e for e in data if e["ph"] == "C"]) == 2
