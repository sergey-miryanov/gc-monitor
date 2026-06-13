"""Chrome Trace Event format: builders and IR-to-chrome serializer.

The format-neutral event representation lives in :mod:`gcmon.exporters.ir`.
This module re-exports the chrome wire-shape TypedDicts and builders, and
provides :func:`ir_to_chrome` plus the legacy :func:`convert_item_to_trace_format`
/ :func:`convert_to_trace_format` entry points that route through the IR.
"""

from typing import Literal, TypedDict, cast

from ..data import dur_to_us, ts_to_us
from ..protocol import TGCStatsInfo, TInstantMsg
from .ir import (
    CounterData,
    IRCounterEvent,
    IRIncrementalEvent,
    IRInstantEvent,
    IRPauseEvent,
    IRProcessMeta,
    IRThreadEvent,
    NameInfo,
    PauseData,
    convert_item_to_ir,
    convert_to_ir,
)
from .ir import (
    TraceEvent as IRTraceEvent,
)

__all__ = [
    "CounterData",
    "CounterEvent",
    "IncrementalEvent",
    "InstantEvent",
    "NameInfo",
    "PauseData",
    "PauseEvent",
    "ProcessMeta",
    "ThreadMeta",
    "TraceEvent",
    "convert_item_to_trace_format",
    "convert_to_trace_format",
    "counter_event",
    "inc_event",
    "instant_event",
    "ir_to_chrome",
    "pause_event",
    "process_meta",
    "thread_meta",
]


class InstantEvent(TypedDict):
    name: str
    ph: Literal["I"]
    s: Literal["p"]
    ts: int
    pid: int


class PauseEvent(TypedDict):
    name: str
    cat: str
    ph: Literal["X"]
    ts: int
    dur: float
    pid: int
    tid: int
    args: PauseData


class IncrementalEvent(TypedDict):
    name: str
    cat: str
    ph: Literal["X"]
    ts: int
    dur: float
    pid: int
    tid: int
    args: dict[str, int]


class CounterEvent(TypedDict):
    name: str
    ph: Literal["C"]
    ts: int
    pid: int
    tid: int
    args: CounterData


class ProcessMeta(TypedDict):
    name: Literal["process_name"]
    ph: Literal["M"]
    pid: int
    args: NameInfo


class ThreadMeta(TypedDict):
    name: Literal["thread_name"]
    ph: Literal["M"]
    pid: int
    tid: int
    args: NameInfo


TraceEvent = (
    PauseEvent
    | IncrementalEvent
    | CounterEvent
    | ProcessMeta
    | ThreadMeta
    | InstantEvent
)


def process_meta(pid: int, name: str) -> ProcessMeta:
    return {
        "name": "process_name",
        "ph": "M",
        "pid": pid,
        "args": {"name": name},
    }


def thread_meta(pid: int, tid: int, name: str) -> ThreadMeta:
    return {
        "name": "thread_name",
        "ph": "M",
        "pid": pid,
        "tid": tid,
        "args": {"name": name},
    }


def pause_event(
    pid: int, tid: int, name: str, cat: str, ts_us: int, dur_us: float, args: PauseData
) -> PauseEvent:
    return {
        "name": name,
        "cat": cat,
        "ph": "X",
        "ts": ts_us,
        "dur": dur_us,
        "pid": pid,
        "tid": tid,
        "args": args,
    }


def inc_event(
    pid: int, tid: int, name: str, cat: str, ts_us: int, dur_us: float, args: dict[str, int]
) -> IncrementalEvent:
    return {
        "name": name,
        "cat": cat,
        "ph": "X",
        "ts": ts_us,
        "dur": dur_us,
        "pid": pid,
        "tid": tid,
        "args": args,
    }


def instant_event(
    pid: int, name: str, ts_us: int,
) -> InstantEvent:
    return {
        "name": name,
        "ph": "I",
        "s": "p",
        "pid": pid,
        "ts": ts_us,
    }


