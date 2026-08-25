"""The events every output format is built from, and the factories that build them.

One converter fills these and every encoder reads them (ADR-0007). `ts` is
nanoseconds (ADR-0009). Every event names the `Track` it is drawn on, and
the encoder derives every other row of the trace from those.
"""

from typing import Literal

import msgspec

__all__ = [
    "ArgGroup",
    "BeginEvent",
    "CounterEvent",
    "EndEvent",
    "EventArgs",
    "InstantEvent",
    "LossTrack",
    "ProcessTrack",
    "ThreadTrack",
    "TraceEvent",
    "Track",
    "begin_event",
    "counter_event",
    "end_event",
    "instant_event",
]


class ProcessTrack(msgspec.Struct, frozen=True):
    """The process's own row: its marks, and its RSS.

    Nothing here belongs to an interpreter, so nothing here names one.
    """

    pid: int


class ThreadTrack(msgspec.Struct, frozen=True):
    """Interpreter *iid*'s row, carrying its collections."""

    pid: int
    iid: int


class LossTrack(msgspec.Struct, frozen=True):
    """Interpreter *iid*'s loss row, drawn beside its collections per
    ADR-0015.

    One row holds every poll: a poll draws a single span there whatever went
    blind in it, and consecutive polls tile the timeline, so no two spans
    overlap.
    """

    pid: int
    iid: int


type Track = ProcessTrack | ThreadTrack | LossTrack


type ArgGroup = dict[str, int | str]

# Perfetto and the trace processor flatten a group's names onto the slice's
# own, so a second level of nesting reads as noise.
type EventArgs = dict[str, int | str | ArgGroup]


class BeginEvent(msgspec.Struct):
    name: str
    cat: str
    ph: Literal["B"]
    ts: int
    track: Track
    args: EventArgs


class EndEvent(msgspec.Struct):
    name: str
    cat: str
    ph: Literal["E"]
    ts: int
    track: Track


class InstantEvent(msgspec.Struct):
    name: str
    ph: Literal["I"]
    s: Literal["p"]
    ts: int
    track: ProcessTrack


class CounterEvent(msgspec.Struct):
    metric: str
    display_name: str
    ph: Literal["C"]
    ts: int
    track: Track
    value: int | float


TraceEvent = BeginEvent | EndEvent | CounterEvent | InstantEvent


def begin_event(
    track: Track,
    name: str,
    cat: str,
    ts_ns: int,
    args: EventArgs,
) -> BeginEvent:
    # The slice owns *args*: every caller builds the dict for this one event
    # and drops it, so the event keeps it rather than copying it. A capture
    # holds one of these per phase of every collection, and the copy was the
    # largest single cost of converting one.
    return BeginEvent(
        name=name,
        cat=cat,
        ph="B",
        ts=ts_ns,
        track=track,
        args=args,
    )


def end_event(
    track: Track,
    name: str,
    cat: str,
    ts_ns: int,
) -> EndEvent:
    return EndEvent(
        name=name,
        cat=cat,
        ph="E",
        ts=ts_ns,
        track=track,
    )


def instant_event(
    track: ProcessTrack,
    name: str,
    ts_ns: int,
) -> InstantEvent:
    return InstantEvent(
        name=name,
        ph="I",
        s="p",
        track=track,
        ts=ts_ns,
    )


def counter_event(
    track: Track,
    metric: str,
    display_name: str,
    ts_ns: int,
    value: int | float,
) -> CounterEvent:
    return CounterEvent(
        metric=metric,
        display_name=display_name,
        ph="C",
        ts=ts_ns,
        track=track,
        value=value,
    )
