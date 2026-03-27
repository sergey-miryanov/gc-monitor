"""Tests for the JSONL file exporter."""

import json
from pathlib import Path

from gc_monitor.jsonl_exporter import JsonlExporter

from tests.helpers import create_mock_stats_item


class TestJsonlExporter:
    """Test suite for JsonlExporter class."""

    def test_init_default_parameters(self, tmp_path: Path) -> None:
        """Test initialization with default parameters."""
        output_path = tmp_path / "test.jsonl"
        exporter = JsonlExporter(pid=12345, output_path=output_path)

        assert exporter._pid == 12345
        assert exporter._thread_id == 0
        assert exporter._flush_threshold == 100
        assert exporter._event_count == 0
        assert exporter._events == []

    def test_init_custom_parameters(self, tmp_path: Path) -> None:
        """Test initialization with custom parameters."""
        output_path = tmp_path / "custom.jsonl"
        exporter = JsonlExporter(
            pid=67890,
            output_path=output_path,
            thread_id=1234,
            flush_threshold=50,
        )

        assert exporter._pid == 67890
        assert exporter._thread_id == 1234
        assert exporter._flush_threshold == 50
        assert exporter._event_count == 0

    def test_add_event_json_output_format(self, tmp_path: Path) -> None:
        """Test that add_event writes correct JSON format."""
        output_path = tmp_path / "test.jsonl"
        exporter = JsonlExporter(pid=12345, output_path=output_path, flush_threshold=1)

        stats_item = create_mock_stats_item(
            gen=0,
            ts=1_000_000,
            collections=10,
            collected=5,
            uncollectable=0,
            candidates=15,
            object_visits=100,
            objects_transitively_reachable=50,
            objects_not_transitively_reachable=30,
            heap_size=1024,
            work_to_do=5,
            duration=0.001,
            total_duration=0.005,
        )

        exporter.add_event(stats_item)
        exporter.close()

        # Read the file and verify the content
        with open(output_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

        assert len(lines) == 1
        event = json.loads(lines[0])

        assert event["pid"] == 12345
        assert event["tid"] == 0
        assert event["gen"] == 0
        # JSONL format stores raw values (no conversion)
        assert event["ts"] == 1000000  # nanoseconds (raw)
        assert event["collections"] == 10
        assert event["collected"] == 5
        assert event["uncollectable"] == 0
        assert event["candidates"] == 15
        assert event["object_visits"] == 100
        assert event["objects_transitively_reachable"] == 50
        assert event["objects_not_transitively_reachable"] == 30
        assert event["heap_size"] == 1024
        assert event["work_to_do"] == 5
        # duration is in seconds (raw)
        assert event["duration"] == 0.001
        assert event["total_duration"] == 0.005

    def test_add_event_increments_event_count(self, tmp_path: Path) -> None:
        """Test that add_event increments the event counter."""
        output_path = tmp_path / "test.jsonl"
        exporter = JsonlExporter(pid=12345, output_path=output_path, flush_threshold=1000)

        stats_item = create_mock_stats_item()

        assert exporter.get_event_count() == 0

        exporter.add_event(stats_item)
        assert exporter.get_event_count() == 1

        exporter.add_event(stats_item)
        assert exporter.get_event_count() == 2

        exporter.close()

    def test_add_event_multiple_events(self, tmp_path: Path) -> None:
        """Test that multiple events are written as separate lines."""
        output_path = tmp_path / "test.jsonl"
        exporter = JsonlExporter(pid=12345, output_path=output_path, flush_threshold=1000)

        stats_item = create_mock_stats_item()

        # Add 3 events
        exporter.add_event(stats_item)
        exporter.add_event(stats_item)
        exporter.add_event(stats_item)
        exporter.close()

        # Read the file and verify the content
        with open(output_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

        assert len(lines) == 3
        # Each line should be valid JSON
        for line in lines:
            event = json.loads(line)
            assert event["pid"] == 12345

    def test_close_with_flush_true(self, tmp_path: Path) -> None:
        """Test close() flushes remaining events."""
        output_path = tmp_path / "test.jsonl"
        exporter = JsonlExporter(pid=12345, output_path=output_path, flush_threshold=1000)

        stats_item = create_mock_stats_item()

        exporter.add_event(stats_item)
        exporter.close()

        # File should exist and have content
        assert output_path.exists()
        with open(output_path, "r", encoding="utf-8") as f:
            content = f.read()
        assert len(content) > 0

    def test_close_with_remaining_events(self, tmp_path: Path) -> None:
        """Test close() flushes any remaining buffered events."""
        output_path = tmp_path / "test.jsonl"
        # Set threshold high so events are buffered
        exporter = JsonlExporter(pid=12345, output_path=output_path, flush_threshold=1000)

        stats_item = create_mock_stats_item()

        # Add events but don't reach threshold
        exporter.add_event(stats_item)
        exporter.add_event(stats_item)

        # Events should still be buffered (not flushed yet)
        assert len(exporter._events) == 2

        # close() should flush remaining events
        exporter.close()

        # File should exist and have 2 events
        assert output_path.exists()
        with open(output_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        assert len(lines) == 2

    def test_get_event_count_accuracy(self, tmp_path: Path) -> None:
        """Test that get_event_count returns accurate count."""
        output_path = tmp_path / "test.jsonl"
        exporter = JsonlExporter(pid=12345, output_path=output_path, flush_threshold=1000)

        stats_item = create_mock_stats_item()

        # Add 5 events
        for _ in range(5):
            exporter.add_event(stats_item)

        assert exporter.get_event_count() == 5
        exporter.close()

    def test_add_event_output_to_file(self, tmp_path: Path) -> None:
        """Test that add_event writes to the correct file."""
        output_path = tmp_path / "test.jsonl"
        exporter = JsonlExporter(pid=12345, output_path=output_path, flush_threshold=1)

        stats_item = create_mock_stats_item()

        exporter.add_event(stats_item)
        exporter.close()

        # Verify file exists and has content
        assert output_path.exists()
        with open(output_path, "r", encoding="utf-8") as f:
            content = f.read()
        assert len(content) > 0

    def test_add_event_json_is_single_line(self, tmp_path: Path) -> None:
        """Test that each event is written as a single line (no newlines in JSON)."""
        output_path = tmp_path / "test.jsonl"
        exporter = JsonlExporter(pid=12345, output_path=output_path, flush_threshold=1000)

        stats_item = create_mock_stats_item()

        exporter.add_event(stats_item)
        exporter.add_event(stats_item)
        exporter.close()

        # Read the file and verify each line is valid JSON
        with open(output_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

        assert len(lines) == 2
        for line in lines:
            # Each line should be valid JSON without trailing newlines in the JSON itself
            event = json.loads(line.strip())
            assert "pid" in event

    def test_thread_id_in_output(self, tmp_path: Path) -> None:
        """Test that custom thread ID appears in output."""
        output_path = tmp_path / "test.jsonl"
        exporter = JsonlExporter(
            pid=12345, output_path=output_path, thread_id=5678, flush_threshold=1
        )

        stats_item = create_mock_stats_item()

        exporter.add_event(stats_item)
        exporter.close()

        with open(output_path, "r", encoding="utf-8") as f:
            event = json.loads(f.readline())

        assert event["tid"] == 5678

    def test_pid_in_output(self, tmp_path: Path) -> None:
        """Test that PID appears in output."""
        output_path = tmp_path / "test.jsonl"
        exporter = JsonlExporter(pid=99999, output_path=output_path, flush_threshold=1)

        stats_item = create_mock_stats_item()

        exporter.add_event(stats_item)
        exporter.close()

        with open(output_path, "r", encoding="utf-8") as f:
            event = json.loads(f.readline())

        assert event["pid"] == 99999

    def test_close_multiple_calls_safe(self, tmp_path: Path) -> None:
        """Test that calling close() multiple times is safe."""
        output_path = tmp_path / "test.jsonl"
        exporter = JsonlExporter(pid=12345, output_path=output_path, flush_threshold=1)

        stats_item = create_mock_stats_item()

        exporter.add_event(stats_item)

        # First close
        exporter.close()

        # Second close should be safe (no error)
        exporter.close()

        # File should have exactly 1 event
        with open(output_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        assert len(lines) == 1

    def test_add_event_after_close(self, tmp_path: Path) -> None:
        """Test that add_event after close() still works (events are buffered again)."""
        output_path = tmp_path / "test.jsonl"
        exporter = JsonlExporter(pid=12345, output_path=output_path, flush_threshold=1000)

        stats_item = create_mock_stats_item()

        exporter.add_event(stats_item)
        exporter.close()

        # Add another event after close (this is allowed, events are buffered)
        exporter.add_event(stats_item)

        # Count should be 2
        assert exporter.get_event_count() == 2

        # Close again to flush
        exporter.close()

        # File should have 2 events
        with open(output_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        assert len(lines) == 2


class TestJsonlExporterFlushThreshold:
    """Test suite for JsonlExporter flush threshold behavior."""

    def test_flush_threshold_default_value(self, tmp_path: Path) -> None:
        """Test that default flush threshold is 100."""
        output_path = tmp_path / "test.jsonl"
        exporter = JsonlExporter(pid=12345, output_path=output_path)

        assert exporter._flush_threshold == 100

    def test_flush_threshold_custom_value(self, tmp_path: Path) -> None:
        """Test that custom flush threshold is respected."""
        output_path = tmp_path / "test.jsonl"
        exporter = JsonlExporter(pid=12345, output_path=output_path, flush_threshold=50)

        assert exporter._flush_threshold == 50

    def test_events_buffered_until_threshold(self, tmp_path: Path) -> None:
        """Test that events are buffered until threshold is reached."""
        output_path = tmp_path / "test.jsonl"
        # Set threshold to 10
        exporter = JsonlExporter(pid=12345, output_path=output_path, flush_threshold=10)

        stats_item = create_mock_stats_item()

        # Add 5 events (below threshold)
        for _ in range(5):
            exporter.add_event(stats_item)

        # File should not exist yet (events still buffered)
        # Note: file may be created on first flush, so check if it's empty
        if output_path.exists():
            with open(output_path, "r", encoding="utf-8") as f:
                content = f.read()
            assert len(content) == 0 or len(f.readlines()) == 0

        # Add 5 more events (now at threshold)
        for _ in range(5):
            exporter.add_event(stats_item)

        # Now file should have 10 events
        with open(output_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        assert len(lines) == 10

    def test_flush_on_threshold_reached(self, tmp_path: Path) -> None:
        """Test that flush occurs exactly when threshold is reached."""
        output_path = tmp_path / "test.jsonl"
        exporter = JsonlExporter(pid=12345, output_path=output_path, flush_threshold=5)

        stats_item = create_mock_stats_item()

        # Add 4 events (below threshold)
        for i in range(4):
            exporter.add_event(stats_item)
            # File should not exist or be empty
            if output_path.exists():
                with open(output_path, "r", encoding="utf-8") as f:
                    lines = f.readlines()
                assert len(lines) == 0

        # Add 5th event (at threshold)
        exporter.add_event(stats_item)

        # File should now have 5 events
        with open(output_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        assert len(lines) == 5

    def test_multiple_flushes(self, tmp_path: Path) -> None:
        """Test that multiple flush cycles work correctly."""
        output_path = tmp_path / "test.jsonl"
        exporter = JsonlExporter(pid=12345, output_path=output_path, flush_threshold=3)

        stats_item = create_mock_stats_item()

        # Add 7 events (should trigger 2 flushes: at 3 and 6, with 1 buffered)
        for _ in range(7):
            exporter.add_event(stats_item)

        # File should have 6 events (2 flushes of 3 each)
        with open(output_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        assert len(lines) == 6

        # Close to flush remaining 1 event
        exporter.close()

        # Now file should have 7 events
        with open(output_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        assert len(lines) == 7

    def test_close_flushes_remaining_buffered_events(self, tmp_path: Path) -> None:
        """Test that close() flushes any remaining buffered events."""
        output_path = tmp_path / "test.jsonl"
        exporter = JsonlExporter(pid=12345, output_path=output_path, flush_threshold=100)

        stats_item = create_mock_stats_item()

        # Add 10 events (below threshold)
        for _ in range(10):
            exporter.add_event(stats_item)

        # File should not exist or be empty
        if output_path.exists():
            with open(output_path, "r", encoding="utf-8") as f:
                content = f.read()
            assert len(content) == 0

        # Close should flush all 10 events
        exporter.close()

        # File should now have 10 events
        with open(output_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        assert len(lines) == 10

    def test_get_event_count_includes_buffered_and_flushed(self, tmp_path: Path) -> None:
        """Test that get_event_count returns total events (buffered + flushed)."""
        output_path = tmp_path / "test.jsonl"
        exporter = JsonlExporter(pid=12345, output_path=output_path, flush_threshold=5)

        stats_item = create_mock_stats_item()

        # Add 12 events (2 flushes of 5, plus 2 buffered)
        for _ in range(12):
            exporter.add_event(stats_item)

        # Count should be 12 (even though only 10 are flushed)
        assert exporter.get_event_count() == 12

        # Close to flush remaining
        exporter.close()

        # Count should still be 12
        assert exporter.get_event_count() == 12

        # File should have 12 events
        with open(output_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        assert len(lines) == 12

    def test_threshold_one(self, tmp_path: Path) -> None:
        """Test that threshold=1 flushes immediately after each event."""
        output_path = tmp_path / "test.jsonl"
        exporter = JsonlExporter(pid=12345, output_path=output_path, flush_threshold=1)

        stats_item = create_mock_stats_item()

        # Add first event
        exporter.add_event(stats_item)

        # File should have 1 event immediately
        with open(output_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        assert len(lines) == 1

        # Add second event
        exporter.add_event(stats_item)

        # File should have 2 events immediately
        with open(output_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        assert len(lines) == 2
