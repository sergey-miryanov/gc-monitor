"""Does `gcmon combine` redraw the loss row the live run drew?

A live run never hands a span's shape to anyone. It emits one poll's windows
widest first, straight out of `stack_order`, and the row nests because of that
order and nothing else — no record says which span contains which, and the
converter reads its input in the order it arrives. `combine` rebuilds the row
from records instead of from windows, through JSONL, so the nesting survives
that trip only if the file kept the order the monitor wrote in. Field-by-field
round-trip assertions cannot reach that claim: every one of them passes on a
file whose lines were shuffled.

So the check walks the combined **output**. The Chrome trace is parsed as plain
JSON and its BEGIN/END events are resolved as a stack, the way a trace
processor resolves them, and the resulting slices are compared against the ones
the live capture drew. A reversed emission order closes the wrong span, which
shows up here as a depth — the one place ADR-0015 says the mistake is visible
at all, since the trace processor itself reports ``misplaced_end_event = 0``
and reads a crossing as a nesting.

The live side is `test_loss_track_stack`'s capture, reused rather than
restated: the point is that two paths agree, which is worth nothing if the
paths were given different data.
"""

import json
from pathlib import Path

import pytest
from perfetto.trace_processor import TraceProcessor, TraceProcessorConfig

from gcmon.exporters.chrome_trace_io import combine_files, read_jsonl, write_jsonl
from gcmon.protocol import is_loss
from gcmon.trace_event import loss_tid
from tests.exporters.test_loss_track_stack import (
    IID,
    PID,
    Slice,
    ingest,
    loss_slices,
    three_generations,
)

LOSS_TID = loss_tid(IID)

