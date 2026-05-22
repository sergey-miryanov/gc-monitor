"""Chrome Trace Event format types and conversion utilities."""

from typing import Literal, NotRequired, TypedDict

from ..data import dur_to_us, ts_to_us
from ..protocol import TGCStatsInfo, TIncrementalGCStatsInfo, is_incremental

__all__ = [
    "CounterData",
    "CounterEvent",
    "IncData",
    "IncrementalEvent",
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
    "pause_event",
    "process_meta",
    "thread_meta",
]


class PauseData(TypedDict):
    generation: int
    iid: int
    collections: int
    heap_size: int
    collected: int
    uncollectable: int
    candidates: int
    increment_size: NotRequired[int]
    alive_size: NotRequired[int]


class IncData(TypedDict):
    generation: int
    iid: int
    increment_size: int
    alive_size: int


class CounterData(TypedDict):
    collected: int
    uncollectable: int
    candidates: int
    increment_size: NotRequired[int]
    alive_size: NotRequired[int]
    heap_size: int


class NameInfo(TypedDict):
    name: str


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
    args: IncData


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


TraceEvent = PauseEvent | IncrementalEvent | CounterEvent | ProcessMeta | ThreadMeta


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
    pid: int, tid: int, name: str, cat: str, ts_us: int, dur_us: float, args: IncData
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


def counter_event(pid: int, tid: int, name: str, ts_us: int, args: CounterData) -> CounterEvent:
    return {
        "name": name,
        "ph": "C",
        "ts": ts_us,
        "pid": pid,
        "tid": tid,
        "args": args,
    }


def convert_item_to_trace_format(pid: int, item: TGCStatsInfo | TIncrementalGCStatsInfo) -> list[TraceEvent]:
    tid = item.iid
    ts_us = ts_to_us(item.ts_start)
    dur_us = dur_to_us(item.ts_start, item.ts_stop)

    pause_data: PauseData = {
        "generation": item.gen,
        "iid": item.iid,
        "collections": item.collections,
        "heap_size": item.heap_size,
        "collected": item.collected,
        "uncollectable": item.uncollectable,
        "candidates": item.candidates,
    }

    counter_data: CounterData = {
        "collected": item.collected,
        "uncollectable": item.uncollectable,
        "candidates": item.candidates,
        "heap_size": item.heap_size,
    }

    if is_incremental(item):
        if item.gen < 2:
            pause_data["increment_size"] = item.increment_size
            counter_data["increment_size"] = item.increment_size

        if item.gen > 0:
            pause_data["alive_size"] = item.alive_size
            counter_data["alive_size"] = item.alive_size

    events: list[TraceEvent] = []
    events.append(
        pause_event(
            pid,
            tid,
            f"GC Pause (gen={item.gen})",
            f"gc.pause(gen={item.gen})",
            ts_us,
            dur_us,
            pause_data,
        )
    )

    if is_incremental(item):
        inc_data: IncData = {
            "generation": item.gen,
            "iid": item.iid,
            "alive_size": item.alive_size,
            "increment_size": item.increment_size,
        }
        if item.ts_mark_alive_stop - item.ts_mark_alive_start > 0:
            events.append(
                inc_event(
                    pid,
                    tid,
                    f"Mark Alive (gen={item.gen})",
                    f"gc.mark.alive(gen={item.gen})",
                    ts_to_us(item.ts_mark_alive_start),
                    dur_to_us(item.ts_mark_alive_start, item.ts_mark_alive_stop),
                    inc_data,
                )
            )
        if item.ts_fill_increment_stop - item.ts_fill_increment_start > 0:
            events.append(
                inc_event(
                    pid,
                    tid,
                    f"Fill increment (gen={item.gen})",
                    f"gc.increment(gen={item.gen})",
                    ts_to_us(item.ts_fill_increment_start),
                    dur_to_us(item.ts_fill_increment_start, item.ts_fill_increment_stop),
                    inc_data,
                )
            )
        if item.ts_deduce_uncreachable_stop - item.ts_deduce_uncreachable_start > 0:
            events.append(
                inc_event(
                    pid,
                    tid,
                    f"Deduce Unreachable (gen={item.gen})",
                    f"gc.deduce(gen={item.gen})",
                    ts_to_us(item.ts_deduce_uncreachable_start),
                    dur_to_us(item.ts_deduce_uncreachable_start, item.ts_deduce_uncreachable_stop),
                    inc_data,
                )
            )

    events.append(
        counter_event(
            pid,
            tid,
            f"G{item.gen}",
            ts_us,
            counter_data,
        )
    )

    return events


def convert_to_trace_format(items: dict[int, list[TGCStatsInfo | TIncrementalGCStatsInfo]]) -> list[TraceEvent]:
    events: list[TraceEvent] = []
    for pid, pid_items in items.items():
        events.append(process_meta(pid, f"{pid}"))
        threads: set[int] = set()
        pid_events: list[TraceEvent] = []
        for item in pid_items:
            threads.add(item.iid)
            pid_events.extend(convert_item_to_trace_format(pid, item))

        events.extend(thread_meta(pid, tid, f"{pid}:{tid}") for tid in threads)
        events.extend(pid_events)

    return events
