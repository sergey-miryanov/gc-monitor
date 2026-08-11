"""Tests for stats_output module."""

from collections.abc import Callable

import pytest

from gcmon.data import GCStatsInfo
from gcmon.stats import Stats, StreamingStats
from gcmon.stats_output import TableFormat, _build_rows, _print_table, print_stats
from tests.helpers import create_mock_stats_item


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
            line.startswith("|") and not any(c.isalpha() or c.isdigit() or c == "-" for c in line) for line in lines[2:]
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
            ["12345", "0", "100", "1000.000", "10.000", "20.000", "30.000", "40.000", "50.000", "1.00", "1.00"],
        ]
        _print_table(rows)
        captured = capsys.readouterr()
        lines = captured.out.strip().splitlines()
        assert len(lines) >= 2

    def test_separator_full_format(self, capsys: pytest.CaptureFixture[str]) -> None:
        rows = [
            ["12345", "0", "100", "1000.000", "10.000", "20.000", "30.000", "40.000", "50.000", "1.00", "1.00"],
        ]
        _print_table(rows, table_format=TableFormat.PLAIN)
        captured = capsys.readouterr()
        assert "---" in captured.out

    def test_separator_phase_format(self, capsys: pytest.CaptureFixture[str]) -> None:
        from gcmon.stats_output import _SEP_PHASE

        rows = [
            ["12345", "0", "100", "1000.000", "10.000", "20.000", "30.000", "40.000", "50.000", "1.00", "1.00"],
            _SEP_PHASE,
            ["12345", "1", "200", "2000.000", "20.000", "30.000", "40.000", "50.000", "60.000", "1.00", "1.00"],
        ]
        _print_table(rows, table_format=TableFormat.PLAIN)
        captured = capsys.readouterr()
        lines = captured.out.strip().splitlines()
        assert len(lines) >= 4

    def test_separator_blank_markdown(self, capsys: pytest.CaptureFixture[str]) -> None:
        from gcmon.stats_output import _SEP_GROUP

        rows = [
            ["12345", "0", "100", "1000.000", "10.000", "20.000", "30.000", "40.000", "50.000", "1.00", "1.00"],
            _SEP_GROUP,
            ["22222", "0", "200", "2000.000", "20.000", "30.000", "40.000", "50.000", "60.000", "1.00", "1.00"],
        ]
        _print_table(rows, table_format=TableFormat.MARKDOWN)
        captured = capsys.readouterr()
        lines = captured.out.strip().splitlines()
        blank_separator_found = any(line.startswith("|") and all(c in ("|", " ") for c in line) for line in lines[2:])
        assert blank_separator_found


