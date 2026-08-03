"""Trace Event model types and factory functions."""

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
# wanting a track of its own needs a tid no interpreter will claim. Negative
# ones also skip `thread_meta`, which is what keeps them from being drawn and
# named as threads.
RSS_TID: int = -1  # one sample stream per process, no interpreter to attach to
LOSS_TID_BASE: int = -2  # one track per interpreter, counting downwards


def loss_tid(iid: int) -> int:
    """The track interpreter *iid* draws its loss spans on.

    Not `iid` itself: that is where its GC slices go, and a loss span crosses
    them whenever the records bounding a gap span two generations. See
    ADR-0015.
    """
    return LOSS_TID_BASE - iid


def loss_iid(tid: int) -> int:
    """The interpreter behind a loss tid."""
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
    args: dict[str, int]


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


def begin_event(pid: int, tid: int, name: str, cat: str, ts_ns: int, args: dict[str, int]) -> BeginEvent:
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
