"""Tests for Chrome trace exporter."""

from unittest.mock import patch

import pytest

from gc_monitor.data import ts_to_us
from gc_monitor.monitor import EventsMonitor
from gc_monitor.stats import StreamingStats
from gc_monitor.target_process import ExternalProcess

from tests.conftest import DEFAULT_PID
from tests.data_helpers import create_instant_msg
from tests.helpers import (
    assert_is_complete,
    assert_is_counter,
    assert_is_process_meta,
    assert_is_thread_meta,
    assert_is_instant_event,
    assert_valid_chrome_trace_format,
    create_mock_stats_item,
)


class TestTraceExporter:
    def test_init(self, trace_exporter) -> None:
        exporter, path = trace_exporter()
        assert exporter.get_event_count() == 0

    def test_init_with_flush_threshold(self, trace_exporter) -> None:
        exporter, path = trace_exporter(threshold=500)
        assert exporter.get_event_count() == 0

    def _verify_events(self, data: list[dict], num_items: int) -> None:
        completes = [e for e in data if e["ph"] == "X"]
        counters = [e for e in data if e["ph"] == "C"]
        metas = [e for e in data if e["ph"] == "M"]
        assert len(completes) == num_items
        assert len(counters) == num_items
        assert all(e["name"] == "GC Pause (gen=0)" for e in completes)
        assert all(e["name"] == "G0" for e in counters)
        assert_is_process_meta(next(e for e in metas if e["name"] == "process_name"), pid=12345, args={"name": "Parent Process"})
        assert_is_thread_meta(next(e for e in metas if e["name"] == "thread_name"), pid=12345, tid=0, args={"name": "Thread 0"})

    def test_flushes_at_threshold(self, mock_stats_item, trace_exporter) -> None:
        exporter, path = trace_exporter(threshold=10)
        for _ in range(10):
            exporter.add_event(DEFAULT_PID, mock_stats_item)
        assert path.exists()
        exporter.close()
        data = assert_valid_chrome_trace_format(path)
        self._verify_events(data, 10)

    def test_flush_multiple_times(self, mock_stats_item, trace_exporter) -> None:
        exporter, path = trace_exporter(threshold=5)
        for _ in range(15):
            exporter.add_event(DEFAULT_PID, mock_stats_item)
        assert path.exists()
        exporter.close()
        data = assert_valid_chrome_trace_format(path)
        self._verify_events(data, 15)

    def test_close_writes_file(self, mock_stats_item, trace_exporter) -> None:
        exporter, path = trace_exporter()
        exporter.add_event(DEFAULT_PID, mock_stats_item)
        exporter.close()
        assert path.exists()
        data = assert_valid_chrome_trace_format(path)
        for event in data:
            if event["ph"] == "X":
                assert_is_complete(event, name="GC Pause (gen=0)", cat="gc.pause(gen=0)", ts=1_500_000, dur=5_000.0, pid=12345, tid=0, args={"generation": 0, "iid": 0, "collections": 50, "heap_size": 52428800, "collected": 200, "uncollectable": 10, "candidates": 40})
            elif event["ph"] == "C":
                assert_is_counter(event, name="G0", ts=1_500_000, pid=12345, tid=0, args={"collected": 200, "uncollectable": 10, "candidates": 40, "heap_size": 52428800})
            elif event["ph"] == "M" and event["name"] == "process_name":
                assert_is_process_meta(event, pid=12345, args={"name": "Parent Process"})
            elif event["ph"] == "M" and event["name"] == "thread_name":
                assert_is_thread_meta(event, pid=12345, tid=0, args={"name": "Thread 0"})

    def test_close_writes_all_events(self, mock_stats_item, trace_exporter) -> None:
        exporter, path = trace_exporter(threshold=5)
        for _ in range(15):
            exporter.add_event(DEFAULT_PID, mock_stats_item)
        exporter.close()
        data = assert_valid_chrome_trace_format(path)
        self._verify_events(data, 15)

    def test_add_event_count(self, mock_stats_item, trace_exporter) -> None:
        exporter, path = trace_exporter()
        exporter.add_event(DEFAULT_PID, mock_stats_item)
        assert exporter.get_event_count() == 1

    def test_timestamp_conversion(self, mock_stats_item, trace_exporter) -> None:
        exporter, path = trace_exporter()
        exporter.add_event(DEFAULT_PID, mock_stats_item)
        exporter.close()

        data = assert_valid_chrome_trace_format(path)
        events = [e for e in data if e.get("ph") != "M"]
        assert events[0]["ts"] == 1_500_000
        assert events[1]["ts"] == 1_500_000
        assert events[0]["dur"] == 5000

    def test_complete_event_structure(self, mock_stats_item, trace_exporter) -> None:
        exporter, path = trace_exporter()
        exporter.add_event(DEFAULT_PID, mock_stats_item)
        exporter.close()

        data = assert_valid_chrome_trace_format(path)
        event = next(e for e in data if e["ph"] == "X")
        assert_is_complete(event, name="GC Pause (gen=0)", cat="gc.pause(gen=0)", ts=1_500_000, dur=5_000.0, pid=12345, tid=0, args={"generation": 0, "iid": 0, "collections": 50, "heap_size": 52428800, "collected": 200, "uncollectable": 10, "candidates": 40})

    def test_counter_event_structure(self, mock_stats_item, trace_exporter) -> None:
        exporter, path = trace_exporter()
        exporter.add_event(DEFAULT_PID, mock_stats_item)
        exporter.close()

        data = assert_valid_chrome_trace_format(path)
        event = next(e for e in data if e["ph"] == "C")
        assert_is_counter(event, name="G0", ts=1_500_000, pid=12345, tid=0, args={"collected": 200, "uncollectable": 10, "candidates": 40, "heap_size": 52428800})

    def test_close_adds_metadata(self, mock_stats_item, trace_exporter) -> None:
        exporter, path = trace_exporter()
        exporter.add_event(DEFAULT_PID, mock_stats_item)
        exporter.close()

        data = assert_valid_chrome_trace_format(path)
        assert_is_process_meta(next(e for e in data if e["ph"] == "M" and e["name"] == "process_name"), pid=12345, args={"name": "Parent Process"})
        assert_is_thread_meta(next(e for e in data if e["ph"] == "M" and e["name"] == "thread_name"), pid=12345, tid=0, args={"name": "Thread 0"})

    def test_multiple_close_calls(self, mock_stats_item, trace_exporter) -> None:
        exporter, path = trace_exporter()
        exporter.add_event(DEFAULT_PID, mock_stats_item)
        exporter.close()
        exporter.close()

        data = assert_valid_chrome_trace_format(path)
        # Second close should not duplicate events
        self._verify_events(data, 1)

    def test_different_generation_events(self, trace_exporter) -> None:
        exporter, path = trace_exporter()
        for gen in range(3):
            item = create_mock_stats_item(gen=gen)
            exporter.add_event(DEFAULT_PID, item)
        exporter.close()

        data = assert_valid_chrome_trace_format(path)
        complete_events = [e for e in data if e["ph"] == "X"]
        assert len(complete_events) == 3
        assert {e["args"]["generation"] for e in complete_events} == {0, 1, 2}

    def test_add_instant_event_writes_instant_event(self, trace_exporter) -> None:
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

    def test_add_instant_event_alongside_add_event(self, mock_stats_item, trace_exporter) -> None:
        exporter, path = trace_exporter()
        exporter.add_event(DEFAULT_PID, mock_stats_item)
        instant = create_instant_msg(name="stop GC monitor", ts=2_000_000_000)
        exporter.add_instant_event(DEFAULT_PID, instant)
        exporter.close()

        data = assert_valid_chrome_trace_format(path)
        assert any(e["ph"] == "I" for e in data)
        assert any(e["ph"] == "X" for e in data)
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

    def test_add_instant_event_not_counted_in_get_event_count(self, trace_exporter) -> None:
        exporter, path = trace_exporter()
        instant = create_instant_msg(name="event", ts=1_000_000_000)
        exporter.add_instant_event(DEFAULT_PID, instant)
        assert exporter.get_event_count() == 0  # instant events not counted

    def test_multiple_add_instant_event(self, trace_exporter) -> None:
        exporter, path = trace_exporter()
        for name in ("start GC monitor", "stop GC monitor"):
            exporter.add_instant_event(DEFAULT_PID, create_instant_msg(name=name, ts=1_500_000_000))
        exporter.close()

        data = assert_valid_chrome_trace_format(path)
        instants = [e for e in data if e["ph"] == "I"]
        assert len(instants) == 2
        assert [e["name"] for e in instants] == ["start GC monitor", "stop GC monitor"]


