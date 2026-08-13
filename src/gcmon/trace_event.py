"""Trace Event model types and factory functions."""

from collections.abc import Mapping
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

# A track is `(pid, tid)` and nothing else in the Chrome format, so anything
# that is not an interpreter needs a tid no interpreter will claim. A negative
# one also skips `thread_meta`, which keeps it from being drawn and named as a
# thread.
#
# RSS is process-wide with no interpreter behind it at all.
RSS_TID: int = -1

# Loss is an interpreter's, on a row of its own per ADR-0015. One row per
# interpreter is enough: a poll draws one span there whatever went blind in
# it, and consecutive polls tile the timeline, so the spans cannot overlap.
LOSS_TID_BASE: int = -2


def loss_tid(iid: int) -> int:
    """The tid interpreter *iid*'s loss track is drawn on: -2, -3, ..."""
    return LOSS_TID_BASE - iid


def loss_iid(tid: int) -> int:
    """The interpreter behind a loss tid. A `TraceEvent` carries no `iid`."""
    return LOSS_TID_BASE - tid


type ArgGroup = dict[str, int | str]

# A group goes no deeper because the UI and the trace processor flatten its
# names onto the slice's own, which stops reading well past one level.
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
    args: Mapping[str, int | str | ArgGroup],
) -> BeginEvent:
    """A slice's opening event.

    *args* takes a ``Mapping`` because ``dict`` is invariant: a call site
    holding a plain ``dict[str, int]`` would otherwise have to widen.
    """
    return BeginEvent(
        name=name,
        cat=cat,
        ph="B",
        ts=ts_ns,
        pid=pid,
        tid=tid,
        args=dict(args),
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
