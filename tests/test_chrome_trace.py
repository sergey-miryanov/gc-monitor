"""Tests for Chrome trace exporter."""

import time
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import Mock

import pytest

from gc_monitor.chrome_trace_exporter import TraceExporter
from gc_monitor.core import GCMonitor

from tests.test_pyperf_hook import _assert_valid_chrome_trace_format  # pyright: ignore[reportPrivateUsage]

# Import GCMonitorHandler from the same source as core.py uses
if TYPE_CHECKING:
    from gc_monitor._gc_monitor import GCMonitorHandler
else:
    try:
        from _gc_monitor import GCMonitorHandler  # type: ignore[import-not-found]
    except ImportError:
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
        return item

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

        # Close to finish the JSON file
        exporter.close()

        # Verify file is valid Chrome Trace format
        data = _assert_valid_chrome_trace_format(output_file)

        # Should have metadata (2) + events (10 * 4 = 40)
        assert len(data) == 42

    def test_exporter_flush_multiple_times(
        self, mock_stats_item: Mock, tmp_path: Path
    ) -> None:
        """Test that exporter can flush multiple times."""
        output_file = tmp_path / "trace.json"
        exporter = TraceExporter(
            pid=12345, output_path=output_file, flush_threshold=5
        )

        # Add 15 stats_items (creates 60 events, triggers flushes)
        for _ in range(15):
            exporter.add_event(mock_stats_item)

        assert output_file.exists()

        # Close to finish the JSON file
        exporter.close()

        # Verify file is valid Chrome Trace format
        data = _assert_valid_chrome_trace_format(output_file)

        # Should have metadata (2) + all events (15 * 4 = 60)
        assert len(data) == 62

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

        # Verify file is valid Chrome Trace format
        data = _assert_valid_chrome_trace_format(output_file)

        # Should have metadata (2) + events (4 per stats_item)
        assert len(data) == 6

    def test_exporter_close_writes_all_events(
        self, mock_stats_item: Mock, tmp_path: Path
    ) -> None:
        """Test that close() writes all events including flushed ones."""
        output_file = tmp_path / "trace.json"
        exporter = TraceExporter(
            pid=12345, output_path=output_file, flush_threshold=5
        )

        # Add 15 stats_items (creates 60 events, triggers flushes at 5, 10, 15...)
        for _ in range(15):
            exporter.add_event(mock_stats_item)

        # Close should write remaining events and finish marker
        exporter.close()

        # Verify file is valid Chrome Trace format
        data = _assert_valid_chrome_trace_format(output_file)

        # Should have metadata (2) + all events (15 * 4 = 60)
        assert len(data) == 62

    def test_add_event_creates_complete_and_counter(
        self, mock_stats_item: Mock, tmp_path: Path
    ) -> None:
        """Test that add_event creates 4 events per stats_item."""
        exporter = TraceExporter(pid=12345, output_path=tmp_path / "trace.json")
        exporter.add_event(mock_stats_item)

        # Should have 4 events per stats_item
        assert exporter.get_event_count() == 4

    def test_add_event_timestamp_conversion(
        self, mock_stats_item: Mock, tmp_path: Path
    ) -> None:
        """Test timestamp conversion to microseconds."""
        exporter = TraceExporter(pid=12345, output_path=tmp_path / "test.json")
        exporter.add_event(mock_stats_item)
        exporter.close()

        # Verify file is valid Chrome Trace format
        data = _assert_valid_chrome_trace_format(tmp_path / "test.json")

        # First 2 events are metadata, then our events
        events = data[2:]

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

        # Verify file is valid Chrome Trace format
        data = _assert_valid_chrome_trace_format(tmp_path / "test.json")

        # First 2 events are metadata, then our events
        event = data[2]

        assert event["name"] == "GC Pause"
        assert event["cat"] == "gc.pause"
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

        # Verify file is valid Chrome Trace format
        data = _assert_valid_chrome_trace_format(tmp_path / "test.json")

        # First 2 events are metadata, then our events
        # Events are: GC Pause, GC Pause (gen=X), Memory Counters, Heap Size
        event = data[4]

        assert "Memory Counters" in event["name"]
        assert "gc.memory" in event["cat"]
        assert event["ph"] == "C"
        assert event["pid"] == 12345
        assert event["tid"] == "GC Monitor"
        assert event["args"]["heap_size"] == 52428800
        assert event["args"]["object_visits"] == 600

    def test_close_adds_metadata(self, mock_stats_item: Mock, tmp_path: Path) -> None:
        """Test that close() automatically adds metadata."""
        exporter = TraceExporter(pid=12345, output_path=tmp_path / "test_trace.json")
        exporter.add_event(mock_stats_item)
        exporter.close()

        # Verify file is valid Chrome Trace format
        data = _assert_valid_chrome_trace_format(tmp_path / "test_trace.json")

        # Find metadata events
        metadata_events = [e for e in data if e["ph"] == "M"]
        assert len(metadata_events) == 2

        process_name = next(
            e for e in metadata_events if e["name"] == "process_name"
        )
        assert f"PID: {12345}" in process_name["args"]["name"]

        thread_name = next(e for e in metadata_events if e["name"] == "thread_name")
        assert thread_name["args"]["name"] == "GC Monitor"

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

        # Verify file is valid Chrome Trace format
        data = _assert_valid_chrome_trace_format(tmp_path / "test_trace.json")

        # Metadata should only appear once
        metadata_events = [e for e in data if e["ph"] == "M"]
        assert len(metadata_events) == 2

    def test_different_generation_events(self, tmp_path: Path) -> None:
        """Test events with different GC generations."""
        exporter = TraceExporter(pid=12345, output_path=tmp_path / "test_trace.json")

        for gen in range(3):
            item = Mock()
            item.gen = gen
            item.ts = 1_000_000_000 + gen * 100_000_000  # int, nanoseconds
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

        # Verify file is valid Chrome Trace format
        data = _assert_valid_chrome_trace_format(tmp_path / "test_trace.json")

        # Find complete events (2 per gen: "GC Pause" and "GC Pause (gen=X)")
        complete_events = [e for e in data if e["ph"] == "X"]
        assert len(complete_events) == 6  # 3 generations * 2 events each

        generations = {e["args"]["generation"] for e in complete_events}
        assert generations == {0, 1, 2}

        # Check event names
        event_names = {e["name"] for e in complete_events}
        assert event_names == {
            "GC Pause",
            "GC Pause (gen=0)",
            "GC Pause (gen=1)",
            "GC Pause (gen=2)",
        }


