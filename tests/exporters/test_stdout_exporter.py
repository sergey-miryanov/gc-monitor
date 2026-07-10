"""Tests for the StdoutExporter."""

import json
import sys
from typing import Any

import pytest

from gcmon.exporters import StdoutExporter
from gcmon.protocol import TGCStatsInfo
from tests.conftest import DEFAULT_PID
from tests.helpers import create_mock_stats_item


class TestStdoutExporter:
    """Tests for StdoutExporter class."""

    def test_init_default_parameters(self) -> None:
        """Test StdoutExporter initialization with default parameters."""
        exporter = StdoutExporter()
        assert exporter._flush_threshold == 100
        assert exporter._output is sys.stdout

    def test_init_custom_parameters(self) -> None:
        """Test StdoutExporter initialization with custom parameters."""
        exporter = StdoutExporter(flush_threshold=50)
        assert exporter._flush_threshold == 50
        assert exporter._output is sys.stdout

    def test_add_event_json_output_format(
        self, mock_stats_item: TGCStatsInfo, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Test that add_event outputs correct JSON format to stdout."""
        exporter = StdoutExporter()
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

    def test_add_event_multiple_events(
        self, mock_stats_item_batch: list[TGCStatsInfo], capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Test output with multiple events."""
        exporter = StdoutExporter()

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
        exporter = StdoutExporter(flush_threshold=1000)
        exporter.add_event(DEFAULT_PID, mock_stats_item)
        exporter.close()
        captured = capsys.readouterr()
        assert captured.out != ""

    def test_add_event_output_to_stdout(
        self, mock_stats_item: TGCStatsInfo, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Test that add_event writes to stdout (not stderr)."""
        exporter = StdoutExporter()
        exporter.add_event(DEFAULT_PID, mock_stats_item)
        exporter.close()

        captured = capsys.readouterr()

        # Output should be in stdout
        assert captured.out != ""
        # stderr should be empty
        assert captured.err == ""

    def test_add_event_json_is_single_line(
        self, mock_stats_item: TGCStatsInfo, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Test that each event is written as a single JSON line."""
        exporter = StdoutExporter()
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
        exporter = StdoutExporter()
        stats_item_with_tid = create_mock_stats_item(iid=42)
        exporter.add_event(DEFAULT_PID, stats_item_with_tid)
        exporter.close()

        captured = capsys.readouterr()
        data: dict[str, Any] = json.loads(captured.out.strip())

        assert data["tid"] == 42

    def test_pid_in_output(self, mock_stats_item: TGCStatsInfo, capsys: pytest.CaptureFixture[str]) -> None:
        """Test that PID appears in output."""
        exporter = StdoutExporter()
        exporter.add_event(99999, mock_stats_item)
        exporter.close()

        captured = capsys.readouterr()
        data: dict[str, Any] = json.loads(captured.out.strip())

        assert data["pid"] == 99999
