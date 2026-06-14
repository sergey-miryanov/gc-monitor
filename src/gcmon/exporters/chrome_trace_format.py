"""Chrome Trace Event format types and conversion utilities."""

from typing import Literal

import msgspec

from ..data import ts_to_us
from ..protocol import (
    TGCStatsInfo,
    TInstantMsg,
    has_clear_weakrefs,
    has_deduce_unreachable,
    has_delete_garbage,
    has_finalize_garbage,
    has_handle_resurrected,
    has_handle_weakrefs,
    has_incremental,
    has_mark_alive,
    is_gc_stats,
    is_instant,
)

__all__ = [
    "BeginEvent",
    "CounterEvent",
    "EndEvent",
    "InstantEvent",
    "NameInfo",
    "ProcessMeta",
    "ThreadMeta",
    "TraceEvent",
    "begin_event",
    "convert_item_to_trace_format",
    "convert_to_trace_format",
    "counter_event",
    "end_event",
    "instant_event",
    "process_meta",
    "thread_meta",
]


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
    args: dict[str, int]


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
    pid: int, tid: int, name: str, cat: str, ts_us: int, args: dict[str, int]
) -> BeginEvent:
    return BeginEvent(
        name=name,
        cat=cat,
        ph="B",
        ts=ts_us,
        pid=pid,
        tid=tid,
        args=args,
    )


def end_event(
    pid: int, tid: int, name: str, cat: str, ts_us: int,
) -> EndEvent:
    return EndEvent(
        name=name,
        cat=cat,
        ph="E",
        ts=ts_us,
        pid=pid,
        tid=tid,
    )


def instant_event(
    pid: int, name: str, ts_us: int,
) -> InstantEvent:
    return InstantEvent(
        name=name,
        ph="I",
        s="p",
        pid=pid,
        ts=ts_us,
    )


def counter_event(pid: int, tid: int, name: str, ts_us: int, args: dict[str, int]) -> CounterEvent:
    return CounterEvent(
        name=name,
        ph="C",
        ts=ts_us,
        pid=pid,
        tid=tid,
        args=args,
    )