class TestGCMonitorStreaming:
    """Tests for GCMonitor streaming functionality."""

    @pytest.fixture
    def mock_handler(self) -> Mock:
        """Create a mock GCMonitorHandler.
        
        Returns batches of 2 events per read with incrementing timestamps
        to simulate real GC monitoring data.
        
        Note: ts is in nanoseconds (int), duration and total_duration are in seconds (float).
        """
        handler = Mock(spec=GCMonitorHandler)
        handler._connected = True
        
        # Track read calls to generate incrementing timestamps
        read_count = [0]
        
        def read_side_effect() -> list[Mock]:
            """Generate events with incrementing timestamps on each read."""
            base_ts = read_count[0] * 100 + 1  # Increment timestamp for each read, start from 1
            read_count[0] += 1
            
            # Return batch of 2 events per read
            item1 = Mock()
            item1.gen = 0
            item1.ts = base_ts  # int, nanoseconds
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
            item2.ts = base_ts + 1  # Slightly different timestamp
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
            
            return [item1, item2]
        
        handler.read.side_effect = read_side_effect
        return handler

    def test_gcmonitor_streams_each_event_to_exporter(
        self, mock_handler: Mock, tmp_path: Path
    ) -> None:
        """Test that GCMonitor streams each event from read() to exporter immediately."""
        output_file = tmp_path / "trace.json"
        exporter = TraceExporter(pid=12345, output_path=output_file)

        monitor = GCMonitor(mock_handler, exporter, rate=0.05)

        # Let it run for a bit to process events
        time.sleep(0.2)
        monitor.stop()

        # File should be created with events
        assert output_file.exists()
        
        # Verify file is valid Chrome Trace format
        data = _assert_valid_chrome_trace_format(output_file)
        
        # Should have metadata (2) + events (at least 2 reads * 2 stats_items * 4 events)
        assert len(data) >= 10  # 2 metadata + 8 events minimum

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
            original_add(item)

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

        # Verify file is valid Chrome Trace format
        data = _assert_valid_chrome_trace_format(output_file)

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
        item.ts = 1_000_000_000  # 1 second in nanoseconds (int)
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
        output_file = tmp_path / "trace.json"
        exporter = TraceExporter(pid=12345, output_path=output_file)

        monitor = GCMonitor(handler, exporter, rate=0.05)
        # Should not raise, just stop on error
        time.sleep(0.15)
        monitor.stop()

        # File should be created with events
        assert output_file.exists()
        
        # Verify file is valid Chrome Trace format
        data = _assert_valid_chrome_trace_format(output_file)
        
        # Should have metadata (2) + events (4 events per stats_item)
        assert len(data) >= 6  # 2 metadata + 4 events
