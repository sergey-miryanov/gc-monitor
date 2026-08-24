"""Rebuilding a session's statistics from the capture it wrote.

``_replay`` is the last of the hook's reading left in ``pyperf/hook.py``; spec
0061 moves it to a module a tracefile reader can share. The hook itself no
longer calls it, so these drive it directly.
"""

import json
import os
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, override
from unittest.mock import patch

import pytest

from gcmon.exporters.jsonl_io import read_jsonl
from gcmon.model.protocol import TGCStatsInfo, TItem, is_gc_stats, is_loss
from gcmon.pyperf.hook import (
    _get_env_pyperf_hook_control_timeout,
    _replay,
)
from gcmon.stats.streaming_stats import PauseTotals, StreamingStats


def _make_jsonl_event(**kwargs: Any) -> dict[str, Any]:
    defaults: dict[str, Any] = {
        "pid": 12345,
        "tid": 0,
        "gen": 0,
        "iid": 0,
        "ts_start": 1_000_000_000,
        "ts_stop": 1_005_000_000,
        "collections": 5,
        "collected": 50,
        "uncollectable": 2,
        "candidates": 10,
        "heap_size": 20000,
        "duration": 0.005,
    }
    return {**defaults, **kwargs}


def _make_jsonl_loss(gen: int = 0, lost_count: int = 0, lost_pause_ns: int = 0, **kwargs: Any) -> dict[str, Any]:
    """A `LossMsg` line, as `JsonlExporter.add_loss_event` writes one."""
    defaults: dict[str, Any] = {
        "pid": 12345,
        "tid": -2,
        "iid": 0,
        "ts_start": 1_005_000_000,
        "ts_stop": 1_020_000_000,
        "gens": [
            {
                "gen": gen,
                "observed_count": 1,
                "lost_from": 0,
                "lost_count": lost_count,
                "lost_pause_ns": lost_pause_ns,
            }
        ],
    }
    return {**defaults, **kwargs}


def _write_jsonl(path: Path, *events: dict[str, Any]) -> None:
    """Write one or more JSON objects as JSONL to a file."""
    with open(path, "w") as f:
        for event in events:
            f.write(json.dumps(event) + "\n")


def _parse_jsonl(tmp_path: Path, *lines: dict[str, Any]) -> dict[int, list[TItem]]:
    """A capture as `_replay` meets it: decoded off a file, not hand-built.

    Going through the file keeps the guards under test facing whatever
    `from_mapping` actually returns for a line, rather than a struct the test
    chose to construct.
    """
    path = tmp_path / "capture.jsonl"
    _write_jsonl(path, *lines)
    return read_jsonl(path)


def _replayed(tmp_path: Path, *lines: dict[str, Any]) -> StreamingStats:
    """Everything one capture says, folded the way `_replay` folds it."""
    stats = StreamingStats()
    _replay(stats, _parse_jsonl(tmp_path, *lines))
    return stats


def _gen0(stats: StreamingStats) -> PauseTotals:
    return stats.pause_totals_by_gen()[0]


def _exact_total(stats: StreamingStats) -> int:
    return sum(totals.exact_count for totals in stats.pause_totals_by_gen().values())


class _RecordingStats(StreamingStats):
    """A `StreamingStats` that remembers what `_replay` handed it.

    Subclassed rather than mocked because the question is what reaches
    `update`, and a mock that intercepted the call would also swallow the
    arithmetic the same tests read back out of `aggregate`.
    """

    def __init__(self) -> None:
        super().__init__()
        self.updated: list[TGCStatsInfo] = []
        self.losses: list[tuple[int, int, int, int, int]] = []

    @override
    def update(self, pid: int, item: TGCStatsInfo) -> None:
        self.updated.append(item)
        if is_loss(item):
            # Folding one would die on the `heap_size` it does not have,
            # and that AttributeError would surface before any assertion
            # about `updated` got to run. Recording it and stopping lets a
            # guard that stopped being disjoint fail as what it broke.
            return
        super().update(pid, item)

    @override
    def record_loss(self, pid: int, iid: int, gen: int, lost_count: int, lost_pause_ns: int) -> None:
        self.losses.append((pid, iid, gen, lost_count, lost_pause_ns))
        super().record_loss(pid, iid, gen, lost_count, lost_pause_ns)


def _replay_asking_is_loss_first(stats: StreamingStats, parsed: Mapping[int, Sequence[TItem]]) -> None:
    """`_replay` with its two guards asked in the opposite order.

    Making `is_gc_stats` stand down for anything `is_loss` claims is exactly
    what testing `is_loss` first would do, and changes nothing else, so the
    patched and unpatched runs can only disagree about a record that answers
    to both guards.
    """
    with patch("gcmon.pyperf.hook.is_gc_stats", lambda item: not is_loss(item) and is_gc_stats(item)):
        _replay(stats, parsed)


