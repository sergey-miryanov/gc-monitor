"""Tests for stats_output module."""

from collections.abc import Callable

import pytest

from gc_monitor.data import GCStatsInfo
from gc_monitor.stats import Stats, StreamingStats
from gc_monitor.stats_output import TableFormat, _build_rows, _print_table, print_stats


class TestStatsOutput:
    """Tests for print_stats function."""

    def test_print_stats_empty(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Test print_stats with no data."""
        stats = StreamingStats()
        print_stats(stats)
        captured = capsys.readouterr()
        assert "No GC statistics collected." in captured.out

    def test_print_stats_with_data(
        self,
        capsys: pytest.CaptureFixture[str],
        gc_stats_item_factory: Callable[..., GCStatsInfo],
    ) -> None:
        """Test print_stats with some GC data."""
        stats = StreamingStats()
        item = gc_stats_item_factory(ts_stop=1000)
        stats.update(12345, item)

        print_stats(stats)
        captured = capsys.readouterr()
        assert "GC Pause(0)" in captured.out
        assert "Metric" in captured.out
        assert "Count" in captured.out
        assert "Sum" in captured.out
        assert "Avg" in captured.out
        assert "P50" in captured.out
        assert "P90" in captured.out
        assert "P95" in captured.out
        assert "P99" in captured.out

    def test_print_stats_multiple_generations(
        self,
        capsys: pytest.CaptureFixture[str],
        gc_stats_item_factory: Callable[..., GCStatsInfo],
    ) -> None:
        """Test print_stats with multiple GC generations."""
        stats = StreamingStats()

        for gen in range(3):
            item = gc_stats_item_factory(
                gen=gen,
                ts_stop=1000 * (gen + 1),
            )
            stats.update(12345, item)

        print_stats(stats)
        captured = capsys.readouterr()

        assert "GC Pause(0)" in captured.out
        assert "GC Pause(1)" in captured.out
        assert "GC Pause(2)" in captured.out
        assert "Metric" in captured.out
        assert "Count" in captured.out
        assert "Sum" in captured.out

    def test_print_stats_table_format_plain(
        self,
        capsys: pytest.CaptureFixture[str],
        gc_stats_item_factory: Callable[..., GCStatsInfo],
    ) -> None:
        """Test plain table format uses dashes in separators."""
        stats = StreamingStats()
        for pid in (11111, 22222):
            stats.update(pid, gc_stats_item_factory())
        print_stats(stats, table_format=TableFormat.PLAIN)
        captured = capsys.readouterr()
        assert "--------" in captured.out

    def test_print_stats_table_format_markdown(
        self,
        capsys: pytest.CaptureFixture[str],
        gc_stats_item_factory: Callable[..., GCStatsInfo],
    ) -> None:
        """Test markdown table format uses blank rows as separators."""
        stats = StreamingStats()
        for pid in (11111, 22222):
            stats.update(pid, gc_stats_item_factory())
        print_stats(stats, table_format=TableFormat.MARKDOWN)
        captured = capsys.readouterr()
        lines = captured.out.splitlines()
        blank = any(
            line.startswith("|")
            and not any(c.isalpha() or c.isdigit() or c == "-" for c in line)
            for line in lines[2:]
        )
        assert blank


class TestPrintTable:
    """Tests for _print_table function."""

    def test_empty_rows_returns_early(self, capsys: pytest.CaptureFixture[str]) -> None:
        _print_table([])
        captured = capsys.readouterr()
        assert captured.out == ""

    def test_column_width_calculation(self, capsys: pytest.CaptureFixture[str]) -> None:
        rows = [
            ["12345", "0", "100", "1000.000", "10.000", "20.000", "30.000", "40.000", "50.000"],
        ]
        _print_table(rows)
        captured = capsys.readouterr()
        lines = captured.out.strip().splitlines()
        assert len(lines) >= 2

    def test_separator_full_format(self, capsys: pytest.CaptureFixture[str]) -> None:
        rows = [
            ["12345", "0", "100", "1000.000", "10.000", "20.000", "30.000", "40.000", "50.000"],
        ]
        _print_table(rows, table_format=TableFormat.PLAIN)
        captured = capsys.readouterr()
        assert "---" in captured.out

    def test_separator_phase_format(self, capsys: pytest.CaptureFixture[str]) -> None:
        from gc_monitor.stats_output import _SEP_PHASE

        rows = [
            ["12345", "0", "100", "1000.000", "10.000", "20.000", "30.000", "40.000", "50.000"],
            _SEP_PHASE,
            ["12345", "1", "200", "2000.000", "20.000", "30.000", "40.000", "50.000", "60.000"],
        ]
        _print_table(rows, table_format=TableFormat.PLAIN)
        captured = capsys.readouterr()
        lines = captured.out.strip().splitlines()
        assert len(lines) >= 4

    def test_separator_blank_markdown(self, capsys: pytest.CaptureFixture[str]) -> None:
        from gc_monitor.stats_output import _SEP_GROUP

        rows = [
            ["12345", "0", "100", "1000.000", "10.000", "20.000", "30.000", "40.000", "50.000"],
            _SEP_GROUP,
            ["22222", "0", "200", "2000.000", "20.000", "30.000", "40.000", "50.000", "60.000"],
        ]
        _print_table(rows, table_format=TableFormat.MARKDOWN)
        captured = capsys.readouterr()
        lines = captured.out.strip().splitlines()
        blank_separator_found = any(
            line.startswith("|") and all(c in ("|", " ") for c in line)
            for line in lines[2:]
        )
        assert blank_separator_found


class TestBuildRows:
    """Tests for _build_rows function."""

    def test_skips_zero_count_stats(self) -> None:
        stats = {0: Stats()}
        rows = _build_rows(stats, "Test")
        assert len(rows) == 0

    def test_formats_values_correctly(self) -> None:
        s = Stats()
        for v in [1000.0, 2000.0, 3000.0]:
            s.update(v)
        s.materialize()

        rows = _build_rows({0: s}, "Test")
        assert len(rows) == 1
        row = rows[0]
        assert row[0] == "Test(0)"
        assert row[1] == "3"
        assert float(row[2]) > 0
        assert float(row[3]) > 0

    def test_sorted_by_generation(self) -> None:
        stats_dict = {}
        for gen in [2, 0, 1]:
            s = Stats()
            s.update(1000.0)
            stats_dict[gen] = s

        rows = _build_rows(stats_dict, "Test")
        generations = [int(r[0].split("(")[1].rstrip(")")) for r in rows]
        assert generations == [0, 1, 2]


class TestPrintStatsEdgeCases:
    """Tests for print_stats edge cases."""

    def test_multiple_pids_sorted(
        self,
        capsys: pytest.CaptureFixture[str],
        gc_stats_item_factory: Callable[..., GCStatsInfo],
    ) -> None:
        stats = StreamingStats()
        for pid in (33333, 11111, 22222):
            stats.update(pid, gc_stats_item_factory())

        print_stats(stats)
        captured = capsys.readouterr()
        pid_11111_pos = captured.out.find("11111")
        pid_22222_pos = captured.out.find("22222")
        pid_33333_pos = captured.out.find("33333")
        assert pid_11111_pos < pid_22222_pos < pid_33333_pos

    def test_total_label_first_metric(
        self,
        capsys: pytest.CaptureFixture[str],
        gc_stats_item_factory: Callable[..., GCStatsInfo],
    ) -> None:
        stats = StreamingStats()
        stats.update(12345, gc_stats_item_factory())

        print_stats(stats)
        captured = capsys.readouterr()
        assert "Total" in captured.out

    def test_incremental_metrics_output(
        self,
        capsys: pytest.CaptureFixture[str],
        incremental_gc_stats_item_factory: Callable[..., GCStatsInfo],
    ) -> None:
        stats = StreamingStats()
        item = incremental_gc_stats_item_factory(
            ts_mark_alive_stop=5000,
            ts_fill_increment_start=5000,
            ts_fill_increment_stop=7000,
            ts_deduce_unreachable_start=7000,
        )
        stats.update(12345, item)

        print_stats(stats)
        captured = capsys.readouterr()
        assert "GC Mark Alive" in captured.out
        assert "GC Fill Increment" in captured.out
        assert "GC Deduce Unreachable" in captured.out

    def test_markdown_format(
        self,
        capsys: pytest.CaptureFixture[str],
        gc_stats_item_factory: Callable[..., GCStatsInfo],
    ) -> None:
        stats = StreamingStats()
        stats.update(12345, gc_stats_item_factory())

        print_stats(stats, table_format=TableFormat.MARKDOWN)
        captured = capsys.readouterr()
        lines = captured.out.strip().splitlines()
        assert len(lines) >= 2
        assert lines[0].startswith("|")
        assert lines[1].startswith("|")
