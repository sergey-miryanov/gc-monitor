"""Tests for stats_output module."""

import re
from collections.abc import Callable
from pathlib import Path

import pytest

from gcmon.data import GCStatsInfo, RunReport
from gcmon.stats import Stats, StreamingStats
from gcmon.stats_output import TableFormat, _build_rows, _print_table, print_stats, summary_lines
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
        rows = _build_rows(stats, "Test", {}, False)
        assert len(rows) == 0

    def test_formats_values_correctly(self) -> None:
        s = Stats()
        for v in [1000.0, 2000.0, 3000.0]:
            s.update(v)
        s.materialize()

        rows = _build_rows({0: s}, "Test", {}, False)
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

        rows = _build_rows(stats_dict, "Test", {}, False)
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
        pid_11111_pos = captured.out.find("11111:0")
        pid_22222_pos = captured.out.find("22222:0")
        pid_33333_pos = captured.out.find("33333:0")
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


def table_rows(out: str) -> list[list[str]]:
    """Every row of the printed table, header first, cells stripped.

    Separators carry no letters or digits; a row whose first cell is empty
    still does.
    """
    rows = []
    for line in out.splitlines():
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if any(char.isalnum() for cell in cells for char in cell):
            rows.append(cells)
    return rows


class TestTheTablePrintsRings:
    """One block per `(pid, iid)`, under a `Total` block for the run.

    The per-process block is gone: its rows blended interpreters the trace
    keeps apart.
    """

    def _one_interpreter(self) -> StreamingStats:
        stats = StreamingStats()
        for _ in range(3):
            stats.update(12345, create_mock_stats_item(iid=0, gen=0, ts_start=0, ts_stop=1_000_000))
        return stats

    def _two_interpreters(self) -> StreamingStats:
        """Same pid, different pause distributions: 1 ms against 20 ms."""
        stats = StreamingStats()
        for _ in range(3):
            stats.update(12345, create_mock_stats_item(iid=0, gen=0, ts_start=0, ts_stop=1_000_000))
            stats.update(12345, create_mock_stats_item(iid=1, gen=0, ts_start=0, ts_stop=20_000_000))
        return stats

    def test_the_header_names_both_fields(self, capsys: pytest.CaptureFixture[str]) -> None:
        print_stats(self._one_interpreter())

        assert table_rows(capsys.readouterr().out)[0][0] == "PID:IID"

    def test_an_ordinary_run_still_carries_its_iid(self, capsys: pytest.CaptureFixture[str]) -> None:
        """`12345:0` on a single-interpreter run as much as on a tree."""
        print_stats(self._one_interpreter())
        labels = [row[0] for row in table_rows(capsys.readouterr().out)]

        assert "12345:0" in labels
        assert "12345" not in labels

    def test_only_the_first_column_moves(self, capsys: pytest.CaptureFixture[str]) -> None:
        """The regression guard: one interpreter of one pid is the whole run,
        so its row and `Total` still agree cell for cell."""
        print_stats(self._one_interpreter())
        rows = table_rows(capsys.readouterr().out)

        total = next(row for row in rows if row[0] == "Total")
        ring = next(row for row in rows if row[0] == "12345:0")

        assert total[1:] == ring[1:]

    def test_two_interpreters_print_two_rows(self, capsys: pytest.CaptureFixture[str]) -> None:
        print_stats(self._two_interpreters())
        labels = [row[0] for row in table_rows(capsys.readouterr().out)]

        assert labels.count("12345:0") == 1
        assert labels.count("12345:1") == 1

    def test_each_ring_row_keeps_its_own_distribution(self, capsys: pytest.CaptureFixture[str]) -> None:
        """A `P99` over both would describe neither interpreter."""
        print_stats(self._two_interpreters())
        rows = table_rows(capsys.readouterr().out)

        p99 = {row[0]: row[8] for row in rows if row[0] in ("Total", "12345:0", "12345:1")}
        p50 = {row[0]: row[5] for row in rows if row[0] in ("Total", "12345:0", "12345:1")}

        assert p99["12345:0"] == "1.000"
        assert p99["12345:1"] == "20.000"
        # The blend sits between the two, describing neither.
        assert p50["Total"] not in (p50["12345:0"], p50["12345:1"])

    def _one_starved_interpreter(self) -> StreamingStats:
        """Interpreter 0 read all three of its collections; interpreter 1 read
        one of ten."""
        stats = StreamingStats()
        for _ in range(3):
            stats.update(12345, create_mock_stats_item(iid=0, gen=0, ts_start=0, ts_stop=1_000_000))
        stats.update(12345, create_mock_stats_item(iid=1, gen=0, ts_start=0, ts_stop=1_000_000))
        stats.record_loss(12345, 1, 0, 9, 9_000_000)
        return stats

    def test_each_ring_row_carries_its_own_coverage(self, capsys: pytest.CaptureFixture[str]) -> None:
        """What an operator sees: the starved interpreter reads 10% on its own
        row instead of hiding in a process-wide 30.8%."""
        print_stats(self._one_starved_interpreter())
        rows = table_rows(capsys.readouterr().out)

        cov = {row[0]: row[9] for row in rows if row[0] in ("Total", "12345:0", "12345:1")}

        assert cov["12345:0"] == "100.0%"
        assert cov["12345:1"] == "10.0%"
        assert cov["Total"] == "30.8%"

    def test_a_ring_that_lost_nothing_prints_one_number_per_cell(self, capsys: pytest.CaptureFixture[str]) -> None:
        """`3/3` beside a neighbour's `1/10` would say nothing was lost twice
        over on a table where something was."""
        print_stats(self._one_starved_interpreter())
        rows = table_rows(capsys.readouterr().out)

        count = {row[0]: row[2] for row in rows if row[0] in ("12345:0", "12345:1")}

        assert count["12345:0"] == "3"
        assert count["12345:1"] == "1/10"

    def test_rings_sort_by_pid_then_interpreter(self, capsys: pytest.CaptureFixture[str]) -> None:
        stats = StreamingStats()
        for pid, iid in ((22222, 1), (12345, 1), (22222, 0), (12345, 0)):
            stats.update(pid, create_mock_stats_item(iid=iid, gen=0, ts_start=0, ts_stop=1_000_000))

        print_stats(stats)
        labels = [row[0] for row in table_rows(capsys.readouterr().out)[1:] if ":" in row[0]]

        assert labels == ["12345:0", "12345:1", "22222:0", "22222:1"]

    def test_read_time_belongs_to_no_ring(self, capsys: pytest.CaptureFixture[str]) -> None:
        stats = self._one_interpreter()
        stats.record_read_time(500_000)

        print_stats(stats)
        read_time = next(row for row in table_rows(capsys.readouterr().out) if row[1] == "Read Time")

        assert read_time[0] == ""


