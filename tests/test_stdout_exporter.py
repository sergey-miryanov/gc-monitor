"""Tests for the StdoutExporter."""

import json
from typing import Any
from unittest.mock import Mock

import pytest

from gc_monitor.stdout_exporter import StdoutExporter


class TestStdoutExporter:
    """Tests for StdoutExporter class."""

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

    def test_init_default_parameters(self) -> None:
        """Test StdoutExporter initialization with default parameters."""
        exporter = StdoutExporter(pid=12345)

        # Test observable behavior instead of protected attributes
        assert exporter.get_event_count() == 0

    def test_init_custom_parameters(self) -> None:
        """Test StdoutExporter initialization with custom parameters."""
        exporter = StdoutExporter(
            pid=12345, thread_name="Custom Thread", flush=False
        )

        # Test observable behavior instead of protected attributes
        assert exporter.get_event_count() == 0

    def test_add_event_json_output_format(self, mock_stats_item: Mock, capsys: pytest.CaptureFixture[str]) -> None:
        """Test that add_event outputs correct JSON format to stdout."""
        exporter = StdoutExporter(pid=12345)
        exporter.add_event(mock_stats_item)

        captured = capsys.readouterr()
        output = captured.out.strip()

        # Should be valid JSON
        data: dict[str, Any] = json.loads(output)

        # Verify all fields are present
        assert data["pid"] == 12345
        assert data["tid"] == "GC Monitor"
        assert data["gen"] == 2
        assert data["ts"] == 1_500_000_000
        assert data["collections"] == 50
        assert data["collected"] == 200
        assert data["uncollectable"] == 10
        assert data["candidates"] == 40
        assert data["object_visits"] == 600
        assert data["objects_transitively_reachable"] == 250
        assert data["objects_not_transitively_reachable"] == 150
        assert data["heap_size"] == 52428800
        assert data["work_to_do"] == 30
        assert data["duration"] == 0.005
        assert data["total_duration"] == 45.5

    def test_add_event_increments_event_count(self, mock_stats_item: Mock, capsys: pytest.CaptureFixture[str]) -> None:
        """Test that add_event increments the event count."""
        exporter = StdoutExporter(pid=12345)

        assert exporter.get_event_count() == 0

        exporter.add_event(mock_stats_item)
        assert exporter.get_event_count() == 1

        exporter.add_event(mock_stats_item)
        assert exporter.get_event_count() == 2

    def test_add_event_multiple_events(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Test output with multiple events."""
        exporter = StdoutExporter(pid=12345)

        # Create multiple mock items with different generations
        for gen in range(3):
            item = Mock()
            item.gen = gen
            item.ts = 1_000_000_000 + gen * 100_000_000
            item.collections = 10 * (gen + 1)
            item.collected = 50 * (gen + 1)
            item.uncollectable = gen
            item.candidates = 20 * (gen + 1)
            item.object_visits = 100 * (gen + 1)
            item.objects_transitively_reachable = 50 * (gen + 1)
            item.objects_not_transitively_reachable = 30 * (gen + 1)
            item.heap_size = 1_000_000 * (gen + 1)
            item.work_to_do = 5 * (gen + 1)
            item.duration = 0.001 * (gen + 1)
            item.total_duration = 1.0 * (gen + 1)
            exporter.add_event(item)

        captured = capsys.readouterr()
        lines = captured.out.strip().split("\n")

        # Should have 3 lines (one per event)
        assert len(lines) == 3

        # Verify each line is valid JSON with correct generation
        for i, line in enumerate(lines):
            data: dict[str, Any] = json.loads(line)
            assert data["gen"] == i

    def test_close_with_flush_true(self, mock_stats_item: Mock, capsys: pytest.CaptureFixture[str]) -> None:
        """Test close() with flush=True."""
        exporter = StdoutExporter(pid=12345, flush=True)
        exporter.add_event(mock_stats_item)

        # Close should flush stdout
        exporter.close()

        # Event count should remain the same
        assert exporter.get_event_count() == 1

    def test_close_with_flush_false(self, mock_stats_item: Mock, capsys: pytest.CaptureFixture[str]) -> None:
        """Test close() with flush=False."""
        exporter = StdoutExporter(pid=12345, flush=False)
        exporter.add_event(mock_stats_item)

        # Close should not flush when flush=False
        exporter.close()

        # Event count should remain the same
        assert exporter.get_event_count() == 1

    def test_get_event_count_accuracy(self, mock_stats_item: Mock, capsys: pytest.CaptureFixture[str]) -> None:
        """Test get_event_count returns accurate count."""
        exporter = StdoutExporter(pid=12345)

        # Initial count should be 0
        assert exporter.get_event_count() == 0

        # Add events and verify count
        for i in range(10):
            exporter.add_event(mock_stats_item)
            assert exporter.get_event_count() == i + 1

    def test_add_event_output_to_stdout(self, mock_stats_item: Mock, capsys: pytest.CaptureFixture[str]) -> None:
        """Test that add_event writes to stdout (not stderr)."""
        exporter = StdoutExporter(pid=12345)
        exporter.add_event(mock_stats_item)

        captured = capsys.readouterr()

        # Output should be in stdout
        assert captured.out != ""
        # stderr should be empty
        assert captured.err == ""

    def test_add_event_json_is_single_line(self, mock_stats_item: Mock, capsys: pytest.CaptureFixture[str]) -> None:
        """Test that each event is written as a single JSON line."""
        exporter = StdoutExporter(pid=12345)
        exporter.add_event(mock_stats_item)

        captured = capsys.readouterr()
        output = captured.out

        # Should be exactly one line (plus newline)
        lines = output.strip().split("\n")
        assert len(lines) == 1

        # Should be valid JSON
        data: dict[str, Any] = json.loads(output.strip())
        assert isinstance(data, dict)

    def test_thread_name_in_output(self, mock_stats_item: Mock, capsys: pytest.CaptureFixture[str]) -> None:
        """Test that custom thread name appears in output."""
        exporter = StdoutExporter(pid=12345, thread_name="MyCustomThread")
        exporter.add_event(mock_stats_item)

        captured = capsys.readouterr()
        data: dict[str, Any] = json.loads(captured.out.strip())

        assert data["tid"] == "MyCustomThread"

    def test_pid_in_output(self, mock_stats_item: Mock, capsys: pytest.CaptureFixture[str]) -> None:
        """Test that PID appears in output."""
        exporter = StdoutExporter(pid=99999)
        exporter.add_event(mock_stats_item)

        captured = capsys.readouterr()
        data: dict[str, Any] = json.loads(captured.out.strip())

        assert data["pid"] == 99999
