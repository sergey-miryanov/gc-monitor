"""Differential fuzz for the ``Processes`` track's emission order.

``finalize_perfetto_packets`` emits one adjacent BEGIN/END pair per span
rather than interleaving them into stack order (ADR-0011). Whether that
survives depends on how the trace processor pairs slices, which no
wire-level test can check, so these ask it directly.

Marked ``fuzz``: each trial loads a trace, costing seconds rather than
milliseconds. Seeds are fixed, so a failure reproduces.
"""

import random
from pathlib import Path

import pytest

from gcmon.exporters.perfetto_builders import build_trace, build_trace_packet, build_track_event
from gcmon.exporters.perfetto_process_lifetime import (
    _clip_spans_to_laminar,
    _emit_process_lifetime_slice,
    _emit_process_lifetime_track_descriptor,
)
from gcmon.exporters.perfetto_proto import TrackEventType
from gcmon.exporters.perfetto_track_state import PerfettoTrackState
from tests.helpers import open_trace_processor

pytestmark = pytest.mark.fuzz

SEQUENCE_ID = 4242
TRIALS = 12


def _random_spans(rng: random.Random) -> list[tuple[int, int, int]]:
    """Spans over a tiny coordinate space, so that equal
    starts, equal ends and zero-length spans -- the only shapes where
    emission order decides anything -- are common rather than rare."""
    return [
        (pid, start, start + rng.choice([0, 0, 1, 2, 5, 10]))
        for pid in range(100, 100 + rng.randint(2, 6))
        for start in (rng.randrange(0, 12),)
    ]


def _clip(
    spans: list[tuple[int, int, int]],
) -> list[tuple[int, int, int, int, int]]:
    """Clip *spans* -- ``[(pid, start, end), ...]``, one process per pid --
    and drop the epoch again.

    Emission order is what this file measures, and it does not turn on
    which process of a reused pid a span belongs to.
    """
    clipped = _clip_spans_to_laminar([(pid, 1, start, end) for pid, start, end in spans])
    return [(pid, start, end, real_start, real_end) for pid, _pid_epoch, start, end, real_start, real_end in clipped]


def _pairs(clipped: list[tuple[int, int, int, int, int]], state: PerfettoTrackState) -> list[list[bytes]]:
    return [
        _emit_process_lifetime_slice(pid, 1, start, end, state, SEQUENCE_ID, real_start, real_end)
        for pid, start, end, real_start, real_end in clipped
    ]


def _packets_in_order(clipped: list[tuple[int, int, int, int, int]], order: str, rng: random.Random) -> list[bytes]:
    """Build the ``Processes`` packets, laying the pairs out *order*'s way."""
    state = PerfettoTrackState()
    pairs = _pairs(clipped, state)
    if order == "paired":  # what finalize_perfetto_packets does
        events = [packet for pair in pairs for packet in pair]
    elif order == "pairs_reversed":
        events = [packet for pair in reversed(pairs) for packet in pair]
    elif order == "all_shuffled":
        events = [packet for pair in pairs for packet in pair]
        rng.shuffle(events)
    else:  # pragma: no cover - guards a typo in a parametrization
        raise ValueError(f"unknown order: {order}")
    return [_emit_process_lifetime_track_descriptor(state, SEQUENCE_ID), *events]


def _slices_as_read_back(packets: list[bytes], tmp_path: Path, name: str) -> tuple[int, dict[str, tuple[int, int]]]:
    """Load *packets* as a trace and return the ``Processes`` slices the
    trace processor reports, plus its ``misplaced_end_event`` counter."""
    path = tmp_path / f"{name}.pftrace"
    path.write_bytes(build_trace(packets))
    with open_trace_processor(path) as tp:
        rows = list(tp.query("SELECT value FROM stats WHERE name = 'misplaced_end_event'"))
        misplaced = rows[0].value if rows else 0
        slices = {
            row.name: (row.ts, row.dur)
            for row in tp.query(
                "SELECT s.name, s.ts, s.dur FROM slice s JOIN track t ON s.track_id = t.id WHERE t.name = 'Processes'"
            )
        }
    return misplaced, slices


def _expected(clipped: list[tuple[int, int, int, int, int]]) -> dict[str, tuple[int, int]]:
    return {f"Process {pid}": (start, end - start) for pid, start, end, _rs, _re in clipped}


@pytest.mark.parametrize("seed", range(TRIALS))
def test_paired_emission_reads_back_exactly(seed: int, tmp_path: Path) -> None:
    """Every span draws the interval the sweep assigned it, with no
    ``misplaced_end_event``, whatever laminar shape went in."""
    rng = random.Random(seed)
    clipped = _clip(_random_spans(rng))
    misplaced, slices = _slices_as_read_back(_packets_in_order(clipped, "paired", rng), tmp_path, f"paired{seed}")
    assert misplaced == 0
    assert slices == _expected(clipped)