class TestReplayFoldsLossAndCumulativeCounters:
    """What the monitor folded live has to come back off the file.

    A reader meets a session only as JSONL, so a loss record it skips is a
    session that reports full coverage and a sampled sum labelled exact.
    The cumulative counters need no record of their own: ``collections`` and
    ``duration`` on every GC record are the target's own cumulative totals.
    """

    LOST = _make_jsonl_loss(lost_count=2, lost_pause_ns=7_000_000)

    def observed(self) -> list[dict[str, Any]]:
        """Two gen-0 records of 5 ms each, three collections apart."""
        return [
            _make_jsonl_event(collections=5, duration=0.005),
            _make_jsonl_event(collections=8, ts_start=1_020_000_000, ts_stop=1_025_000_000, duration=0.020),
        ]

    def test_the_count_covers_what_the_poll_missed(self, tmp_path: Path) -> None:
        stats = _replayed(tmp_path, *self.observed(), self.LOST)

        assert _gen0(stats).exact_count == 4
        assert _exact_total(stats) == 4

    def test_the_sum_covers_the_pause_nobody_saw(self, tmp_path: Path) -> None:
        stats = _replayed(tmp_path, *self.observed(), self.LOST)

        assert _gen0(stats).exact_pause_ns == pytest.approx(17_000_000)

    def test_coverage_reports_the_share_that_was_read(self, tmp_path: Path) -> None:
        stats = _replayed(tmp_path, *self.observed(), self.LOST)

        assert _gen0(stats).coverage == pytest.approx(0.5)

    def test_a_session_that_lost_nothing_reports_full_coverage(self, tmp_path: Path) -> None:
        totals = _gen0(_replayed(tmp_path, *self.observed()))

        assert totals.coverage == 1.0
        assert totals.exact_count == 2
        assert totals.exact_pause_ns == pytest.approx(10_000_000)

    def test_the_counters_come_from_the_newest_record_of_the_ring(self, tmp_path: Path) -> None:
        """The whole history the target reports, not the monitored part."""
        counters = _replayed(tmp_path, *self.observed(), self.LOST).cumulative_totals_by_gen()[0]

        assert counters.collections == 8
        assert counters.pause_ns == pytest.approx(20_000_000)

    def test_records_out_of_order_do_not_walk_the_counters_backwards(self, tmp_path: Path) -> None:
        """Cumulative totals only ever grow, so the highest counter wins
        however the lines happen to be ordered."""
        newest, oldest = self.observed()[1], self.observed()[0]

        stats = _replayed(tmp_path, newest, oldest)

        assert stats.cumulative_totals_by_gen()[0].collections == 8

    def test_the_loss_applies_to_the_whole_sample_not_a_file_prefix(self, tmp_path: Path) -> None:
        """Loss is summed and applied once the whole sample is folded, so a
        run that ends well covered is not dragged down by a loss line that
        happened to be written before most of the records."""
        records = [
            _make_jsonl_event(
                collections=n,
                ts_start=1_000_000_000 + n * 10_000_000,
                ts_stop=1_005_000_000 + n * 10_000_000,
            )
            for n in range(1, 21)
        ]

        stats = _replayed(tmp_path, _make_jsonl_loss(lost_count=1, lost_pause_ns=5_000_000), *records)

        assert _gen0(stats).coverage > 0.9


