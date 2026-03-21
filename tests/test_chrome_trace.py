"""Tests for Chrome trace exporter."""

import json
import threading
import time
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest

from gc_monitor.chrome_trace_exporter import TraceExporter
from gc_monitor.core import GCMonitor
from gc_monitor._gc_monitor import GCMonitorHandler


class TestTraceExporter:
    """Tests for TraceExporter class."""

    @pytest.fixture
    def mock_stats_item(self) -> Mock:
        """Create a mock GCMonitorStatsItem.
        
        Note: ts is in nanoseconds, duration and total_duration are in seconds.
        """
        item = Mock()
        item.gen = 2
        item.ts = 1_500_000_000  # 1.5 seconds in nanoseconds
        item.collections = 50
        item.collected = 200
        item.uncollectable = 10
        item.candidates = 40
        item.object_visits = 600
        item.objects_transitively_reachable = 250
        item.objects_not_transitively_reachable = 150
        item.heap_size = 52428800
        item.work_to_do = 30
        item.duration = 0.005  # 5ms in seconds
        item.total_duration = 45.5  # 45.5 seconds in seconds
        return item  # type: ignore[no-any-return]

    def test_exporter_init(self, tmp_path: Path) -> None:
        """Test exporter initialization."""
        exporter = TraceExporter(pid=12345, output_path=tmp_path / "trace.json")

        assert exporter.get_event_count() == 0

    def test_exporter_init_with_flush_threshold(self, tmp_path: Path) -> None:
        """Test exporter with custom flush threshold."""
        exporter = TraceExporter(
            pid=12345, output_path=tmp_path / "trace.json", flush_threshold=500
        )
        assert exporter.get_event_count() == 0

    def test_exporter_flushes_at_threshold(
        self, mock_stats_item: Mock, tmp_path: Path
    ) -> None:
        """Test that exporter flushes to file when threshold is reached."""
        output_file = tmp_path / "trace.json"
        exporter = TraceExporter(
            pid=12345, output_path=output_file, flush_threshold=10
        )

        # Add events up to threshold
        for _ in range(10):
            exporter.add_event(mock_stats_item)

        # File should be created after threshold
        assert output_file.exists()

        with open(output_file) as f:
            data: list[dict[str, Any]] = json.load(f)  # type: ignore[assignment]

        # Should have 20 events (10 * 2 for complete + counter)
        assert len(data) >= 20

    def test_exporter_flush_multiple_times(
        self, mock_stats_item: Mock, tmp_path: Path
    ) -> None:
        """Test that exporter can flush multiple times."""
        output_file = tmp_path / "trace.json"
        exporter = TraceExporter(
            pid=12345, output_path=output_file, flush_threshold=5
        )

        # Add 15 events (3 flushes)
        for _ in range(15):
            exporter.add_event(mock_stats_item)

        assert output_file.exists()

        with open(output_file) as f:
            data = json.load(f)

        # Should have all events (15 * 2 = 30)
        assert len(data) >= 30

    def test_exporter_close_writes_file(
        self, mock_stats_item: Mock, tmp_path: Path
    ) -> None:
        """Test that close() writes all events to file."""
        output_file = tmp_path / "trace.json"
        exporter = TraceExporter(pid=12345, output_path=output_file)

        exporter.add_event(mock_stats_item)
        exporter.close()

        # File should be created on close
        assert output_file.exists()

        with open(output_file) as f:
            data: list[dict[str, Any]] = json.load(f)  # type: ignore[assignment]

        # Should have metadata (2) + events (2)
        assert len(data) == 4

    def test_exporter_close_writes_all_events(
        self, mock_stats_item: Mock, tmp_path: Path
    ) -> None:
        """Test that close() writes all events including flushed ones."""
        output_file = tmp_path / "trace.json"
        exporter = TraceExporter(
            pid=12345, output_path=output_file, flush_threshold=5
        )

        # Add 15 events (triggers 3 flushes)
        for _ in range(15):
            exporter.add_event(mock_stats_item)

        # Close should write remaining events
        exporter.close()

        with open(output_file) as f:
            data: list[dict[str, Any]] = json.load(f)  # type: ignore[assignment]

        # Should have metadata (2) + all events (15 * 2 = 30)
        # But metadata is written on first flush, not on close
        # So we have 30 events (metadata was written on first flush)
        assert len(data) == 30

    def test_add_event_creates_complete_and_counter(
        self, mock_stats_item: Mock, tmp_path: Path
    ) -> None:
        """Test that add_event creates both complete and counter events."""
        exporter = TraceExporter(pid=12345, output_path=tmp_path / "trace.json")
        exporter.add_event(mock_stats_item)

        # Should have 2 events: 1 complete + 1 counter
        assert exporter.get_event_count() == 2

    def test_add_event_timestamp_conversion(
        self, mock_stats_item: Mock, tmp_path: Path
    ) -> None:
        """Test timestamp conversion to microseconds."""
        exporter = TraceExporter(pid=12345, output_path=tmp_path / "test.json")
        exporter.add_event(mock_stats_item)
        exporter.close()

        with open(tmp_path / "test.json") as f:
            # First 2 events are metadata, then our events
            events: list[dict[str, Any]] = json.load(f)[2:]  # type: ignore[assignment]

        # ts = 1,500,000,000 nanoseconds -> 1,500,000 microseconds
        assert events[0]["ts"] == 1_500_000
        assert events[1]["ts"] == 1_500_000

        # duration = 0.005 seconds -> 5000 microseconds
        assert events[0]["dur"] == 5000

    def test_add_event_complete_event_structure(
        self, mock_stats_item: Mock, tmp_path: Path
    ) -> None:
        """Test complete event structure."""
        exporter = TraceExporter(pid=12345, output_path=tmp_path / "test.json")
        exporter.add_event(mock_stats_item)
        exporter.close()

        with open(tmp_path / "test.json") as f:
            # First 2 events are metadata, then our events
            event: dict[str, Any] = json.load(f)[2]  # type: ignore[assignment]

        assert event["name"] == "GC Pause (Gen 2)"
        assert event["cat"] == "gc"
        assert event["ph"] == "X"
        assert event["pid"] == 12345
        assert event["tid"] == "GC Monitor"
        assert event["args"]["generation"] == 2
        assert event["args"]["collected"] == 200
        assert event["args"]["uncollectable"] == 10

    def test_add_event_counter_event_structure(
        self, mock_stats_item: Mock, tmp_path: Path
    ) -> None:
        """Test counter event structure."""
        exporter = TraceExporter(pid=12345, output_path=tmp_path / "test.json")
        exporter.add_event(mock_stats_item)
        exporter.close()

        with open(tmp_path / "test.json") as f:
            # First 2 events are metadata, then our events
            event: dict[str, Any] = json.load(f)[3]  # type: ignore[assignment]

        assert event["name"] == "Memory Counters"
        assert event["cat"] == "gc.memory"
        assert event["ph"] == "C"
        assert event["pid"] == 12345
        assert event["tid"] == "GC Monitor"
        assert event["args"]["heap_size"] == 52428800
        assert event["args"]["collections"] == 50

    def test_close_adds_metadata(self, mock_stats_item: Mock, tmp_path: Path) -> None:
        """Test that close() automatically adds metadata."""
        exporter = TraceExporter(pid=12345, output_path=tmp_path / "test_trace.json")
        exporter.add_event(mock_stats_item)
        exporter.close()

        with open(tmp_path / "test_trace.json") as f:
            data: list[dict[str, Any]] = json.load(f)  # type: ignore[assignment]

        # Find metadata events
        metadata_events = [e for e in data if e["ph"] == "M"]
        assert len(metadata_events) == 2

        process_name = next(
            e for e in metadata_events if e["name"] == "process_name"
        )
        assert f"PID: {12345}" in process_name["args"]["name"]

        thread_name = next(e for e in metadata_events if e["name"] == "thread_name")
        assert thread_name["args"]["name"] == "GC Monitor"

    def test_thread_safety(self, mock_stats_item: Mock, tmp_path: Path) -> None:
        """Test thread-safe event addition."""
        exporter = TraceExporter(pid=12345, output_path=tmp_path / "trace.json")

        def add_events() -> None:
            for _ in range(100):
                exporter.add_event(mock_stats_item)

        threads = [threading.Thread(target=add_events) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        exporter.close()

        # Should have 10 threads * 100 events * 2 (complete + counter) = 2000
        assert exporter.get_event_count() == 2000

    def test_clear(self, mock_stats_item: Mock, tmp_path: Path) -> None:
        """Test clearing events."""
        exporter = TraceExporter(pid=12345, output_path=tmp_path / "trace.json")
        exporter.add_event(mock_stats_item)
        exporter.clear()

        assert exporter.get_event_count() == 0

    def test_multiple_close_calls(
        self, mock_stats_item: Mock, tmp_path: Path
    ) -> None:
        """Test that multiple close calls don't duplicate metadata."""
        exporter = TraceExporter(pid=12345, output_path=tmp_path / "test_trace.json")
        exporter.add_event(mock_stats_item)

        # Close multiple times
        exporter.close()
        exporter.close()

        with open(tmp_path / "test_trace.json") as f:
            data: list[dict[str, Any]] = json.load(f)  # type: ignore[assignment]

        # Metadata should only appear once
        metadata_events = [e for e in data if e["ph"] == "M"]
        assert len(metadata_events) == 2

    def test_different_generation_events(self, tmp_path: Path) -> None:
        """Test events with different GC generations."""
        exporter = TraceExporter(pid=12345, output_path=tmp_path / "test_trace.json")

        for gen in range(3):
            item = Mock()
            item.gen = gen
            item.ts = 1.0
            item.collections = 10
            item.collected = 50
            item.uncollectable = 1
            item.candidates = 20
            item.object_visits = 100
            item.objects_transitively_reachable = 50
            item.objects_not_transitively_reachable = 30
            item.heap_size = 1000000
            item.work_to_do = 5
            item.duration = 0.001 * (gen + 1)
            item.total_duration = 1.0
            exporter.add_event(item)

        exporter.close()

        with open(tmp_path / "test_trace.json") as f:
            data: list[dict[str, Any]] = json.load(f)  # type: ignore[assignment]

        # Find complete events
        complete_events = [e for e in data if e["ph"] == "X"]
        assert len(complete_events) == 3

        generations = {e["args"]["generation"] for e in complete_events}
        assert generations == {0, 1, 2}

        # Check event names
        event_names = {e["name"] for e in complete_events}
        assert event_names == {
            "GC Pause (Gen 0)",
            "GC Pause (Gen 1)",
            "GC Pause (Gen 2)",
        }


class TestGCMonitorStreaming:
    """Tests for GCMonitor streaming functionality."""

    @pytest.fixture
    def mock_handler(self) -> Mock:
        """Create a mock GCMonitorHandler."""
        handler = Mock(spec=GCMonitorHandler)
        handler._connected = True
        # Return batch of 2 events per read
        item1 = Mock()
        item1.gen = 0
        item1.ts = 1.0
        item1.collections = 10
        item1.collected = 50
        item1.uncollectable = 1
        item1.candidates = 20
        item1.object_visits = 100
        item1.objects_transitively_reachable = 50
        item1.objects_not_transitively_reachable = 30
        item1.heap_size = 1000000
        item1.work_to_do = 5
        item1.duration = 0.001
        item1.total_duration = 1.0

        item2 = Mock()
        item2.gen = 1
        item2.ts = 2.0
        item2.collections = 20
        item2.collected = 100
        item2.uncollectable = 2
        item2.candidates = 40
        item2.object_visits = 200
        item2.objects_transitively_reachable = 100
        item2.objects_not_transitively_reachable = 60
        item2.heap_size = 2000000
        item2.work_to_do = 10
        item2.duration = 0.002
        item2.total_duration = 2.0

        handler.read.return_value = [item1, item2]
        return handler

    def test_gcmonitor_streams_each_event_to_exporter(
        self, mock_handler: Mock, tmp_path: Path
    ) -> None:
        """Test that GCMonitor streams each event from read() to exporter immediately."""
        exporter = TraceExporter(pid=12345, output_path=tmp_path / "trace.json")

        monitor = GCMonitor(mock_handler, exporter, rate=0.05)

        # Let it run for a bit to process events
        time.sleep(0.2)
        monitor.stop()

        # Should have streamed all events (2 events per read, multiple reads)
        assert exporter.get_event_count() >= 4  # At least 2 reads * 2 events

    def test_gcmonitor_streams_events_individually(
        self, mock_handler: Mock, tmp_path: Path
    ) -> None:
        """Test that each event in a batch is streamed separately."""
        exporter = TraceExporter(pid=12345, output_path=tmp_path / "trace.json")

        # Track when events are added
        events_added: list[int] = []
        original_add = exporter.add_event

        def tracking_add(item: Mock) -> None:
            events_added.append(item.gen)
            original_add(item)  # type: ignore[arg-type]

        exporter.add_event = tracking_add  # pyright: ignore[reportAttributeAccessIssue]

        monitor = GCMonitor(mock_handler, exporter, rate=0.05)
        time.sleep(0.15)
        monitor.stop()

        # Events should be added individually (gen 0 and gen 1 alternating)
        assert len(events_added) >= 4
        # Should see both gen 0 and gen 1 events
        assert 0 in events_added
        assert 1 in events_added

    def test_gcmonitor_stop_closes_exporter(
        self, mock_handler: Mock, tmp_path: Path
    ) -> None:
        """Test that stop() closes the exporter and writes file."""
        output_file = tmp_path / "trace.json"
        exporter = TraceExporter(pid=12345, output_path=output_file)

        monitor = GCMonitor(mock_handler, exporter, rate=0.05)
        time.sleep(0.15)
        monitor.stop()

        # File should be saved on close
        assert output_file.exists()

        with open(output_file) as f:
            data: list[dict[str, Any]] = json.load(f)  # type: ignore[assignment]

        # Should have events
        assert len(data) >= 4

    def test_gcmonitor_handles_read_error_gracefully(
        self, tmp_path: Path
    ) -> None:
        """Test that GCMonitor stops gracefully when read() raises RuntimeError."""
        handler = Mock(spec=GCMonitorHandler)
        handler._connected = True
        # First call succeeds, second fails
        item = Mock()
        item.gen = 0
        item.ts = 1.0
        item.collections = 10
        item.collected = 50
        item.uncollectable = 1
        item.candidates = 20
        item.object_visits = 100
        item.objects_transitively_reachable = 50
        item.objects_not_transitively_reachable = 30
        item.heap_size = 1000000
        item.work_to_do = 5
        item.duration = 0.001
        item.total_duration = 1.0

        handler.read.side_effect = [[item], RuntimeError("Connection broken")]
        exporter = TraceExporter(pid=12345, output_path=tmp_path / "trace.json")

        monitor = GCMonitor(handler, exporter, rate=0.05)
        # Should not raise, just stop on error
        time.sleep(0.15)
        monitor.stop()

        # Should have captured the first event before error
        assert exporter.get_event_count() >= 2  # 1 event * 2 (complete + counter)
