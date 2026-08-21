"""Does `gcmon combine` redraw the loss row the live run drew?

A live run never hands a span's shape to anyone. It emits one record per poll
and the row reads as a sequence because consecutive intervals meet at a poll
instant. `combine` rebuilds the row from records instead, through JSONL, and
the trip is not free: one span's END shares its timestamp with the next one's
BEGIN, and a trace processor sorting by timestamp leaves those two in the order
they were emitted. Emitted the wrong way round they read as nested. Field-by-
field round-trip assertions cannot reach that claim: every one of them passes
on a file whose lines were shuffled.

So the check walks the combined **output**. The Chrome trace is parsed as plain
JSON and its BEGIN/END events are resolved as a stack, the way a trace
processor resolves them, and the resulting slices are compared against the ones
the live capture drew. A span opened inside another shows up here as a depth,
the one place ADR-0015 says the mistake is visible at all, since the trace
processor itself reports ``misplaced_end_event = 0`` and reads a crossing as a
nesting.

The live side comes from `loss_row`, which polls a real monitor on a fixed
clock and resolves the row the same way. Two walks, one capture: the point is
that the paths agree, which is worth nothing if they were given different data.
"""

import json
from pathlib import Path
from typing import Any

import pytest
from perfetto.trace_processor import TraceProcessor, TraceProcessorConfig

from gcmon.exporters.combine import combine_files
from gcmon.exporters.jsonl_io import read_jsonl, write_jsonl
from gcmon.model.protocol import TItem, is_loss
from gcmon.model.trace_event import loss_tid
from tests.exporters.loss_row import (
    IID,
    PID,
    POLL_TIMES,
    Slice,
    ingest,
    loss_slices,
    three_generations,
)
from tests.helpers import create_mock_loss_item

LOSS_TID = loss_tid(IID)

LIVE_ROW: list[Slice] = [
    ("GC Loss(0,1,2)", 1_000, 10_000, 0),
    ("GC Loss(0,1,2)", 10_000, 20_000, 0),
]
"""The row `three_generations` draws, in the microseconds a Chrome trace uses.

Spelled out rather than derived so that a combined trace is measured against a
fixed shape and not only against whatever the live path happened to produce.
"""


def _capture(tmp_path: Path, name: str = "capture.jsonl") -> Path:
    """One lossy run, written to JSONL the way `--format jsonl` writes it."""
    path = tmp_path / name
    write_jsonl(path, {PID: ingest(*three_generations())})
    return path


def _chrome_row(path: Path, tid: int = LOSS_TID) -> list[Slice]:
    """Every slice on one row of a Chrome trace, with the depth it was drawn at.

    Reads the file as JSON and walks it, sharing no code with the converter
    that wrote it. Sorting is by timestamp and **stable**, so events at one
    instant keep file order, which is the emission order, and the thing under
    test. Fails loudly if a span opens while another is still open: that is the
    shape a reordered file produces, and every downstream reader accepts it
    silently.
    """
    events = [
        e
        for e in json.loads(path.read_text(encoding="utf-8"))
        if isinstance(e, dict) and e.get("tid") == tid and e.get("ph") in ("B", "E")
    ]

    stack: list[tuple[str, int]] = []
    slices: list[Slice] = []
    for event in sorted(events, key=lambda e: e["ts"]):
        if event["ph"] == "B":
            assert not stack, f"{event['name']!r} at {event['ts']} opened inside {stack[-1][0]!r}"
            stack.append((event["name"], event["ts"]))
            continue
        assert stack, f"END of {event['name']!r} at {event['ts']} with nothing open"
        name, ts_start = stack.pop()
        assert name == event["name"], f"END of {event['name']!r} closed {name!r}, opened at {ts_start}"
        slices.append((name, ts_start, event["ts"], 0))
    assert not stack, f"{[name for name, _ts in stack]} left open"

    return sorted(slices, key=lambda s: (s[1], -s[2]))


def _chrome_loss_args(path: Path, tid: int = LOSS_TID) -> list[dict[str, Any]]:
    """The args of each loss BEGIN, in the order the file carries them."""
    return [
        e["args"]
        for e in json.loads(path.read_text(encoding="utf-8"))
        if isinstance(e, dict) and e.get("tid") == tid and e.get("ph") == "B"
    ]