@pytest.mark.parametrize("order", ["pairs_reversed", "all_shuffled"])
def test_orders_adr_0011_rejects_really_do_break(order: str, tmp_path: Path) -> None:
    """The negative control: without it, the positive test above would
    pass just as well in a world where emission order did not matter.

    Asserted over the trial set, not per trial: a given shape may have no
    colliding timestamps, and then every order is legitimately fine.
    """
    broken = 0
    for seed in range(TRIALS):
        rng = random.Random(seed)
        clipped = _clip(_random_spans(rng))
        misplaced, slices = _slices_as_read_back(_packets_in_order(clipped, order, rng), tmp_path, f"{order}{seed}")
        if misplaced != 0 or slices != _expected(clipped):
            broken += 1
    assert broken > 0, (
        f"{order} produced a correct trace on all {TRIALS} trials. This is a claim about the "
        "trace processor, not about gcmon: a newer one tolerating this order would fail here "
        "while gcmon is fine. Re-read ADR-0011's emission-order argument before relaxing it."
    )


# The deepest slice stack the trace processor closes. Measured, not
# documented: at 512 every slice reads back exactly, and each level
# beyond leaves one more slice open. Both tests below pin one side of it.
_PAIRABLE_NESTING_DEPTH: int = 512


def _co_terminating(depth: int) -> list[tuple[int, int, int]]:
    """``depth`` spans that all end together, one starting per ns.

    The shape monitor-reported liveness makes ordinary: every pid still
    alive when the loop stops is stamped with the same final timestamp,
    and co-terminating spans never clip -- the sweep breaks out on
    ``outer_end >= end`` -- so they nest, one level per process.
    """
    return [(100 + i, i, depth) for i in range(depth)]


def test_co_terminating_spans_nest_rather_than_clip() -> None:
    """The premise of the two measurements below, checked on the sweep
    itself so a change there cannot quietly turn them into tests of some
    other shape."""
    spans = _co_terminating(8)
    assert [(pid, start, end) for pid, start, end, _rs, _re in _clip(spans)] == spans


def test_nesting_pairs_up_to_the_trace_processor_limit(tmp_path: Path) -> None:
    """512 co-terminating processes read back exactly, with no
    ``misplaced_end_event``.

    ADR-0011 accepts what deep nesting does to the *UI*; what the parser
    does with it is a different question, since a mispair there is wrong
    data rather than an ugly picture.
    """
    clipped = _clip(_co_terminating(_PAIRABLE_NESTING_DEPTH))
    misplaced, slices = _slices_as_read_back(
        _packets_in_order(clipped, "paired", random.Random(0)),
        tmp_path,
        "deep_nesting_ok",
    )
    assert misplaced == 0
    assert slices == _expected(clipped)


def test_nesting_past_the_limit_loses_slices_silently(tmp_path: Path) -> None:
    """Past 512 the trace processor drops slices: each span beyond the
    limit produces no row at all, and nothing in the trace says so.

    gcmon writes a well-formed pair for every span either way, so the
    limit sits in the reader. Pinned here so a trace processor that
    lifts it shows up as a failure rather than going unnoticed. v57.2
    lost the same spans by leaving them open at ``dur = -1``; v58.2
    drops the rows instead, and both keep quiet about it.
    """
    depth = _PAIRABLE_NESTING_DEPTH + 8
    clipped = _clip(_co_terminating(depth))
    misplaced, slices = _slices_as_read_back(
        _packets_in_order(clipped, "paired", random.Random(0)),
        tmp_path,
        "deep_nesting_over",
    )
    expected = _expected(clipped)
    dropped = expected.keys() - slices.keys()
    assert misplaced == 0, "the loss is silent: the parser reports nothing"
    assert len(dropped) == depth - _PAIRABLE_NESTING_DEPTH, (
        f"expected exactly the {depth - _PAIRABLE_NESTING_DEPTH} innermost slices to go missing, got {len(dropped)}"
    )
    # The ones that fit are untouched, and none is left open.
    assert slices == {name: span for name, span in expected.items() if name not in dropped}


def test_a_named_end_matches_by_name_not_by_stack_position(tmp_path: Path) -> None:
    """Pin the mechanism ADR-0011's emission argument rests on.

    It is why a shared start emitted inner-first corrupts *both* slices,
    and why two ENDs on one timestamp need no rule. If it ever changes,
    that argument has to be redone rather than patched.
    """
    state = PerfettoTrackState()
    track_uuid = state.get_or_create_process_lifetime_track_uuid()

    def event(ts: int, event_type: TrackEventType, name: str) -> bytes:
        return build_trace_packet(
            SEQUENCE_ID,
            timestamp=ts,
            track_event=build_track_event(type=event_type, track_uuid=track_uuid, name=name),
        )

    # "Process A" is closed while "Process B" sits above it on the stack.
    packets = [
        _emit_process_lifetime_track_descriptor(state, SEQUENCE_ID),
        event(0, TrackEventType.SLICE_BEGIN, "Process A"),
        event(10, TrackEventType.SLICE_BEGIN, "Process B"),
        event(20, TrackEventType.SLICE_END, "Process A"),
        event(30, TrackEventType.SLICE_END, "Process B"),
    ]
    misplaced, slices = _slices_as_read_back(packets, tmp_path, "named_end")

    assert slices["Process A"] == (0, 20), "the END named 'Process A' closed 'Process A', not the top of the stack"
    assert slices["Process B"] == (10, 10), "'Process B' was force-closed when the slice beneath it was matched"
    assert misplaced == 1, "the trailing END for 'Process B' had nothing left to close"