@pytest.fixture
def mock_read_events():
    """Generate incrementing event batches to simulate real GC monitoring data."""
    read_count = [0]

    def side_effect(*args, **kwargs):
        base_ts = read_count[0] * 100 + 1_500_000_000
        read_count[0] += 1
        item1 = create_mock_stats_item(
            gen=0, ts_start=base_ts, ts_stop=base_ts + 5_000_000,
            collections=10, collected=50, uncollectable=1, candidates=20,
            object_visits=100, objects_transitively_reachable=50,
            objects_not_transitively_reachable=30, heap_size=1000000,
            work_to_do=5, duration=0.001,
        )
        item2 = create_mock_stats_item(
            gen=1, ts_start=base_ts + 1_000_000, ts_stop=base_ts + 6_000_000,
            collections=20, collected=100, uncollectable=2, candidates=40,
            object_visits=200, objects_transitively_reachable=100,
            objects_not_transitively_reachable=60, heap_size=2000000,
            work_to_do=10, duration=0.002,
        )
        return [item1, item2]

    return side_effect


@pytest.fixture
def monitor_with_exporter(trace_exporter):
    """Create an EventsMonitor wired to a TraceExporter."""
    exporter, path = trace_exporter()
    process = ExternalProcess(pid=12345)
    monitor = EventsMonitor(process, lambda meta: exporter, StreamingStats())
    return monitor, exporter, path


