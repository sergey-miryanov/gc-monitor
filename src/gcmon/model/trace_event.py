"""The events every output format is built from, and the factories that build them.

One converter produces these and every encoder reads them (ADR-0007). Their
shape is the Chrome Trace Format's, which is where gcmon started; `ts` is
nanoseconds throughout (ADR-0009).
"""

from typing import Literal

import msgspec

__all__ = [
    "LOSS_TID_BASE",
    "RSS_TID",
    "ArgGroup",
    "BeginEvent",
    "CounterEvent",
    "EndEvent",
    "EventArgs",
    "InstantEvent",
    "NameInfo",
    "ProcessMeta",
    "ThreadMeta",
    "TraceEvent",
    "begin_event",
    "counter_event",
    "end_event",
    "instant_event",
    "loss_iid",
    "loss_tid",
    "process_meta",
    "thread_meta",
]

# These numbers are what they are because the Chrome format, which this shape
# came from, identified a track by `(pid, tid)` alone: a row belonging to no
# interpreter had to take a tid no interpreter would claim. gcmon names none of
# these rows with `thread_meta`, so Perfetto leaves them off its thread list.
#
# RSS belongs to the process, with no interpreter behind it.
RSS_TID: int = -1

# Loss belongs to an interpreter and gets a row of its own, per ADR-0015. One
# row holds every poll: a poll draws a single span there whatever went blind in
# it, and consecutive polls tile the timeline, so no two spans overlap.
LOSS_TID_BASE: int = -2


def loss_tid(iid: int) -> int:
    """The tid carrying interpreter *iid*'s loss track: -2, -3, ..."""
    return LOSS_TID_BASE - iid


def loss_iid(tid: int) -> int:
    """The interpreter behind a loss tid. A `TraceEvent` carries no `iid`."""
    return LOSS_TID_BASE - tid


type ArgGroup = dict[str, int | str]

# Perfetto and the trace processor flatten a group's names onto the slice's
# own, so a second level of nesting reads as noise.
type EventArgs = dict[str, int | str | ArgGroup]


class NameInfo(msgspec.Struct):
    name: str


class BeginEvent(msgspec.Struct):
    name: str
    cat: str
    ph: Literal["B"]
    ts: int
    pid: int
    tid: int
    args: EventArgs


class EndEvent(msgspec.Struct):
    name: str
    cat: str
    ph: Literal["E"]
    ts: int
    pid: int
    tid: int


class InstantEvent(msgspec.Struct):
    name: str
    ph: Literal["I"]
    s: Literal["p"]
    ts: int
    pid: int


class CounterEvent(msgspec.Struct):
    name: str
    ph: Literal["C"]
    ts: int
    pid: int
    tid: int
    args: dict[str, int | float]


class ProcessMeta(msgspec.Struct):
    name: Literal["process_name"]
    ph: Literal["M"]
    pid: int
    args: NameInfo


class ThreadMeta(msgspec.Struct):
    name: Literal["thread_name"]
    ph: Literal["M"]
    pid: int
    tid: int
    args: NameInfo


TraceEvent = BeginEvent | EndEvent | CounterEvent | ProcessMeta | ThreadMeta | InstantEvent


def process_meta(pid: int, name: str) -> ProcessMeta:
    return ProcessMeta(
        name="process_name",
        ph="M",
        pid=pid,
        args=NameInfo(name=name),
    )


def thread_meta(pid: int, tid: int, name: str) -> ThreadMeta:
    return ThreadMeta(
        name="thread_name",
        ph="M",
        pid=pid,
        tid=tid,
        args=NameInfo(name=name),
    )


def begin_event(
    pid: int,
    tid: int,
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
        pid=pid,
        tid=tid,
        args=args,
    )


def end_event(
    pid: int,
    tid: int,
    name: str,
    cat: str,
    ts_ns: int,
) -> EndEvent:
    return EndEvent(
        name=name,
        cat=cat,
        ph="E",
        ts=ts_ns,
        pid=pid,
        tid=tid,
    )


def instant_event(
    pid: int,
    name: str,
    ts_ns: int,
) -> InstantEvent:
    return InstantEvent(
        name=name,
        ph="I",
        s="p",
        pid=pid,
        ts=ts_ns,
    )


def counter_event(pid: int, tid: int, name: str, ts_ns: int, args: dict[str, int | float]) -> CounterEvent:
    return CounterEvent(
        name=name,
        ph="C",
        ts=ts_ns,
        pid=pid,
        tid=tid,
        args=args,
    )