class TestBuildRows:
    """Tests for _build_rows function."""

    def test_skips_zero_count_stats(self) -> None:
        stats = {0: Stats()}
        rows = _build_rows(stats, "Test", StreamingStats(), None, False)
        assert len(rows) == 0

    def test_formats_values_correctly(self) -> None:
        s = Stats()
        for v in [1000.0, 2000.0, 3000.0]:
            s.update(v)
        s.materialize()

        rows = _build_rows({0: s}, "Test", StreamingStats(), None, False)
        assert len(rows) == 1
        row = rows[0]
        assert row[0] == "Test(0)"
        assert row[1] == "3"
        assert float(row[2]) > 0
        assert float(row[3]) > 0

    def test_sorted_by_generation(self) -> None:
        stats_dict: dict[int, Stats] = {}
        for gen in [2, 0, 1]:
            s = Stats()
            s.update(1000.0)
            stats_dict[gen] = s

        rows = _build_rows(stats_dict, "Test", StreamingStats(), None, False)
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
        assert "GC Handle Weakrefs Callbacks" in captured.out
        assert "GC Finalize Garbage" in captured.out
        assert "GC Handle Resurrected" in captured.out
        assert "GC Clear Weakrefs" in captured.out
        assert "GC Delete Garbage" in captured.out

    def test_pause_row_printed_in_milliseconds(
        self,
        capsys: pytest.CaptureFixture[str],
        gc_stats_item_factory: Callable[..., GCStatsInfo],
    ) -> None:
        stats = StreamingStats()
        stats.update(12345, gc_stats_item_factory(ts_start=0, ts_stop=1_000_000))
        stats.update(12345, gc_stats_item_factory(ts_start=0, ts_stop=3_000_000))

        print_stats(stats)
        captured = capsys.readouterr()
        pause_line = next(line for line in captured.out.splitlines() if "GC Pause(0)" in line)
        cells = [c.strip() for c in pause_line.strip().strip("|").split("|")]
        # PID, Metric, Count, Sum, Avg, P50, P90, P95, P99 - durations in milliseconds
        assert cells[1] == "GC Pause(0)"
        assert cells[2] == "2"
        assert cells[3] == "4.000"
        assert cells[4] == "2.000"

    def test_read_time_row_omitted_when_not_recorded(
        self,
        capsys: pytest.CaptureFixture[str],
        gc_stats_item_factory: Callable[..., GCStatsInfo],
    ) -> None:
        stats = StreamingStats()
        stats.update(12345, gc_stats_item_factory())

        print_stats(stats)
        captured = capsys.readouterr()
        assert "Read Time" not in captured.out

    def test_read_time_row_printed(
        self,
        capsys: pytest.CaptureFixture[str],
        gc_stats_item_factory: Callable[..., GCStatsInfo],
    ) -> None:
        stats = StreamingStats()
        stats.update(12345, gc_stats_item_factory())
        stats.record_read_time(1_000_000)
        stats.record_read_time(3_000_000)

        print_stats(stats)
        captured = capsys.readouterr()
        read_time_line = next(line for line in captured.out.splitlines() if "Read Time" in line)
        cells = [c.strip() for c in read_time_line.strip().strip("|").split("|")]
        # PID, Metric, Count, Sum, Avg, P50, P90, P95, P99 - durations in milliseconds
        assert cells[0] == ""
        assert cells[1] == "Read Time"
        assert cells[2] == "2"
        assert cells[3] == "4.000"
        assert cells[4] == "2.000"

    def test_read_time_row_without_gc_data(self, capsys: pytest.CaptureFixture[str]) -> None:
        stats = StreamingStats()
        stats.record_read_time(2_500_000)

        print_stats(stats)
        captured = capsys.readouterr()
        assert "No GC statistics collected." not in captured.out
        assert "Read Time" in captured.out
        assert "2.500" in captured.out

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