class TestLossColumns:
    def _lossy(self) -> StreamingStats:
        stats = StreamingStats()
        for _ in range(3):
            stats.update(1, create_mock_stats_item(gen=0, ts_start=0, ts_stop=1_000_000))
        stats.record_loss(1, 0, 0, 7, 7_000_000)
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

    def test_the_footer_separates_the_cumulative_counters_from_the_session(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """It is not loss and must not read as part of `Cov`."""
        stats = self._lossy()
        stats.observe_cumulative(1, 0, 0, 5_000, 5.0)

        print_stats(stats)
        out = capsys.readouterr().out

        assert "Since each interpreter started" in out
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
        stats.record_loss(1, 0, 0, 8, 8_000_000)

        print_stats(stats)
        out = capsys.readouterr().out

        assert "1763/1771" in out
        assert "99.5%" in out
        assert "100.0%" not in out

    def test_a_gap_too_small_to_show_still_says_so(self, capsys: pytest.CaptureFixture[str]) -> None:
        stats = StreamingStats()
        for _ in range(1_000_000):
            stats.update(1, create_mock_stats_item(gen=0, ts_start=0, ts_stop=1_000))
        stats.record_loss(1, 0, 0, 1, 1_000)

        print_stats(stats)
        out = capsys.readouterr().out

        assert "<100.0%" in out
        assert ">1.000" in out

    def test_the_footer_matches_the_column(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Two roundings of one number that disagree are worse than either."""
        stats = StreamingStats()
        for _ in range(1763):
            stats.update(1, create_mock_stats_item(gen=0, ts_start=0, ts_stop=1_000_000))
        stats.record_loss(1, 0, 0, 8, 8_000_000)

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
        stats.record_loss(1, 0, 0, 7, 7_000_000)
        stats.observe_cumulative(1, 0, 0, 18, 0.02)

        print_stats(stats)
        notes = self._notes(capsys.readouterr().out)

        assert [note.split(".", 1)[0] for note in notes] == ["1", "2"]
        assert "Coverage:" in notes[0]
        assert "Since each interpreter started" in notes[1]

    def test_a_lone_note_is_still_numbered(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Numbering that appeared only above some threshold would make the
        footer's shape depend on its length, which is harder to scan than a
        `1.` with nothing under it."""
        stats = StreamingStats()
        for _ in range(3):
            stats.update(1, create_mock_stats_item(gen=0, ts_start=0, ts_stop=1_000_000))
        stats.record_loss(1, 0, 0, 7, 7_000_000)

        print_stats(stats)
        notes = self._notes(capsys.readouterr().out)

        assert len(notes) == 1
        assert notes[0].startswith("1. Coverage:")

    def test_a_run_with_nothing_to_explain_numbers_nothing(self, capsys: pytest.CaptureFixture[str]) -> None:
        stats = StreamingStats()
        stats.update(1, create_mock_stats_item(gen=0, ts_start=0, ts_stop=1_000_000))

        print_stats(stats)

        assert self._notes(capsys.readouterr().out) == []


class TestTheCumulativeNoteNamesItsFold:
    """One line per generation whatever the size of the tree, stating what it
    summed over: interpreters start at different moments, and a reused pid
    folds two processes into one figure.
    """

    _SCOPE = re.compile(r"summed over (\d+) interpreters? in (\d+) process(?:es)?")

    def _scope(self, out: str) -> tuple[int, int]:
        """The two counts the note printed, read back off the page."""
        match = self._SCOPE.search(out)
        assert match is not None, out
        return int(match[1]), int(match[2])

    def _stats(self, rings: list[tuple[int, int]]) -> StreamingStats:
        stats = StreamingStats()
        for pid, iid in rings:
            stats.update(pid, create_mock_stats_item(iid=iid, gen=0, ts_start=0, ts_stop=1_000_000))
            stats.observe_cumulative(pid, iid, 0, 500, 0.5)
        return stats

    def test_the_counts_are_the_ones_it_summed(self, capsys: pytest.CaptureFixture[str]) -> None:
        stats = self._stats([(1, 0), (1, 1), (2, 0)])

        print_stats(stats)

        assert self._scope(capsys.readouterr().out) == stats.cumulative_scope() == (3, 2)

    def test_an_ordinary_run_reads_in_the_singular(self, capsys: pytest.CaptureFixture[str]) -> None:
        print_stats(self._stats([(1, 0)]))

        assert "summed over 1 interpreter in 1 process:" in capsys.readouterr().out

    def test_the_figure_beside_the_counts_is_the_fold(self, capsys: pytest.CaptureFixture[str]) -> None:
        print_stats(self._stats([(1, 0), (1, 1), (2, 0)]))

        assert "Gen0 1500 in 1500.000 ms" in capsys.readouterr().out

    def test_it_still_says_the_window_is_included(self, capsys: pytest.CaptureFixture[str]) -> None:
        """The interval overlaps the monitored window rather than extending
        it, so the note must not read as a figure to add to `Count`."""
        print_stats(self._stats([(1, 0)]))

        assert "monitored window included" in capsys.readouterr().out


class TestTheBlockOfAReusedPid:
    """Two processes held the pid, so the table carries two blocks and the
    heading says which is which."""

    def _reused(self) -> StreamingStats:
        stats = StreamingStats()
        stats.update(12345, create_mock_stats_item(gen=0, ts_start=0, ts_stop=1_000_000))
        stats.materialize(12345)
        stats.update(12345, create_mock_stats_item(gen=0, ts_start=0, ts_stop=9_000_000))
        return stats

    def test_the_first_block_reads_plain(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Which is every block of an ordinary run, so nothing widens for a
        target that reuses no pid."""
        print_stats(self._reused())

        assert "12345:0 " in capsys.readouterr().out

    def test_the_second_block_says_which_process_it_is(self, capsys: pytest.CaptureFixture[str]) -> None:
        print_stats(self._reused())

        assert "12345:0#2" in capsys.readouterr().out

    def test_the_two_blocks_carry_their_own_figures(self, capsys: pytest.CaptureFixture[str]) -> None:
        """One heading over both sets of numbers was the defect."""
        print_stats(self._reused())
        rows = table_rows(capsys.readouterr().out)

        sums = {row[0]: row[4] for row in rows if row[0].startswith("12345:0")}
        assert sums == {"12345:0": "1.000", "12345:0#2": "9.000"}

    def test_an_ordinary_run_carries_no_suffix(self, capsys: pytest.CaptureFixture[str]) -> None:
        stats = StreamingStats()
        stats.update(12345, create_mock_stats_item(gen=0, ts_start=0, ts_stop=1_000_000))

        print_stats(stats)

        assert "#" not in capsys.readouterr().out


class TestTheNoteOnRingsWithNoRow:
    """The rows can add up to less than the run, so the footer says by how
    many rings rather than leaving a reader to find the gap."""

    def _crowded(self, extra: int) -> StreamingStats:
        stats = StreamingStats()
        for pid in range(StreamingStats.MAX_ACTIVE_RINGS + extra):
            stats.update(pid, create_mock_stats_item(gen=0, ts_start=0, ts_stop=1_000_000))
        return stats

    def test_a_run_that_fits_says_nothing(self, capsys: pytest.CaptureFixture[str]) -> None:
        print_stats(self._crowded(0))

        assert "got no row" not in capsys.readouterr().out

    def test_it_counts_the_rings_left_out(self, capsys: pytest.CaptureFixture[str]) -> None:
        print_stats(self._crowded(3))

        assert "3 rings got no row" in capsys.readouterr().out

    def test_one_ring_reads_in_the_singular(self, capsys: pytest.CaptureFixture[str]) -> None:
        print_stats(self._crowded(1))

        assert "1 ring got no row" in capsys.readouterr().out

    def test_it_points_at_total(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Those records are in the run's cost, which is what the note is for:
        the detail is missing, the arithmetic is not."""
        print_stats(self._crowded(1))

        assert "counted in Total" in capsys.readouterr().out


POINTER = "Run with --stats for the per-generation breakdown."

_COUNTS = re.compile(r"^Total events: (\d+) \(\+(\d+) reconstructed, ([\d.]+)% observed\)$")


def read_counts(lines: list[str]) -> tuple[int, int, float]:
    """The three numbers the summary printed, read back off the page.

    Reading them back beats asserting a literal string: a summary quoting the
    sampled count in both positions satisfies a literal and tells an operator
    nothing.
    """
    match = next(m for m in (_COUNTS.match(line) for line in lines) if m is not None)
    return int(match[1]), int(match[2]), float(match[3])


class TestSummaryLines:
    """What every run says about its own capture, `--stats` or not.

    The table is the breakdown; these lines are what an operator who asked for
    nothing still reads, so the count cannot stand there unqualified.
    """

    def _run(self, sampled: int, lost: int = 0) -> StreamingStats:
        stats = StreamingStats()
        for _ in range(sampled):
            stats.update(1, create_mock_stats_item(gen=0, ts_start=0, ts_stop=1_000_000))
        if lost:
            stats.record_loss(1, 0, 0, lost, lost * 1_000_000)
        return stats

    def test_a_lossless_run_says_only_what_it_read(self) -> None:
        """Today's three lines to the byte, so no scripted run or CI log
        reading them has to change."""
        assert summary_lines(self._run(1234), Path("trace.pftrace")) == [
            "Monitoring complete.",
            "Total events: 1234",
            "Trace saved to: trace.pftrace",
        ]

    def test_a_stdout_trace_names_no_file(self) -> None:
        """`--format stdout` writes the trace to stdout, so there is no path
        to name and the caller passes none."""
        assert summary_lines(self._run(3), None) == [
            "Monitoring complete.",
            "Total events: 3",
        ]

    def test_a_lossy_run_says_what_the_count_is_a_share_of(self) -> None:
        assert summary_lines(self._run(1234, lost=8566), Path("trace.pftrace")) == [
            "Monitoring complete.",
            "Total events: 1234 (+8566 reconstructed, 12.6% observed)",
            POINTER,
            "Trace saved to: trace.pftrace",
        ]

    def test_a_run_that_kept_up_says_so(self) -> None:
        """Coverage alone cannot separate "the target collects fast" from
        "gcmon never got to look", so the summary states the denominator."""
        lines = summary_lines(self._run(3), None, pacing=RunReport(ticks_run=600, ticks_scheduled=600))

        assert "Ticks: 600 of 600 scheduled" in lines

    def test_a_run_that_overran_says_how_far_short_it_fell(self) -> None:
        lines = summary_lines(self._run(3), None, pacing=RunReport(ticks_run=188, ticks_scheduled=600))

        assert "Ticks: 188 of 600 scheduled" in lines

    def test_a_lossy_run_that_kept_up_is_told_polling_more_may_help(self) -> None:
        lines = summary_lines(self._run(3, lost=7), None, pacing=RunReport(ticks_run=600, ticks_scheduled=600))

        assert any("may observe more" in line for line in lines)
        assert not any("will not help" in line for line in lines)

    def test_a_lossy_run_that_overran_is_told_the_rate_is_not_the_problem(self) -> None:
        """The advice the monitor used to give unconditionally. Lowering the
        rate cannot add ticks the loop already could not reach."""
        lines = summary_lines(self._run(3, lost=7), None, pacing=RunReport(ticks_run=188, ticks_scheduled=600))

        assert any("will not help" in line for line in lines)
        assert not any("may observe more" in line for line in lines)

    def test_a_run_that_lost_nothing_is_given_no_remedy(self) -> None:
        """Nothing to remedy, whether or not the loop kept up."""
        lines = summary_lines(self._run(3), None, pacing=RunReport(ticks_run=188, ticks_scheduled=600))

        assert not any("--rate" in line for line in lines)

    def test_a_caller_with_nothing_to_say_about_pacing_says_nothing(self) -> None:
        """The summary is built in tests and by callers that never ran a loop;
        no report means no line, rather than a line full of zeroes."""
        assert not [line for line in summary_lines(self._run(3), None) if line.startswith("Ticks:")]

    def test_the_printed_numbers_come_from_the_stats(self) -> None:
        stats = self._run(1234, lost=8566)
        totals = stats.pause_totals_by_gen()[0]

        sampled, reconstructed, _observed = read_counts(summary_lines(stats, None))

        assert sampled == stats.count()
        assert reconstructed == totals.lost_count

    def test_the_percentage_divides_the_two_numbers_beside_it(self) -> None:
        """A reader who does the arithmetic on the page gets the figure on the
        page. It may sit a fraction off the `Cov` column, which counts only
        records carrying a pause, but it cannot contradict its own line."""
        sampled, reconstructed, observed = read_counts(summary_lines(self._run(1234, lost=8566), None))

        assert observed == pytest.approx(100 * sampled / (sampled + reconstructed), abs=0.05)

    def test_it_counts_the_loss_of_every_generation_and_pid(self) -> None:
        """`Total events` counts every record of every pid, so the number
        beside it has to cover the same ground."""
        stats = self._run(10)
        stats.record_loss(1, 0, 1, 5, 5_000_000)
        stats.record_loss(2, 0, 0, 5, 5_000_000)

        _sampled, reconstructed, observed = read_counts(summary_lines(stats, None))

        assert reconstructed == 10
        assert observed == pytest.approx(50.0)

    def test_a_gap_too_small_to_show_still_says_so(self) -> None:
        """2000 read of 2001 rounds to 100.0%, on a line showing one
        reconstructed. `Cov` has the same problem and the same answer."""
        assert "Total events: 2000 (+1 reconstructed, <100.0% observed)" in summary_lines(self._run(2000, lost=1), None)

    def test_the_pointer_appears_once_and_only_when_qualified(self) -> None:
        """It points at the breakdown of a figure the line above it just
        raised. A run with nothing to break down has nothing to point at."""
        assert summary_lines(self._run(3, lost=7), Path("trace.pftrace")).count(POINTER) == 1
        assert POINTER not in summary_lines(self._run(3), Path("trace.pftrace"))

    def test_a_run_that_asked_for_the_table_is_not_sent_for_it(self) -> None:
        """`--stats` prints the breakdown two lines further down, so pointing
        the reader at it there would be pointing at the next paragraph."""
        lines = summary_lines(self._run(3, lost=7), Path("trace.pftrace"), show_stats=True)

        assert POINTER not in lines
        assert "Total events: 3 (+7 reconstructed, 30.0% observed)" in lines

    def test_it_prints_nothing_itself(self, capsys: pytest.CaptureFixture[str]) -> None:
        """`--format stdout` puts the JSONL trace on stdout, so a summary that
        printed would land in the middle of the stream."""
        summary_lines(self._run(3, lost=7), None)

        assert capsys.readouterr().out == ""