class TestTheCombinedRowIsTheLiveRow:
    """The drawing, rebuilt offline, against the drawing made live."""

    def combined(self, tmp_path: Path) -> list[Slice]:
        out = tmp_path / "combined.json"
        combine_files([_capture(tmp_path)], out, input_format="jsonl", output_format="chrome")
        return _chrome_row(out)

    def test_the_row_comes_back_flat(self, tmp_path: Path) -> None:
        """The claim JSONL could quietly drop. Depth is derived by the walk, so
        a file whose spans were emitted out of order nests here, and would
        have passed a check that only counted two loss slices."""
        assert [(name, depth) for name, _s, _e, depth in self.combined(tmp_path)] == [
            ("GC Loss(0,1,2)", 0),
            ("GC Loss(0,1,2)", 0),
        ]

    def test_every_span_keeps_its_interval(self, tmp_path: Path) -> None:
        assert self.combined(tmp_path) == LIVE_ROW

    def test_consecutive_spans_still_meet(self, tmp_path: Path) -> None:
        """Which is the part that has to survive: the intervals tile, so the
        second span opens exactly where the first closes and nowhere else."""
        first, second = self.combined(tmp_path)

        assert first[2] == second[1]

    def test_the_two_paths_agree(self, tmp_path: Path) -> None:
        """Live and offline, one capture, resolved by two independent walks:
        one over the converter's objects, one over the Chrome JSON on disk."""
        live = loss_slices(ingest(*three_generations()))[(PID, LOSS_TID)]

        assert self.combined(tmp_path) == [(name, s // 1_000, e // 1_000, d) for name, s, e, d in live]

    def test_the_counts_ride_on_the_generation_that_lost_them(self, tmp_path: Path) -> None:
        """A span redrawn over the right interval with another generation's
        counters says the wrong thing just as loudly."""
        out = tmp_path / "combined.json"
        combine_files([_capture(tmp_path)], out, input_format="jsonl", output_format="chrome")

        assert [
            {gen: (args[f"gen{gen}"]["lost_count"], args[f"gen{gen}"]["lost_collections"]) for gen in (0, 1, 2)}
            for args in _chrome_loss_args(out)
        ] == [
            {0: (2, "11..12"), 1: (2, "21..22"), 2: (2, "31..32")},
            {0: (2, "14..15"), 1: (2, "24..25"), 2: (2, "34..35")},
        ]

    def test_the_totals_survive_the_trip(self, tmp_path: Path) -> None:
        out = tmp_path / "combined.json"
        combine_files([_capture(tmp_path)], out, input_format="jsonl", output_format="chrome")

        assert [(a["observed_count"], a["lost_count"], a["lost_pause_ns"]) for a in _chrome_loss_args(out)] == [
            (3, 6, 600_000),
            (3, 6, 600_000),
        ]


class TestTheFileCarriesTheIntervals:
    """Why the row survives at all: JSONL is a sequence of records, one per
    poll, and each carries its own two edges. Pinned separately from the
    drawing so that a regression says which half broke."""

    def test_one_line_per_poll_interval(self, tmp_path: Path) -> None:
        lines = [json.loads(line) for line in _capture(tmp_path).read_text(encoding="utf-8").splitlines() if line]

        assert [(r["ts_start"], r["ts_stop"]) for r in lines if "gens" in r] == [
            (POLL_TIMES[0], POLL_TIMES[1]),
            (POLL_TIMES[1], POLL_TIMES[2]),
        ]

    def test_each_line_names_every_generation_in_its_interval(self, tmp_path: Path) -> None:
        lines = [json.loads(line) for line in _capture(tmp_path).read_text(encoding="utf-8").splitlines() if line]

        assert [[entry["gen"] for entry in r["gens"]] for r in lines if "gens" in r] == [[0, 1, 2], [0, 1, 2]]

    def test_a_jsonl_to_jsonl_pass_does_not_reshuffle_them(self, tmp_path: Path) -> None:
        """`combine` can also write JSONL, and a capture that went through it
        has to still draw the same row when it is converted later."""
        source = _capture(tmp_path)
        out = tmp_path / "combined.jsonl"

        combine_files([source], out, input_format="jsonl", output_format="jsonl")

        assert [item for item in read_jsonl(out)[PID] if is_loss(item)] == [
            item for item in read_jsonl(source)[PID] if is_loss(item)
        ]


class TestTheWalksCanFail:
    """Negative controls for the two resolvers above.

    Every assertion in this file reads a row through one of them, so a walk
    that accepted a crossing row would pass in a world where the shape did not
    matter. Both are fed a pair of spans that genuinely overlap, which is a
    shape one span per poll cannot produce and which every downstream reader
    accepts in silence.
    """

    def crossing(self) -> list[TItem]:
        return [
            create_mock_loss_item(iid=IID, ts_start=2_000_000, ts_stop=5_000_000),
            create_mock_loss_item(iid=IID, ts_start=4_000_000, ts_stop=9_000_000),
        ]

    def test_the_object_walk_rejects_a_crossing_row(self) -> None:
        with pytest.raises(AssertionError, match="opened inside"):
            loss_slices(self.crossing())

    def test_the_json_walk_rejects_a_crossing_row(self, tmp_path: Path) -> None:
        source = tmp_path / "crossing.jsonl"
        out = tmp_path / "crossing.json"
        write_jsonl(source, {PID: self.crossing()})
        combine_files([source], out, input_format="jsonl", output_format="chrome")

        with pytest.raises(AssertionError, match="opened inside"):
            _chrome_row(out)

    def test_the_live_row_is_flat_to_begin_with(self) -> None:
        """The baseline the combined row is compared against. If the converter
        drew a nested row live, `test_the_two_paths_agree` would pass on two
        wrong rows."""
        row = loss_slices(ingest(*three_generations()))[(PID, LOSS_TID)]

        assert [depth for _name, _s, _e, depth in row] == [0, 0]

    def test_the_walks_are_reading_something(self) -> None:
        """Both resolvers return an empty row for a track that carries no
        slices, so an empty walk is indistinguishable from a clean one."""
        row = loss_slices(ingest(*three_generations()))[(PID, LOSS_TID)]

        assert len(row) == 2


def _shuffled_capture(tmp_path: Path) -> Path:
    """The same records, written newest first."""
    path = tmp_path / "shuffled.jsonl"
    losses = [item for item in ingest(*three_generations()) if is_loss(item)]
    write_jsonl(path, {PID: list(reversed(losses))})
    return path


def test_a_shuffled_file_still_draws_a_flat_row(tmp_path: Path) -> None:
    """Line order is not part of the record. The converter sorts what it reads
    into time order before it emits, so a capture whose lines were rewritten
    draws the same row as the one gcmon wrote.

    Without that sort the later span's BEGIN would go out before the earlier
    one's END, and since the two share a timestamp a processor would read the
    pair as nested rather than as neighbours.
    """
    out = tmp_path / "shuffled.json"

    combine_files([_shuffled_capture(tmp_path)], out, input_format="jsonl", output_format="chrome")

    assert _chrome_row(out) == LIVE_ROW


@pytest.mark.fuzz
class TestTheTraceProcessorAgrees:
    """The same claim put to the real trace processor, over `combine`'s
    Perfetto output, marked ``fuzz`` for what loading a trace costs.

    Worth the cost because the walk above is gcmon's own opinion of how a row
    resolves. Perfetto is the one that decides, and it decides silently.
    """

    def row(self, tmp_path: Path, source: Path, name: str) -> tuple[int, list[tuple[str, int, int, int]]]:
        out = tmp_path / f"{name}.pftrace"
        combine_files([source], out, input_format="jsonl", output_format="perfetto")

        tp = TraceProcessor(trace=str(out), config=TraceProcessorConfig(load_timeout=300))
        try:
            stats = list(tp.query("SELECT value FROM stats WHERE name = 'misplaced_end_event'"))
            return (
                stats[0].value if stats else 0,
                [
                    (row.name, row.ts, row.dur, row.depth)
                    for row in tp.query(
                        "SELECT s.name, s.ts, s.dur, s.depth FROM slice s JOIN track t ON s.track_id = t.id "
                        "WHERE t.name LIKE 'GC Loss%' ORDER BY s.ts, s.depth"
                    )
                ],
            )
        finally:
            tp.close()

    def test_the_intervals_come_back_as_neighbours(self, tmp_path: Path) -> None:
        misplaced, slices = self.row(tmp_path, _capture(tmp_path), "flat")

        assert misplaced == 0
        assert slices == [
            ("GC Loss(0,1,2)", POLL_TIMES[0], POLL_TIMES[1] - POLL_TIMES[0], 0),
            ("GC Loss(0,1,2)", POLL_TIMES[1], POLL_TIMES[2] - POLL_TIMES[1], 0),
        ]

    def test_a_shuffled_file_comes_back_the_same_way(self, tmp_path: Path) -> None:
        """What the sort in the converter buys, measured where it matters:
        two spans that touch, read by the processor that decides."""
        misplaced, slices = self.row(tmp_path, _shuffled_capture(tmp_path), "shuffled")

        assert misplaced == 0
        assert [depth for _name, _ts, _dur, depth in slices] == [0, 0]

    def test_the_generation_groups_survive_as_flattened_args(self, tmp_path: Path) -> None:
        """The grouped annotations, through `combine`, into SQL. The processor
        flattens a `dict_entries` group by joining the names with a dot, so a
        group that arrived as a value instead would leave these keys missing
        rather than wrong."""
        out = tmp_path / "args.pftrace"
        combine_files([_capture(tmp_path)], out, input_format="jsonl", output_format="perfetto")

        tp = TraceProcessor(trace=str(out), config=TraceProcessorConfig(load_timeout=300))
        try:
            rows = list(
                tp.query(
                    "SELECT a.flat_key, a.string_value, a.int_value "
                    "FROM slice s JOIN track t ON s.track_id = t.id JOIN args a ON a.arg_set_id = s.arg_set_id "
                    "WHERE t.name LIKE 'GC Loss%' AND s.ts = "
                    f"{POLL_TIMES[0]} ORDER BY a.flat_key"
                )
            )
        finally:
            tp.close()

        found: dict[str, Any] = {}
        for row in rows:
            # An args row fills one value column and leaves the other NULL,
            # which the stub's non-optional types do not describe.
            text: Any = row.string_value
            found[row.flat_key] = text if text is not None else row.int_value

        assert found["debug.gen1.lost_collections"] == "21..22"
        assert found["debug.gen1.lost_count"] == 2
        assert found["debug.lost_count"] == 6
