"""Does the trace processor actually accept the loss track?

Everything else about it is checked at the wire level, which cannot say
whether a plain custom track carrying slices loads, or whether merging really
was necessary. These ask it directly, so they load traces and are marked
``fuzz`` alongside the other tests that do.

The negative control matters more than usual here: merging is the whole of
ADR-0015, and without evidence that unmerged crossing windows break, the
positive test would pass equally in a world where the merge was pointless.
"""

from pathlib import Path

import pytest
from perfetto.trace_processor import TraceProcessor, TraceProcessorConfig

from gcmon.exporters.perfetto_builders import build_trace
from gcmon.exporters.perfetto_format import convert_trace_events_to_perfetto
from gcmon.exporters.perfetto_track_state import PerfettoTrackState
from gcmon.exporters.trace_converter import convert_loss_to_trace_format
from gcmon.loss import LossWindow, merge_windows, to_loss_msg
from gcmon.trace_event import TraceEvent, process_meta

pytestmark = pytest.mark.fuzz

PID = 100
SEQUENCE_ID = 4242

# The shape §5.1 of the spec exists for: gen 0 was the last record observed
# before the gap and gen 1 the first observed after it, so their windows cross.
CROSSING = [
    LossWindow(ts_start=1_000, ts_stop=2_000, gen=0, lost_count=76, lost_pause_ns=8_100_000),
    LossWindow(ts_start=1_500, ts_stop=2_500, gen=1, lost_count=5, lost_pause_ns=700_000),
]


def _load(events: list[TraceEvent], tmp_path: Path, name: str) -> tuple[int, list[tuple[int, int]]]:
    """Emit *events*, load them, and report the loss slices with the trace
    processor's ``misplaced_end_event`` counter."""
    descriptors, packets = convert_trace_events_to_perfetto(events, PerfettoTrackState(), SEQUENCE_ID)
    path = tmp_path / f"{name}.pftrace"
    path.write_bytes(build_trace([*descriptors, *packets]))

    tp = TraceProcessor(trace=str(path), config=TraceProcessorConfig(load_timeout=300))
    try:
        rows = list(tp.query("SELECT value FROM stats WHERE name = 'misplaced_end_event'"))
        misplaced = rows[0].value if rows else 0
        slices = [
            (row.ts, row.dur)
            for row in tp.query(
                "SELECT s.ts, s.dur FROM slice s JOIN track t ON s.track_id = t.id "
                "WHERE t.name = 'GC Loss 0' ORDER BY s.ts"
            )
        ]
    finally:
        tp.close()
    return misplaced, slices


def _events(windows: list[LossWindow], *, merge: bool) -> list[TraceEvent]:
    spans = merge_windows(windows) if merge else [merge_windows([w])[0] for w in windows]
    events: list[TraceEvent] = [process_meta(PID, f"Process {PID}")]
    for span in spans:
        events.extend(convert_loss_to_trace_format(PID, to_loss_msg(0, span)))
    return events


def test_a_merged_span_reads_back_exactly(tmp_path: Path) -> None:
    misplaced, slices = _load(_events(CROSSING, merge=True), tmp_path, "merged")

    assert misplaced == 0
    assert slices == [(1_000, 1_500)]


def test_crossing_windows_left_unmerged_are_silently_reshaped(tmp_path: Path) -> None:
    """The negative control, and the reason ``misplaced_end_event`` is not on
    its own a sufficient check.

    Both windows are emitted as they were measured, ``[1000, 2000]`` and
    ``[1500, 2500]``. A track is a stack, so the trace processor reads the
    second BEGIN as a child of the first: the END at 2000 closes the inner
    span and the one at 2500 closes the outer. Both come back with durations
    nobody emitted, and nothing is reported as wrong.
    """
    misplaced, slices = _load(_events(CROSSING, merge=False), tmp_path, "unmerged")

    assert misplaced == 0
    assert slices == [(1_000, 1_500), (1_500, 500)]


def test_disjoint_windows_stay_two_slices(tmp_path: Path) -> None:
    """Merging must not collapse spans that never overlapped."""
    disjoint = [
        LossWindow(ts_start=1_000, ts_stop=2_000, gen=0, lost_count=76, lost_pause_ns=0),
        LossWindow(ts_start=9_000, ts_stop=9_500, gen=0, lost_count=3, lost_pause_ns=0),
    ]

    misplaced, slices = _load(_events(disjoint, merge=True), tmp_path, "disjoint")

    assert misplaced == 0
    assert slices == [(1_000, 1_000), (9_000, 500)]
