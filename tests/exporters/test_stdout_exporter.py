"""Tests for the StdoutExporter."""

import json
from typing import Any

import pytest

from gc_monitor.lock_strategy import NoLock
from gc_monitor.protocol import TGCStatsInfo
from gc_monitor.exporters import StdoutExporter

from tests.conftest import DEFAULT_PID
from tests.helpers import create_mock_stats_item


class TestStdoutExporter:
    """Tests for StdoutExporter class."""

    def test_init_default_parameters(self) -> None:
        """Test StdoutExporter initialization with default parameters."""
        exporter = StdoutExporter(NoLock)

        # Test observable behavior instead of protected attributes
        assert exporter.get_event_count() == 0

    def test_init_custom_parameters(self) -> None:
        """Test StdoutExporter initialization with custom parameters."""
        exporter = StdoutExporter(NoLock, flush_threshold=50)

        # Test observable behavior instead of protected attributes
        assert exporter.get_event_count() == 0

    def test_add_event_json_output_format(self, mock_stats_item: TGCStatsInfo, capsys: pytest.CaptureFixture[str]) -> None:
        """Test that add_event outputs correct JSON format to stdout."""
        exporter = StdoutExporter(NoLock)
        exporter.add_event(DEFAULT_PID, mock_stats_item)
        exporter.close()

        captured = capsys.readouterr()
        output = captured.out.strip()

        # Should be valid JSON
        data: dict[str, Any] = json.loads(output)

        # Verify all fields are present
        assert data["pid"] == 12345
        assert data["tid"] == 0
        assert data["gen"] == 0
        assert data["ts_start"] == 1_500_000_000
        assert data["collections"] == 50
        assert data["collected"] == 200
        assert data["uncollectable"] == 10
        assert data["candidates"] == 40
        assert data["heap_size"] == 52428800
        assert data["duration"] == 0.005

    def test_add_event_increments_event_count(self, mock_stats_item: TGCStatsInfo, capsys: pytest.CaptureFixture[str]) -> None:
        """Test that add_event increments the event count."""
        exporter = StdoutExporter(NoLock)

        assert exporter.get_event_count() == 0

        exporter.add_event(DEFAULT_PID, mock_stats_item)
        assert exporter.get_event_count() == 1

        exporter.add_event(DEFAULT_PID, mock_stats_item)
        assert exporter.get_event_count() == 2

    def test_add_event_multiple_events(self, mock_stats_item_batch: list[TGCStatsInfo], capsys: pytest.CaptureFixture[str]) -> None:
        """Test output with multiple events."""
        exporter = StdoutExporter(NoLock)

        for item in mock_stats_item_batch:
            exporter.add_event(DEFAULT_PID, item)
        exporter.close()

        captured = capsys.readouterr()
        lines = captured.out.strip().split("\n")

        # Should have 3 lines (one per event)
        assert len(lines) == 3

        # Verify each line is valid JSON with correct generation
        for i, line in enumerate(lines):
            data: dict[str, Any] = json.loads(line)
            assert data["gen"] == i

    def test_close_with_flush(self, mock_stats_item: TGCStatsInfo, capsys: pytest.CaptureFixture[str]) -> None:
        """Test close() flushes stdout."""
        exporter = StdoutExporter(NoLock, flush_threshold=1000)
        exporter.add_event(DEFAULT_PID, mock_stats_item)

        # Close should flush stdout
        exporter.close()

        # Event count should remain the same
        assert exporter.get_event_count() == 1

    def test_get_event_count_accuracy(self, mock_stats_item: TGCStatsInfo, capsys: pytest.CaptureFixture[str]) -> None:
        """Test get_event_count returns accurate count."""
        exporter = StdoutExporter(NoLock)

        # Initial count should be 0
        assert exporter.get_event_count() == 0

        # Add events and verify count
        for i in range(10):
            exporter.add_event(DEFAULT_PID, mock_stats_item)
            assert exporter.get_event_count() == i + 1

    def test_add_event_output_to_stdout(self, mock_stats_item: TGCStatsInfo, capsys: pytest.CaptureFixture[str]) -> None:
        """Test that add_event writes to stdout (not stderr)."""
        exporter = StdoutExporter(NoLock)
        exporter.add_event(DEFAULT_PID, mock_stats_item)
        exporter.close()

        captured = capsys.readouterr()

        # Output should be in stdout
        assert captured.out != ""
        # stderr should be empty
        assert captured.err == ""

    def test_add_event_json_is_single_line(self, mock_stats_item: TGCStatsInfo, capsys: pytest.CaptureFixture[str]) -> None:
        """Test that each event is written as a single JSON line."""
        exporter = StdoutExporter(NoLock)
        exporter.add_event(DEFAULT_PID, mock_stats_item)
        exporter.close()

        captured = capsys.readouterr()
        output = captured.out

        # Should be exactly one line (plus newline)
        lines = output.strip().split("\n")
        assert len(lines) == 1

        # Should be valid JSON
        data: dict[str, Any] = json.loads(output.strip())
        assert isinstance(data, dict)

    def test_thread_id_in_output(self, mock_stats_item: TGCStatsInfo, capsys: pytest.CaptureFixture[str]) -> None:
        """Test that thread ID appears in output."""
        exporter = StdoutExporter(NoLock)
        stats_item_with_tid = create_mock_stats_item(iid=42)
        exporter.add_event(DEFAULT_PID, stats_item_with_tid)
        exporter.close()

        captured = capsys.readouterr()
        data: dict[str, Any] = json.loads(captured.out.strip())

        assert data["tid"] == 42

    def test_pid_in_output(self, mock_stats_item: TGCStatsInfo, capsys: pytest.CaptureFixture[str]) -> None:
        """Test that PID appears in output."""
        exporter = StdoutExporter(NoLock)
        exporter.add_event(99999, mock_stats_item)
        exporter.close()

        captured = capsys.readouterr()
        data: dict[str, Any] = json.loads(captured.out.strip())

        assert data["pid"] == 99999
