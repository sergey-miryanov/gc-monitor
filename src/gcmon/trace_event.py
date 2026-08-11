"""Trace Event model types and factory functions."""

from collections.abc import Mapping
from typing import Literal

import msgspec

__all__ = [
    "LOSS_TID_BASE",
    "RSS_TID",
    "BeginEvent",
    "CounterEvent",
    "EndEvent",
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

# Loss is an interpreter's, but it gets a row of its own: a row that holds
# nothing else is the one you can find, and a red bar among the GC slices is
# the one you have to hunt for. One row per interpreter is enough — a poll's
# windows share a left edge, so its generations' spans nest rather than cross,
# and `stack_order` puts them out widest first.
LOSS_TID_BASE: int = -2


def loss_tid(iid: int) -> int:
    """The tid interpreter *iid*'s loss track is drawn on: -2, -3, ..."""
    return LOSS_TID_BASE - iid


def loss_iid(tid: int) -> int:
    """The interpreter behind a loss tid. A `TraceEvent` carries no `iid`."""
    return LOSS_TID_BASE - tid


class NameInfo(msgspec.Struct):
    name: str


class BeginEvent(msgspec.Struct):
    name: str
    cat: str
    ph: Literal["B"]
    ts: int
    pid: int
    tid: int
    # Mostly counters. A string carries a range meant to be read as one thing,
    # such as `missing_collections`; a nested mapping groups counts under one
    # heading, such as a generation's on a `GC Loss` slice. One level deep, as
    # far as the UI and the trace processor's flattened arg names stay
    # readable.
    args: dict[str, int | str | dict[str, int | str]]


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
    args: Mapping[str, int | str | dict[str, int | str]],
) -> BeginEvent:
    """A slice's opening event.

    *args* is a ``Mapping`` so a caller with a plain ``dict[str, int]`` needs
    no annotation of its own; ``dict`` is invariant and every counter-only
    call site would otherwise have to widen to match the one arg that carries
    a string.
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
