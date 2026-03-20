"""Tests for Chrome trace exporter."""

import json
import tempfile
import threading
from pathlib import Path
from unittest.mock import Mock

import pytest

from gc_monitor.chrome_trace_exporter import TraceExporter


class TestTraceExporter:
    """Tests for TraceExporter class."""

    @pytest.fixture
    def mock_stats_item(self) -> Mock:
        """Create a mock GCMonitorStatsItem."""
        item = Mock()
        item.gen = 2
        item.ts = 1.5
        item.collections = 50
        item.collected = 200
        item.uncollectable = 10
        item.candidates = 40
        item.object_visits = 600
        item.objects_transitively_reachable = 250
        item.objects_not_transitively_reachable = 150
        item.heap_size = 52428800
        item.work_to_do = 30
        item.duration = 0.005
        item.total_duration = 45.5
        return item

    def test_exporter_init(self) -> None:
        """Test exporter initialization."""
        exporter = TraceExporter(pid=12345)

        assert exporter._pid == 12345
        assert exporter._thread_name == "GC Monitor"
        assert exporter.get_event_count() == 0

    def test_add_event_creates_complete_and_counter(
        self, mock_stats_item: Mock
    ) -> None:
        """Test that add_event creates both complete and counter events."""
        exporter = TraceExporter(pid=12345)
        exporter.add_event(mock_stats_item)

        # Should have 2 events: 1 complete + 1 counter
        assert exporter.get_event_count() == 2

    def test_add_event_timestamp_conversion(
        self, mock_stats_item: Mock
    ) -> None:
        """Test timestamp conversion to microseconds."""
        exporter = TraceExporter(pid=12345)
        exporter.add_event(mock_stats_item)

        events = exporter._events
        complete_event = events[0]
        counter_event = events[1]

        # ts = 1.5 seconds -> 1500000 microseconds
        assert complete_event["ts"] == 1500000
        assert counter_event["ts"] == 1500000

        # duration = 0.005 seconds -> 5000 microseconds
        assert complete_event["dur"] == 5000

    def test_add_event_complete_event_structure(
        self, mock_stats_item: Mock
    ) -> None:
        """Test complete event structure."""
        exporter = TraceExporter(pid=12345)
        exporter.add_event(mock_stats_item)

        event = exporter._events[0]

        assert event["name"] == "GC Pause (Gen 2)"
        assert event["cat"] == "gc"
        assert event["ph"] == "X"
        assert event["pid"] == 12345
        assert event["tid"] == "GC Monitor"
        assert event["args"]["generation"] == 2
        assert event["args"]["collected"] == 200
        assert event["args"]["uncollectable"] == 10

    def test_add_event_counter_event_structure(
        self, mock_stats_item: Mock
    ) -> None:
        """Test counter event structure."""
        exporter = TraceExporter(pid=12345)
        exporter.add_event(mock_stats_item)

        event = exporter._events[1]

        assert event["name"] == "Memory Counters"
        assert event["cat"] == "gc.memory"
        assert event["ph"] == "C"
        assert event["pid"] == 12345
        assert event["tid"] == "GC Monitor"
        assert event["args"]["heap_size"] == 52428800
        assert event["args"]["collections"] == 50

    def test_save_json(self, mock_stats_item: Mock, tmp_path: Path) -> None:
        """Test saving to JSON file."""
        exporter = TraceExporter(pid=12345)
        exporter.add_event(mock_stats_item)

        output_file = tmp_path / "test_trace.json"
        exporter.save_json(output_file)

        assert output_file.exists()

        with open(output_file) as f:
            data = json.load(f)

        # Should have metadata (2) + events (2)
        assert len(data) == 4
        assert all("ph" in event for event in data)

    def test_save_json_adds_metadata(self, mock_stats_item: Mock, tmp_path: Path) -> None:
        """Test that save_json automatically adds metadata."""
        exporter = TraceExporter(pid=12345)
        exporter.add_event(mock_stats_item)

        output_file = tmp_path / "test_trace.json"
        exporter.save_json(output_file)

        with open(output_file) as f:
            data = json.load(f)

        # Find metadata events
        metadata_events = [e for e in data if e["ph"] == "M"]
        assert len(metadata_events) == 2

        process_name = next(e for e in metadata_events if e["name"] == "process_name")
        assert f"PID: {12345}" in process_name["args"]["name"]

        thread_name = next(e for e in metadata_events if e["name"] == "thread_name")
        assert thread_name["args"]["name"] == "GC Monitor"

    def test_thread_safety(self, mock_stats_item: Mock) -> None:
        """Test thread-safe event addition."""
        exporter = TraceExporter(pid=12345)

        def add_events() -> None:
            for _ in range(100):
                exporter.add_event(mock_stats_item)

        threads = [threading.Thread(target=add_events) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Should have 10 threads * 100 events * 2 (complete + counter) = 2000
        assert exporter.get_event_count() == 2000

    def test_clear(self, mock_stats_item: Mock) -> None:
        """Test clearing events."""
        exporter = TraceExporter(pid=12345)
        exporter.add_event(mock_stats_item)
        exporter.clear()

        assert exporter.get_event_count() == 0
        assert exporter._metadata_added is False

    def test_multiple_save_calls(self, mock_stats_item: Mock, tmp_path: Path) -> None:
        """Test that multiple save calls don't duplicate metadata."""
        exporter = TraceExporter(pid=12345)
        exporter.add_event(mock_stats_item)

        output_file = tmp_path / "test_trace.json"

        # Save multiple times
        exporter.save_json(output_file)
        exporter.save_json(output_file)

        with open(output_file) as f:
            data = json.load(f)

        # Metadata should only appear once
        metadata_events = [e for e in data if e["ph"] == "M"]
        assert len(metadata_events) == 2

    def test_different_generation_events(self, tmp_path: Path) -> None:
        """Test events with different GC generations."""
        exporter = TraceExporter(pid=12345)

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

        output_file = tmp_path / "test_trace.json"
        exporter.save_json(output_file)

        with open(output_file) as f:
            data = json.load(f)

        # Find complete events
        complete_events = [e for e in data if e["ph"] == "X"]
        assert len(complete_events) == 3

        generations = {e["args"]["generation"] for e in complete_events}
        assert generations == {0, 1, 2}

        # Check event names
        event_names = {e["name"] for e in complete_events}
        assert event_names == {"GC Pause (Gen 0)", "GC Pause (Gen 1)", "GC Pause (Gen 2)"}