class TestGCMonitorStreaming:
    def test_streams_to_exporter(self, mock_read_events, monitor_with_exporter) -> None:
        monitor, exporter, path = monitor_with_exporter
        with patch("gc_monitor.monitor.get_gc_stats", side_effect=mock_read_events):
            for _ in range(4):
                monitor.poll(12345)
        monitor.stop()
        assert path.exists()
        data = assert_valid_chrome_trace_format(path)
        assert_is_process_meta(next(e for e in data if e["ph"] == "M" and e["name"] == "process_name"), pid=12345, args={"name": "Parent Process"})
        assert any(e["name"] == "GC Pause (gen=1)" for e in data)
        # At least one complete event per poll
        assert len([e for e in data if e["ph"] == "X"]) >= 4
        assert len([e for e in data if e["ph"] == "C"]) >= 4

    def test_streams_events_individually(self, mock_read_events, monitor_with_exporter) -> None:
        monitor, exporter, path = monitor_with_exporter
        with patch("gc_monitor.monitor.get_gc_stats", side_effect=mock_read_events):
            for _ in range(3):
                monitor.poll(12345)
        monitor.stop()
        assert exporter.get_event_count() >= 4

    def test_stop_closes_exporter(self, mock_read_events, monitor_with_exporter) -> None:
        monitor, exporter, path = monitor_with_exporter
        with patch("gc_monitor.monitor.get_gc_stats", side_effect=mock_read_events):
            for _ in range(3):
                monitor.poll(12345)
        monitor.stop()
        assert path.exists()
        data = assert_valid_chrome_trace_format(path)
        assert_is_process_meta(next(e for e in data if e["ph"] == "M" and e["name"] == "process_name"), pid=12345, args={"name": "Parent Process"})
        assert next((e for e in data if e["ph"] == "M" and e["name"] == "thread_name"), None) is not None
        assert len([e for e in data if e["ph"] == "X"]) >= 3
        assert len([e for e in data if e["ph"] == "C"]) >= 3

    def test_handles_read_error_gracefully(self, monitor_with_exporter) -> None:
        monitor, exporter, path = monitor_with_exporter
        item = create_mock_stats_item(
            gen=0, ts_start=1_500_000_000, ts_stop=1_505_000_000,
            collections=10, collected=50, uncollectable=1, candidates=20,
            object_visits=100, objects_transitively_reachable=50,
            objects_not_transitively_reachable=30, heap_size=1000000,
            work_to_do=5, duration=0.001,
        )
        call_count = [0]
        def side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return [item]
            raise RuntimeError("Connection broken")

        with patch("gc_monitor.monitor.get_gc_stats", side_effect=side_effect):
            from gc_monitor.poll_status import PollStatus
            assert monitor.poll(12345) == PollStatus.OK
            result = monitor.poll(12345)
            assert result in (PollStatus.INVALID_PROCESS, PollStatus.FAIL)

        monitor.stop()
        assert path.exists()
        data = assert_valid_chrome_trace_format(path)
        # 1 successful poll = 1 GC event (X + C) + metadata
        assert_is_process_meta(next(e for e in data if e["ph"] == "M" and e["name"] == "process_name"), pid=12345, args={"name": "Parent Process"})
        assert_is_thread_meta(next(e for e in data if e["ph"] == "M" and e["name"] == "thread_name"), pid=12345, tid=0, args={"name": "Thread 0"})
        assert len([e for e in data if e["ph"] == "X"]) == 1
        assert len([e for e in data if e["ph"] == "C"]) == 1