LIVE_ROW: list[Slice] = [
    ("GC Loss(2)", 1_500, 9_000, 0),
    ("GC Loss(1)", 1_500, 7_000, 1),
    ("GC Loss(0)", 1_500, 5_000, 2),
    ("GC Loss(2)", 9_100, 16_000, 0),
    ("GC Loss(1)", 9_100, 14_000, 1),
    ("GC Loss(0)", 9_100, 12_000, 2),
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
    instant keep file order — which is the emission order, and the thing under
    test. Fails loudly if an END closes a slice other than the one on top: a
    crossing row is the exact shape a reversed emission produces, and every
    downstream reader accepts it silently.
    """
    events = [
        e
        for e in json.loads(path.read_text(encoding="utf-8"))
        if isinstance(e, dict) and e.get("tid") == tid and e.get("ph") in ("B", "E")
    ]

    stack: list[tuple[str, int, int]] = []
    slices: list[Slice] = []
    for event in sorted(events, key=lambda e: e["ts"]):
        if event["ph"] == "B":
            stack.append((event["name"], event["ts"], len(stack)))
            continue
        assert stack, f"END of {event['name']!r} at {event['ts']} with nothing open"
        name, ts_start, depth = stack.pop()
        assert name == event["name"], f"END of {event['name']!r} closed {name!r}, opened at {ts_start}"
        slices.append((name, ts_start, event["ts"], depth))
    assert not stack, f"{[name for name, _ts, _depth in stack]} left open"

    return sorted(slices, key=lambda s: (s[1], -s[2]))


def _chrome_loss_args(path: Path, tid: int = LOSS_TID) -> list[dict[str, int]]:
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

    def test_the_generations_still_nest_widest_first(self, tmp_path: Path) -> None:
        """The claim JSONL could quietly drop. Depth is derived by the walk, so
        a file that lost the emission order reparents every span here — and
        would have passed a check that only looked for six loss slices."""
        assert [(name, depth) for name, _s, _e, depth in self.combined(tmp_path)] == [
            ("GC Loss(2)", 0),
            ("GC Loss(1)", 1),
            ("GC Loss(0)", 2),
            ("GC Loss(2)", 0),
            ("GC Loss(1)", 1),
            ("GC Loss(0)", 2),
        ]

    def test_every_span_keeps_its_own_width(self, tmp_path: Path) -> None:
        """A row emitted the wrong way round still holds six spans at three
        widths — it just hands each one to the wrong generation."""
        assert self.combined(tmp_path) == LIVE_ROW

    def test_the_two_paths_agree(self, tmp_path: Path) -> None:
        """Live and offline, one capture, resolved by two independent walks:
        one over the converter's objects, one over the Chrome JSON on disk."""
        live = loss_slices(ingest(*three_generations()))[(PID, LOSS_TID)]

        assert self.combined(tmp_path) == [(name, s // 1_000, e // 1_000, d) for name, s, e, d in live]

    def test_the_counts_ride_on_the_generation_that_lost_them(self, tmp_path: Path) -> None:
        """Nesting is only half of it: a span redrawn at the right depth with
        another generation's counters says the wrong thing just as loudly."""
        out = tmp_path / "combined.json"
        combine_files([_capture(tmp_path)], out, input_format="jsonl", output_format="chrome")

        assert [
            (a["generation"], a["lost_count"], a["lost_pause_ns"], a["collections_from"], a["collections_to"])
            for a in _chrome_loss_args(out)
        ] == [
            (2, 2, 200_000, 31, 32),
            (1, 2, 200_000, 21, 22),
            (0, 2, 200_000, 11, 12),
            (2, 2, 200_000, 34, 35),
            (1, 2, 200_000, 24, 25),
            (0, 2, 200_000, 14, 15),
        ]


class TestTheFileCarriesTheOrder:
    """Why the row survives at all: JSONL is a sequence, and `combine` reads it
    as one. Pinned separately from the drawing so that a regression says which
    half broke."""

    def test_the_lines_are_written_widest_first(self, tmp_path: Path) -> None:
        lines = [json.loads(line) for line in _capture(tmp_path).read_text(encoding="utf-8").splitlines() if line]

        assert [(r["gen"], r["ts_stop"]) for r in lines if "lost_count" in r] == [
            (2, 9_000_000),
            (1, 7_000_000),
            (0, 5_000_000),
            (2, 16_000_000),
            (1, 14_000_000),
            (0, 12_000_000),
        ]

    def test_a_jsonl_to_jsonl_pass_does_not_reshuffle_them(self, tmp_path: Path) -> None:
        """`combine` can also write JSONL, and a capture that went through it
        has to still draw the same row when it is converted later."""
        source = _capture(tmp_path)
        out = tmp_path / "combined.jsonl"

        combine_files([source], out, input_format="jsonl", output_format="jsonl")

        assert [item for item in read_jsonl(out)[PID] if is_loss(item)] == [
            item for item in read_jsonl(source)[PID] if is_loss(item)
        ]


def _shuffled_capture(tmp_path: Path) -> Path:
    """The same six records, each poll's run reversed: the order `groupby`
    walks its keys in, which is narrowest generation first."""
    path = tmp_path / "shuffled.jsonl"
    losses = [item for item in ingest(*three_generations()) if is_loss(item)]
    write_jsonl(path, {PID: [*reversed(losses[:3]), *reversed(losses[3:])]})
    return path


def test_a_shuffled_file_still_nests(tmp_path: Path) -> None:
    """Line order is not part of the record. The converter sorts what it reads
    into stack order, so a capture whose lines were rewritten draws the same
    row as the one gcmon wrote.
    """
    out = tmp_path / "shuffled.json"

    combine_files([_shuffled_capture(tmp_path)], out, input_format="jsonl", output_format="chrome")

    assert [(name, depth) for name, _s, _e, depth in _chrome_row(out)] == [
        ("GC Loss(2)", 0),
        ("GC Loss(1)", 1),
        ("GC Loss(0)", 2),
        ("GC Loss(2)", 0),
        ("GC Loss(1)", 1),
        ("GC Loss(0)", 2),
    ]


@pytest.mark.fuzz
class TestTheTraceProcessorAgrees:
    """The same two claims put to the real trace processor, over `combine`'s
    Perfetto output — marked ``fuzz`` for what loading a trace costs.

    Worth the cost because the walk above is gcmon's own opinion of how a row
    resolves. Perfetto is the one that decides, and it decides silently: the
    reshaped row below comes back with ``misplaced_end_event = 0``.
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

    def test_the_generations_nest_outermost_first(self, tmp_path: Path) -> None:
        misplaced, slices = self.row(tmp_path, _capture(tmp_path), "nested")

        assert misplaced == 0
        assert slices == [
            ("GC Loss(2)", 1_500_000, 7_500_000, 0),
            ("GC Loss(1)", 1_500_000, 5_500_000, 1),
            ("GC Loss(0)", 1_500_000, 3_500_000, 2),
            ("GC Loss(2)", 9_100_000, 6_900_000, 0),
            ("GC Loss(1)", 9_100_000, 4_900_000, 1),
            ("GC Loss(0)", 9_100_000, 2_900_000, 2),
        ]

    def test_a_shuffled_file_is_reshaped_without_complaint(self, tmp_path: Path) -> None:
        """Every generation drawn at another's width, nothing reported. This is
        what a JSONL round trip that dropped the order would ship."""
        misplaced, slices = self.row(tmp_path, _shuffled_capture(tmp_path), "shuffled")

        assert misplaced == 0
        assert [(name, depth) for name, _ts, _dur, depth in slices] == [
            ("GC Loss(0)", 0),
            ("GC Loss(1)", 1),
            ("GC Loss(2)", 2),
            ("GC Loss(0)", 0),
            ("GC Loss(1)", 1),
            ("GC Loss(2)", 2),
        ]