def counter_event(pid: int, tid: int, name: str, ts_us: int, args: CounterData) -> CounterEvent:
    return {
        "name": name,
        "ph": "C",
        "ts": ts_us,
        "pid": pid,
        "tid": tid,
        "args": args,
    }


def _ir_pause_to_chrome(event: IRPauseEvent) -> PauseEvent:
    ts_start_ns = event["ts_start_ns"]
    ts_us = ts_to_us(ts_start_ns)
    dur_us = dur_to_us(ts_start_ns, ts_start_ns + int(event["dur_ns"]))
    return {
        "name": event["name"],
        "cat": event["cat"],
        "ph": "X",
        "ts": ts_us,
        "dur": dur_us,
        "pid": event["pid"],
        "tid": event["tid"],
        "args": event["args"],
    }


def _ir_incremental_to_chrome(event: IRIncrementalEvent) -> IncrementalEvent:
    ts_start_ns = event["ts_start_ns"]
    ts_us = ts_to_us(ts_start_ns)
    dur_us = dur_to_us(ts_start_ns, ts_start_ns + int(event["dur_ns"]))
    return {
        "name": event["name"],
        "cat": event["cat"],
        "ph": "X",
        "ts": ts_us,
        "dur": dur_us,
        "pid": event["pid"],
        "tid": event["tid"],
        "args": event["args"],
    }


def _ir_counter_to_chrome(event: IRCounterEvent) -> CounterEvent:
    return {
        "name": event["name"],
        "ph": "C",
        "ts": ts_to_us(event["ts_ns"]),
        "pid": event["pid"],
        "tid": event["tid"],
        "args": event["args"],
    }


def _ir_instant_to_chrome(event: IRInstantEvent) -> InstantEvent:
    return {
        "name": event["name"],
        "ph": "I",
        "s": "p",
        "pid": event["pid"],
        "ts": ts_to_us(event["ts_ns"]),
    }


def _ir_process_to_chrome(event: IRProcessMeta) -> ProcessMeta:
    return {
        "name": "process_name",
        "ph": "M",
        "pid": event["pid"],
        "args": event["args"],
    }


def _ir_thread_to_chrome(event: IRThreadEvent) -> ThreadMeta:
    return {
        "name": "thread_name",
        "ph": "M",
        "pid": event["pid"],
        "tid": event["tid"],
        "args": event["args"],
    }


def ir_to_chrome(events: list[IRTraceEvent]) -> list[TraceEvent]:
    """Serialize a list of IR events to chrome wire-format events.

    Adds the chrome-specific ``ph`` field and converts nanosecond timestamps
    to microseconds. The IR event kind is determined by the presence of
    timestamp and ``args`` fields.
    """
    out: list[TraceEvent] = []
    for event in events:
        if "ts_start_ns" in event:
            args = cast("PauseData | dict[str, int]", event.get("args"))
            if isinstance(args, dict) and "collected" in args:
                out.append(_ir_pause_to_chrome(cast(IRPauseEvent, event)))
            else:
                out.append(_ir_incremental_to_chrome(cast(IRIncrementalEvent, event)))
        elif "ts_ns" in event:
            if "tid" in event:
                out.append(_ir_counter_to_chrome(cast(IRCounterEvent, event)))
            else:
                out.append(_ir_instant_to_chrome(cast(IRInstantEvent, event)))
        else:
            if event.get("name") == "process_name":
                out.append(_ir_process_to_chrome(cast(IRProcessMeta, event)))
            else:
                out.append(_ir_thread_to_chrome(cast(IRThreadEvent, event)))
    return out


def convert_item_to_trace_format(pid: int, item: TGCStatsInfo) -> list[TraceEvent]:
    return ir_to_chrome(convert_item_to_ir(pid, item))


def convert_to_trace_format(
    items: dict[int, list[TGCStatsInfo | TInstantMsg]],
) -> list[TraceEvent]:
    return ir_to_chrome(convert_to_ir(items))