class TestLossIsNeverReplayedAsACollection:
    """The one record type `_replay` must keep out of the sample.

    `_replay` asks `is_gc_stats` before `is_loss`; every other call site in
    the codebase asks the other way round. That stays harmless only while the
    two guards are disjoint. A loss record claimed by `is_gc_stats` would be
    folded in as a collection here and nowhere else, inflating the very
    sample the loss it carries exists to correct, and the inflated sum would
    still be published labelled exact. All that stands between the hook and
    that is which field each guard reaches for, which is far too load-bearing
    to leave resting on nobody having noticed.
    """

    LOST = _make_jsonl_loss(lost_count=2, lost_pause_ns=7_000_000)

    def capture(self) -> list[dict[str, Any]]:
        """Two gen-0 records of 5 ms and 20 ms, and one loss record."""
        return [
            _make_jsonl_event(collections=5, duration=0.005),
            _make_jsonl_event(collections=8, ts_start=1_020_000_000, ts_stop=1_025_000_000, duration=0.020),
            self.LOST,
        ]

    @pytest.mark.parametrize(
        "line, claimant, impostor",
        [
            (_make_jsonl_event(), is_gc_stats, is_loss),
            (_make_jsonl_loss(lost_count=2, lost_pause_ns=7_000_000), is_loss, is_gc_stats),
        ],
        ids=["gc-record", "loss-record"],
    )
    def test_exactly_one_guard_claims_each_record(
        self,
        tmp_path: Path,
        line: dict[str, Any],
        claimant: Callable[[object], bool],
        impostor: Callable[[object], bool],
    ) -> None:
        """Guards that cannot both fire are what make the order immaterial.

        A loss record carries a `ts_start` and a `ts_stop` of its own, so a
        guard resting on either would claim both types.
        """
        (item,) = _parse_jsonl(tmp_path, line)[12345]

        assert claimant(item)
        assert not impostor(item)

    def test_replay_does_not_fold_a_loss_record_into_the_sample(self, tmp_path: Path) -> None:
        stats = _RecordingStats()

        _replay(stats, _parse_jsonl(tmp_path, *self.capture()))

        assert [item for item in stats.updated if is_loss(item)] == []
        assert len(stats.updated) == 2
        assert stats.losses == [(12345, 0, 0, 2, 7_000_000)]

    def test_the_guard_order_does_not_change_what_gets_folded(self, tmp_path: Path) -> None:
        """Same capture, both branch orders, same statistics."""
        parsed = _parse_jsonl(tmp_path, *self.capture())
        as_written, reversed_order = _RecordingStats(), _RecordingStats()

        _replay(as_written, parsed)
        _replay_asking_is_loss_first(reversed_order, parsed)

        assert as_written.updated == reversed_order.updated
        assert as_written.losses == reversed_order.losses
        assert as_written.pause_totals_by_gen() == reversed_order.pause_totals_by_gen()

    def test_a_capture_of_nothing_but_loss_replays_and_still_counts_it(self, tmp_path: Path) -> None:
        """Nothing was sampled, so there is no pause to describe, but the
        collections nobody saw happened all the same."""
        stats = _RecordingStats()

        _replay(stats, _parse_jsonl(tmp_path, self.LOST))

        assert stats.updated == []
        assert stats.count() == 0
        assert stats.pause_totals(12345, 0, 0).lost_count == 2
        assert stats.pause_totals_by_gen()[0].exact_count == 2
        assert stats.pause_totals_by_gen()[0].coverage == 0.0
        assert _exact_total(stats) == 2

    def test_loss_before_the_records_folds_the_same_as_loss_after(self, tmp_path: Path) -> None:
        """Loss is summed and applied once the whole sample is folded, so
        where its record sits in the file cannot move a number."""
        records = self.capture()[:-1]

        after = _replayed(tmp_path, *records, self.LOST)
        before = _replayed(tmp_path, self.LOST, *records)

        assert before.pause_totals_by_gen() == after.pause_totals_by_gen()
        assert _gen0(after).coverage == pytest.approx(0.5)
        assert _gen0(after).exact_pause_ns == pytest.approx(17_000_000)


class TestReplayKeepsTheInterpretersApart:
    """A loss record names the interpreter it belongs to, so `_replay` keys on
    it. A capture read back from JSONL has to report what the live run
    reported, and the live run reports per ring.
    """

    def capture(self) -> list[dict[str, Any]]:
        """Interpreter 0 read everything it ran; interpreter 1 read one of
        ten."""
        return [
            _make_jsonl_event(iid=0, collections=5),
            _make_jsonl_event(iid=1, collections=10, ts_start=1_030_000_000, ts_stop=1_031_000_000),
            _make_jsonl_loss(iid=1, lost_count=9, lost_pause_ns=9_000_000),
        ]

    def _replayed(self, tmp_path: Path) -> StreamingStats:
        stats = StreamingStats()
        _replay(stats, _parse_jsonl(tmp_path, *self.capture()))
        return stats

    def test_the_loss_lands_on_the_interpreter_that_lost_it(self, tmp_path: Path) -> None:
        stats = self._replayed(tmp_path)

        assert stats.pause_totals(12345, 0, 0).lost_count == 0
        assert stats.pause_totals(12345, 1, 0).lost_count == 9

    def test_each_interpreter_reports_its_own_coverage(self, tmp_path: Path) -> None:
        stats = self._replayed(tmp_path)

        assert stats.pause_totals(12345, 0, 0).coverage == 1.0
        assert stats.pause_totals(12345, 1, 0).coverage == pytest.approx(0.1)

    def test_the_run_still_folds_to_one_answer(self, tmp_path: Path) -> None:
        """`Total` and the benchmark metrics stay run-wide, which is the scope
        they were released with."""
        totals = self._replayed(tmp_path).pause_totals_by_gen()[0]

        assert (totals.sampled_count, totals.lost_count) == (2, 9)


class TestGetEnvControlTimeout:
    def test_default_value(self) -> None:
        with patch.dict(os.environ, clear=True):
            assert _get_env_pyperf_hook_control_timeout() == 10.0

    def test_custom_value(self) -> None:
        with patch.dict(os.environ, {"GCMON_PYPERF_HOOK_CONTROL_TIMEOUT": "30"}):
            assert _get_env_pyperf_hook_control_timeout() == 30.0

    def test_invalid_value_returns_default(self) -> None:
        with patch.dict(os.environ, {"GCMON_PYPERF_HOOK_CONTROL_TIMEOUT": "not-a-number"}):
            assert _get_env_pyperf_hook_control_timeout() == 10.0