class TestLossColumns:
    def _lossy(self) -> StreamingStats:
        stats = StreamingStats()
        for _ in range(3):
            stats.update(1, create_mock_stats_item(gen=0, ts_start=0, ts_stop=1_000_000))
        stats.record_loss(1, 0, 7, 7_000_000)
        return stats

    def test_count_and_sum_carry_both_numbers(self, capsys: pytest.CaptureFixture[str]) -> None:
        print_stats(self._lossy())
        out = capsys.readouterr().out

        assert "3/10" in out
        assert "3.000/10.000" in out

    def test_cov_and_f_are_columns(self, capsys: pytest.CaptureFixture[str]) -> None:
        print_stats(self._lossy())
        out = capsys.readouterr().out

        assert "Cov" in out
        assert "30.0%" in out
        assert "3.333" in out

    def test_a_lossless_run_shows_one_number_per_cell(self, capsys: pytest.CaptureFixture[str]) -> None:
        """`3/3` in every cell would say nothing was lost twice over."""
        stats = StreamingStats()
        for _ in range(3):
            stats.update(1, create_mock_stats_item(gen=0, ts_start=0, ts_stop=1_000_000))

        print_stats(stats)
        out = capsys.readouterr().out

        assert "3/3" not in out
        assert "100.0%" in out

    def test_a_lossless_run_prints_no_footer(self, capsys: pytest.CaptureFixture[str]) -> None:
        stats = StreamingStats()
        stats.update(1, create_mock_stats_item(gen=0, ts_start=0, ts_stop=1_000_000))

        print_stats(stats)

        assert "Coverage:" not in capsys.readouterr().out

    def test_the_footer_names_the_coverage(self, capsys: pytest.CaptureFixture[str]) -> None:
        print_stats(self._lossy())
        out = capsys.readouterr().out

        assert "Coverage: Gen0 30.0%" in out
        assert "percentiles are sampled and read high" in out

    def test_the_footer_separates_lifetime_from_the_session(self, capsys: pytest.CaptureFixture[str]) -> None:
        """It is not loss and must not read as part of `Cov`."""
        stats = self._lossy()
        stats.record_lifetime(1, 0, 0, 5_000, 5.0)

        print_stats(stats)
        out = capsys.readouterr().out

        assert "Since interpreter start" in out
        assert "Gen0 5000" in out

    def test_read_time_leaves_cov_and_f_blank(self, capsys: pytest.CaptureFixture[str]) -> None:
        stats = self._lossy()
        stats.record_read_time(500_000)

        print_stats(stats)
        lines = [ln for ln in capsys.readouterr().out.splitlines() if "Read Time" in ln]

        assert lines[0].rstrip().endswith("|      |      |") or lines[0].count("|") == 12

    def test_cov_never_rounds_up_past_a_visible_gap(self, capsys: pytest.CaptureFixture[str]) -> None:
        """1763 of 1771 is 99.5%, but a coarser format would print 100% beside
        a `Count` cell plainly showing eight missing."""
        stats = StreamingStats()
        for _ in range(1763):
            stats.update(1, create_mock_stats_item(gen=0, ts_start=0, ts_stop=1_000_000))
        stats.record_loss(1, 0, 8, 8_000_000)

        print_stats(stats)
        out = capsys.readouterr().out

        assert "1763/1771" in out
        assert "99.5%" in out
        assert "100.0%" not in out

    def test_a_gap_too_small_to_show_still_says_so(self, capsys: pytest.CaptureFixture[str]) -> None:
        stats = StreamingStats()
        for _ in range(1_000_000):
            stats.update(1, create_mock_stats_item(gen=0, ts_start=0, ts_stop=1_000))
        stats.record_loss(1, 0, 1, 1_000)

        print_stats(stats)
        out = capsys.readouterr().out

        assert "<100.0%" in out
        assert ">1.000" in out

    def test_the_footer_matches_the_column(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Two roundings of one number that disagree are worse than either."""
        stats = StreamingStats()
        for _ in range(1763):
            stats.update(1, create_mock_stats_item(gen=0, ts_start=0, ts_stop=1_000_000))
        stats.record_loss(1, 0, 8, 8_000_000)

        print_stats(stats)
        out = capsys.readouterr().out

        assert "Coverage: Gen0 99.5%" in out


class TestTheFooterNotesAreNumbered:
    """Which notes appear depends on the run, so their order teaches a reader
    nothing. The number is what separates one from the next once two of them
    wrap across a narrow terminal.
    """

    def _notes(self, out: str) -> list[str]:
        return [line for line in out.splitlines() if line[:1].isdigit()]

    def test_every_note_present_is_numbered_in_order(self, capsys: pytest.CaptureFixture[str]) -> None:
        stats = StreamingStats()
        for _ in range(3):
            stats.update(1, create_mock_stats_item(gen=0, ts_start=0, ts_stop=1_000_000))
        stats.record_loss(1, 0, 7, 7_000_000)
        stats.record_lifetime(1, 0, 0, 18, 0.02)

        print_stats(stats)
        notes = self._notes(capsys.readouterr().out)

        assert [note.split(".", 1)[0] for note in notes] == ["1", "2"]
        assert "Coverage:" in notes[0]
        assert "Since interpreter start" in notes[1]

    def test_a_lone_note_is_still_numbered(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Numbering that appeared only above some threshold would make the
        footer's shape depend on its length, which is harder to scan than a
        `1.` with nothing under it."""
        stats = StreamingStats()
        for _ in range(3):
            stats.update(1, create_mock_stats_item(gen=0, ts_start=0, ts_stop=1_000_000))
        stats.record_loss(1, 0, 7, 7_000_000)

        print_stats(stats)
        notes = self._notes(capsys.readouterr().out)

        assert len(notes) == 1
        assert notes[0].startswith("1. Coverage:")

    def test_a_run_with_nothing_to_explain_numbers_nothing(self, capsys: pytest.CaptureFixture[str]) -> None:
        stats = StreamingStats()
        stats.update(1, create_mock_stats_item(gen=0, ts_start=0, ts_stop=1_000_000))

        print_stats(stats)

        assert self._notes(capsys.readouterr().out) == []
