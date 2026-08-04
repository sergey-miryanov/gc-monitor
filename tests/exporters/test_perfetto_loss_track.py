"""Does the trace processor draw loss spans where we think it does?

Two claims that nothing at the wire level can settle. First, that a loss span
lands on a row of its own rather than among the collections, which is the whole
point of the sentinel tid and of a track descriptor Perfetto could ignore.
Second, that the spans on that row stay laminar, which is what `merge_windows`
is for. Both ask trace processor directly, and are marked ``fuzz`` for the
cost.

The negative control matters more than usual: without evidence that crossing
spans break, the positive test would pass equally in a world where the merge
did nothing.
"""

from pathlib import Path

import pytest
from perfetto.trace_processor import TraceProcessor, TraceProcessorConfig

from gcmon.data import GCStatsInfo, LossMsg
from gcmon.exporters.perfetto_builders import build_trace
from gcmon.exporters.perfetto_format import convert_trace_events_to_perfetto
from gcmon.exporters.perfetto_track_state import PerfettoTrackState
from gcmon.exporters.trace_converter import convert_item_to_trace_format, convert_loss_to_trace_format
from gcmon.trace_event import TraceEvent, process_meta, thread_meta

pytestmark = pytest.mark.fuzz

PID = 100
IID = 0
SEQUENCE_ID = 4242

Slice = tuple[str, str, int, int]
"""``(track_name, slice_name, ts, dur)``."""


def _pause(ts_start: int, ts_stop: int, collections: int) -> GCStatsInfo:
    return GCStatsInfo(
        gen=0,
        iid=IID,
        ts_start=ts_start,
        ts_stop=ts_stop,
        heap_size=1024,
        collections=collections,
        collected=1,
        uncollectable=0,
        candidates=1,
        duration=0.001,
    )


def _write(events: list[TraceEvent], tmp_path: Path, name: str) -> Path:
    descriptors, packets = convert_trace_events_to_perfetto(events, PerfettoTrackState(), SEQUENCE_ID)
    path = tmp_path / f"{name}.pftrace"
    path.write_bytes(build_trace([*descriptors, *packets]))
    return path


def _load(events: list[TraceEvent], tmp_path: Path, name: str) -> tuple[int, list[Slice]]:
    path = _write(events, tmp_path, name)

    tp = TraceProcessor(trace=str(path), config=TraceProcessorConfig(load_timeout=300))
    try:
        rows = list(tp.query("SELECT value FROM stats WHERE name = 'misplaced_end_event'"))
        misplaced = rows[0].value if rows else 0
        slices = [
            (row.track_name, row.name, row.ts, row.dur)
            for row in tp.query(
                # A thread track carries no `track.name` of its own -- trace
                # processor resolves it through the thread table, which the
                # custom loss track has no row in.
                "SELECT COALESCE(t.name, th.name) AS track_name, s.name, s.ts, s.dur "
                "FROM slice s JOIN track t ON s.track_id = t.id "
                "LEFT JOIN thread_track tt ON tt.id = t.id "
                "LEFT JOIN thread th ON th.utid = tt.utid "
                "WHERE s.depth = 0 AND s.name != 'Start Process' ORDER BY s.ts"
            )
        ]
    finally:
        tp.close()
    return misplaced, slices


def _loss(ts_start: int, ts_stop: int, lost_pause: int, iid: int = IID) -> LossMsg:
    return LossMsg(iid=iid, ts_start=ts_start, ts_stop=ts_stop, lost_gen_0=1, lost_pause_gen_0=lost_pause)


def _events(*losses: LossMsg) -> list[TraceEvent]:
    """A collection, the gap the losses sit in, then the next collection."""
    events: list[TraceEvent] = [
        process_meta(PID, f"Process {PID}"),
        thread_meta(PID, IID, f"Thread {IID}"),
        *convert_item_to_trace_format(PID, _pause(1_000, 2_000, 1)),
    ]
    for loss in losses:
        events.extend(convert_loss_to_trace_format(PID, loss))
    events.extend(convert_item_to_trace_format(PID, _pause(9_000, 10_000, 3)))
    return events


def test_a_loss_span_lands_on_its_own_track(tmp_path: Path) -> None:
    """The sentinel tid has to survive as a real, named row: the descriptor is
    emitted off the slices, and nothing else in the trace refers to it."""
    misplaced, slices = _load(_events(_loss(2_000, 9_000, 500)), tmp_path, "own_track")

    assert misplaced == 0
    assert slices == [
        ("Thread 0", "GC Pause (gen=0)", 1_000, 1_000),
        ("GC Loss 0", "GC Loss", 2_000, 7_000),
        ("Thread 0", "GC Pause (gen=0)", 9_000, 1_000),
    ]


def test_the_bar_fills_the_gap_between_two_collections(tmp_path: Path) -> None:
    """It abuts the collection before it and the one after: what is known is
    the interval, not where in it the lost records ran."""
    _, slices = _load(_events(_loss(2_000, 9_000, 500)), tmp_path, "fills_gap")

    loss = next((ts, dur) for _t, name, ts, dur in slices if name == "GC Loss")
    assert loss == (2_000, 7_000)


def test_two_interpreters_get_two_rows(tmp_path: Path) -> None:
    events = _events(_loss(2_000, 9_000, 500), _loss(2_000, 9_000, 500, iid=7))

    _, slices = _load(events, tmp_path, "two_rows")

    assert {track for track, name, _ts, _dur in slices if name == "GC Loss"} == {"GC Loss 0", "GC Loss 7"}


def _process_slices(events: list[TraceEvent], tmp_path: Path, name: str) -> list[Slice]:
    """What sits on the process track itself, which `_load` filters out."""
    tp = TraceProcessor(trace=str(_write(events, tmp_path, name)), config=TraceProcessorConfig(load_timeout=300))
    try:
        return [
            (row.track_name, row.name, row.ts, row.dur)
            for row in tp.query(
                "SELECT t.name AS track_name, s.name, s.ts, s.dur FROM slice s "
                "JOIN track t ON s.track_id = t.id WHERE s.name = 'Start Process'"
            )
        ]
    finally:
        tp.close()


def test_the_process_marker_is_untouched_by_loss_spans(tmp_path: Path) -> None:
    """The loss track hangs off the process track, so a descriptor naming the
    wrong parent would land its spans on the process's own row and reshape
    the lifetime marker ADR-0013 put there."""
    without = _process_slices(_events(), tmp_path, "no_loss")
    with_loss = _process_slices(_events(_loss(2_000, 9_000, 500)), tmp_path, "with_loss")

    assert len(without) == 1
    assert with_loss == without


def test_crossing_spans_are_silently_reshaped(tmp_path: Path) -> None:
    """The negative control, and why `merge_windows` runs before export.
    A track is a stack, so an unmerged span crossing its neighbour is read as
    its parent, and trace processor reports nothing wrong."""
    events = _events(_loss(2_000, 5_000, 500), _loss(4_000, 9_000, 500))

    misplaced, slices = _load(events, tmp_path, "crossing")

    assert misplaced == 0
    assert ("GC Loss 0", "GC Loss", 4_000, 5_000) not in slices