def convert_item_to_trace_format(pid: int, item: TGCStatsInfo) -> list[TraceEvent]:
    gen = item.gen
    iid = item.iid
    tid = iid
    ts_start_us = ts_to_us(item.ts_start)
    ts_stop_us = ts_to_us(item.ts_stop)

    pause_data: dict[str, int] = {
        "generation": gen,
        "iid": iid,
        "collections": item.collections,
        "heap_size": item.heap_size,
        "collected": item.collected,
        "uncollectable": item.uncollectable,
        "candidates": item.candidates,
    }

    counter_data = {
        "collected": item.collected,
        "uncollectable": item.uncollectable,
        "candidates": item.candidates,
        "heap_size": item.heap_size,
    }

    if has_incremental(item) and gen < 2:
        pause_data["increment_size"] = item.increment_size
        counter_data["increment_size"] = item.increment_size

    if has_mark_alive(item) and gen > 0:
        pause_data["alive_size"] = item.alive_size
        counter_data["alive_size"] = item.alive_size

    if has_finalize_garbage(item):
        pause_data["finalized_garbage_count"] = item.finalized_garbage_count
        counter_data["finalized_garbage_count"] = item.finalized_garbage_count

    if has_delete_garbage(item):
        pause_data["deleted_garbage_count"] = item.deleted_garbage_count
        counter_data["deleted_garbage_count"] = item.deleted_garbage_count

    if has_clear_weakrefs(item):
        pause_data["clear_weakrefs_count"] = item.clear_weakrefs_count
        counter_data["clear_weakrefs_count"] = item.clear_weakrefs_count

    events: list[TraceEvent] = []
    events.append(
        begin_event(
            pid,
            tid,
            f"GC Pause (gen={gen})",
            f"gc.pause(gen={gen})",
            ts_start_us,
            pause_data,
        )
    )

    if has_mark_alive(item) and item.ts_mark_alive_stop - item.ts_mark_alive_start > 0:
        inc_data: dict[str, int] = {"generation": gen, "iid": iid, "alive_size": item.alive_size}
        events.append(
            begin_event(
                pid,
                tid,
                f"Mark Alive (gen={gen})",
                f"gc.mark.alive(gen={gen})",
                ts_to_us(item.ts_mark_alive_start),
                inc_data,
            )
        )
        events.append(
            end_event(
                pid,
                tid,
                f"Mark Alive (gen={gen})",
                f"gc.mark.alive(gen={gen})",
                ts_to_us(item.ts_mark_alive_stop),
            )
        )

    if has_incremental(item) and item.ts_fill_increment_stop - item.ts_fill_increment_start > 0:
        inc_data = {"generation": gen, "iid": iid, "increment_size": item.increment_size}
        events.append(
            begin_event(
                pid,
                tid,
                f"Fill increment (gen={gen})",
                f"gc.increment(gen={gen})",
                ts_to_us(item.ts_fill_increment_start),
                inc_data,
            )
        )
        events.append(
            end_event(
                pid,
                tid,
                f"Fill increment (gen={gen})",
                f"gc.increment(gen={gen})",
                ts_to_us(item.ts_fill_increment_stop),
            )
        )

    if has_deduce_unreachable(item) and item.ts_deduce_unreachable_stop - item.ts_deduce_unreachable_start > 0:
        inc_data = {"generation": gen, "iid": iid}
        events.append(
            begin_event(
                pid,
                tid,
                f"Deduce Unreachable (gen={gen})",
                f"gc.deduce(gen={gen})",
                ts_to_us(item.ts_deduce_unreachable_start),
                inc_data,
            )
        )
        events.append(
            end_event(
                pid,
                tid,
                f"Deduce Unreachable (gen={gen})",
                f"gc.deduce(gen={gen})",
                ts_to_us(item.ts_deduce_unreachable_stop),
            )
        )

    if has_handle_weakrefs(item) and item.ts_handle_weakref_callbacks_stop - item.ts_handle_weakref_callbacks_start > 0:
        inc_data = {"generation": gen, "iid": iid}
        events.append(
            begin_event(
                pid, tid,
                f"Handle Weakrefs Callbacks (gen={gen})",
                f"gc.weakrefs(gen={gen})",
                ts_to_us(item.ts_handle_weakref_callbacks_start),
                inc_data,
            )
        )
        events.append(
            end_event(
                pid, tid,
                f"Handle Weakrefs Callbacks (gen={gen})",
                f"gc.weakrefs(gen={gen})",
                ts_to_us(item.ts_handle_weakref_callbacks_stop),
            )
        )

    if (has_finalize_garbage(item) and item.ts_finalize_garbage_stop - item.ts_handle_weakref_callbacks_stop > 0):
        inc_data = {"generation": gen, "iid": iid}
        events.append(
            begin_event(
                pid, tid,
                f"Finalize Garbage (gen={gen})",
                f"gc.finalize(gen={gen})",
                ts_to_us(item.ts_handle_weakref_callbacks_stop),
                inc_data,
            )
        )
        events.append(
            end_event(
                pid, tid,
                f"Finalize Garbage (gen={gen})",
                f"gc.finalize(gen={gen})",
                ts_to_us(item.ts_finalize_garbage_stop),
            )
        )

    if (has_handle_resurrected(item)
            and item.ts_handle_resurrected_stop - item.ts_finalize_garbage_stop > 0):
        inc_data = {"generation": gen, "iid": iid}
        events.append(
            begin_event(
                pid, tid,
                f"Handle Resurrected (gen={gen})",
                f"gc.resurrect(gen={gen})",
                ts_to_us(item.ts_finalize_garbage_stop),
                inc_data,
            )
        )
        events.append(
            end_event(
                pid, tid,
                f"Handle Resurrected (gen={gen})",
                f"gc.resurrect(gen={gen})",
                ts_to_us(item.ts_handle_resurrected_stop),
            )
        )

    if (has_clear_weakrefs(item)
            and item.ts_clear_weakrefs_stop - item.ts_handle_resurrected_stop > 0):
        inc_data = {"generation": gen, "iid": iid}
        events.append(
            begin_event(
                pid, tid,
                f"Clear Weakrefs (gen={gen})",
                f"gc.clear_weakrefs(gen={gen})",
                ts_to_us(item.ts_handle_resurrected_stop),
                inc_data,
            )
        )
        events.append(
            end_event(
                pid, tid,
                f"Clear Weakrefs (gen={gen})",
                f"gc.clear_weakrefs(gen={gen})",
                ts_to_us(item.ts_clear_weakrefs_stop),
            )
        )

    if has_delete_garbage(item) and item.ts_delete_garbage_stop - item.ts_delete_garbage_start > 0:
        inc_data = {"generation": gen, "iid": iid}
        events.append(
            begin_event(
                pid, tid,
                f"Delete Garbage (gen={gen})",
                f"gc.delete(gen={gen})",
                ts_to_us(item.ts_delete_garbage_start),
                inc_data,
            )
        )
        events.append(
            end_event(
                pid, tid,
                f"Delete Garbage (gen={gen})",
                f"gc.delete(gen={gen})",
                ts_to_us(item.ts_delete_garbage_stop),
            )
        )

    events.append(
        end_event(
            pid,
            tid,
            f"GC Pause (gen={gen})",
            f"gc.pause(gen={gen})",
            ts_stop_us,
        )
    )

    events.append(
        counter_event(
            pid,
            tid,
            f"G{gen}",
            ts_start_us,
            counter_data,
        )
    )

    return events


def convert_to_trace_format(
    items: dict[int, list[TGCStatsInfo | TInstantMsg]]
) -> list[TraceEvent]:
    events: list[TraceEvent] = []
    for pid, pid_items in items.items():
        events.append(process_meta(pid, f"{pid}"))
        threads: set[int] = set()
        pid_events: list[TraceEvent] = []
        for item in pid_items:
            if is_instant(item):
                pid_events.append(instant_event(pid, item.name, ts_to_us(item.ts)))
            elif is_gc_stats(item):
                threads.add(item.iid)
                pid_events.extend(convert_item_to_trace_format(pid, item))

        events.extend(thread_meta(pid, tid, f"{pid}:{tid}") for tid in threads)
        events.extend(pid_events)

    return events
