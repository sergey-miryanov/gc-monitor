"""Format-neutral intermediate representation (IR) for GC trace events.

Both the Chrome Trace Event exporter and the Perfetto exporter build events
from this shared representation. Format-specific serializers convert IR
events into their wire format (JSON TypedDicts or Perfetto protobuf bytes).

The IR uses nanoseconds for all timestamps and never carries a wire-format
``ph`` field, keeping it free of format-specific concerns.
"""

from typing import Literal, NotRequired, TypedDict, cast

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
    "CounterData",
    "IRCounterEvent",
    "IRIncrementalEvent",
    "IRInstantEvent",
    "IRPauseEvent",
    "IRProcessMeta",
    "IRThreadEvent",
    "NameInfo",
    "PauseData",
    "TraceEvent",
    "convert_instant_to_ir",
    "convert_item_to_ir",
    "convert_to_ir",
    "ir_counter_event",
    "ir_inc_event",
    "ir_instant_event",
    "ir_pause_event",
    "ir_process_meta",
    "ir_thread_meta",
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
    finalized_garbage_count: NotRequired[int]
    deleted_garbage_count: NotRequired[int]
    clear_weakrefs_count: NotRequired[int]


class CounterData(TypedDict):
    collected: int
    uncollectable: int
    candidates: int
    increment_size: NotRequired[int]
    alive_size: NotRequired[int]
    heap_size: int
    finalized_garbage_count: NotRequired[int]
    deleted_garbage_count: NotRequired[int]
    clear_weakrefs_count: NotRequired[int]


class NameInfo(TypedDict):
    name: str


class IRPauseEvent(TypedDict):
    name: str
    cat: str
    ts_start_ns: int
    dur_ns: float
    pid: int
    tid: int
    args: PauseData


class IRIncrementalEvent(TypedDict):
    name: str
    cat: str
    ts_start_ns: int
    dur_ns: float
    pid: int
    tid: int
    args: dict[str, int]


class IRCounterEvent(TypedDict):
    name: str
    ts_ns: int
    pid: int
    tid: int
    gen: int
    args: CounterData


class IRProcessMeta(TypedDict):
    name: Literal["process_name"]
    pid: int
    args: NameInfo


class IRThreadEvent(TypedDict):
    name: Literal["thread_name"]
    pid: int
    tid: int
    args: NameInfo


class IRInstantEvent(TypedDict):
    name: str
    pid: int
    ts_ns: int


TraceEvent = IRPauseEvent | IRIncrementalEvent | IRCounterEvent | IRProcessMeta | IRThreadEvent | IRInstantEvent


def ir_process_meta(pid: int, name: str) -> IRProcessMeta:
    return {
        "name": "process_name",
        "pid": pid,
        "args": {"name": name},
    }


def ir_thread_meta(pid: int, tid: int, name: str) -> IRThreadEvent:
    return {
        "name": "thread_name",
        "pid": pid,
        "tid": tid,
        "args": {"name": name},
    }


def ir_pause_event(
    pid: int, tid: int, name: str, cat: str, ts_start_ns: int, dur_ns: float, args: PauseData
) -> IRPauseEvent:
    return {
        "name": name,
        "cat": cat,
        "ts_start_ns": ts_start_ns,
        "dur_ns": dur_ns,
        "pid": pid,
        "tid": tid,
        "args": args,
    }


def ir_inc_event(
    pid: int, tid: int, name: str, cat: str, ts_start_ns: int, dur_ns: float, args: dict[str, int]
) -> IRIncrementalEvent:
    return {
        "name": name,
        "cat": cat,
        "ts_start_ns": ts_start_ns,
        "dur_ns": dur_ns,
        "pid": pid,
        "tid": tid,
        "args": args,
    }


def ir_instant_event(pid: int, name: str, ts_ns: int) -> IRInstantEvent:
    return {
        "name": name,
        "pid": pid,
        "ts_ns": ts_ns,
    }


def ir_counter_event(pid: int, tid: int, gen: int, name: str, ts_ns: int, args: CounterData) -> IRCounterEvent:
    return {
        "name": name,
        "ts_ns": ts_ns,
        "pid": pid,
        "tid": tid,
        "gen": gen,
        "args": args,
    }


def _extra_annotations(gen: int, item: TGCStatsInfo) -> dict[str, int]:
    """Compute the conditional annotation dict for pause/sub-phase events.

    Returns only the fields that apply for this item, mirroring the chrome
    trace format's gating (increment_size only for gen<2, alive_size only
    for gen>0, count fields whenever the corresponding sub-phase is present).
    """
    result: dict[str, int] = {}
    if has_incremental(item) and gen < 2:
        result["increment_size"] = item.increment_size
    if has_mark_alive(item) and gen > 0:
        result["alive_size"] = item.alive_size
    if has_finalize_garbage(item):
        result["finalized_garbage_count"] = item.finalized_garbage_count
    if has_delete_garbage(item):
        result["deleted_garbage_count"] = item.deleted_garbage_count
    if has_clear_weakrefs(item):
        result["clear_weakrefs_count"] = item.clear_weakrefs_count
    return result


def _has_dur(item: object, start: int | None, stop: int | None) -> bool:
    return start is not None and stop is not None and stop - start > 0


def convert_item_to_ir(pid: int, item: TGCStatsInfo) -> list[TraceEvent]:
    """Build the IR event list for a single GC stats item.

    Owns all "what events to emit" decisions: the ``GC Pause`` slice, all
    sub-phase slices (when the corresponding timestamps indicate a positive
    duration), and the counter snapshot. Process and thread metadata are
    not emitted here; the exporter / serializer layer is responsible for
    emitting and deduplicating them.
    """
    gen = item.gen
    iid = item.iid
    tid = iid
    ts_start_ns = item.ts_start
    ts_stop_ns = item.ts_stop
    dur_ns = ts_stop_ns - ts_start_ns

    pause_data: PauseData = {
        "generation": gen,
        "iid": iid,
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

    extra = _extra_annotations(gen, item)
    pause_data = cast(PauseData, {**pause_data, **extra})
    counter_data = cast(CounterData, {**counter_data, **extra})

    events: list[TraceEvent] = [
        ir_pause_event(
            pid,
            tid,
            f"GC Pause (gen={gen})",
            f"gc.pause(gen={gen})",
            ts_start_ns,
            float(dur_ns),
            pause_data,
        ),
    ]

    if has_mark_alive(item) and _has_dur(item, item.ts_mark_alive_start, item.ts_mark_alive_stop):
        events.append(
            ir_inc_event(
                pid,
                tid,
                f"Mark Alive (gen={gen})",
                f"gc.mark.alive(gen={gen})",
                item.ts_mark_alive_start,
                float(item.ts_mark_alive_stop - item.ts_mark_alive_start),
                {"generation": gen, "iid": iid, **extra},
            )
        )

    if has_incremental(item) and _has_dur(item, item.ts_fill_increment_start, item.ts_fill_increment_stop):
        events.append(
            ir_inc_event(
                pid,
                tid,
                f"Fill increment (gen={gen})",
                f"gc.increment(gen={gen})",
                item.ts_fill_increment_start,
                float(item.ts_fill_increment_stop - item.ts_fill_increment_start),
                {"generation": gen, "iid": iid, **extra},
            )
        )

    if has_deduce_unreachable(item) and _has_dur(
        item, item.ts_deduce_unreachable_start, item.ts_deduce_unreachable_stop
    ):
        events.append(
            ir_inc_event(
                pid,
                tid,
                f"Deduce Unreachable (gen={gen})",
                f"gc.deduce(gen={gen})",
                item.ts_deduce_unreachable_start,
                float(item.ts_deduce_unreachable_stop - item.ts_deduce_unreachable_start),
                {"generation": gen, "iid": iid, **extra},
            )
        )

    if has_handle_weakrefs(item) and _has_dur(
        item, item.ts_handle_weakref_callbacks_start, item.ts_handle_weakref_callbacks_stop
    ):
        events.append(
            ir_inc_event(
                pid,
                tid,
                f"Handle Weakrefs Callbacks (gen={gen})",
                f"gc.weakrefs(gen={gen})",
                item.ts_handle_weakref_callbacks_start,
                float(item.ts_handle_weakref_callbacks_stop - item.ts_handle_weakref_callbacks_start),
                {"generation": gen, "iid": iid, **extra},
            )
        )

    if has_finalize_garbage(item) and _has_dur(
        item, item.ts_handle_weakref_callbacks_stop, item.ts_finalize_garbage_stop
    ):
        events.append(
            ir_inc_event(
                pid,
                tid,
                f"Finalize Garbage (gen={gen})",
                f"gc.finalize(gen={gen})",
                item.ts_handle_weakref_callbacks_stop,
                float(item.ts_finalize_garbage_stop - item.ts_handle_weakref_callbacks_stop),
                {"generation": gen, "iid": iid, **extra},
            )
        )

    if has_handle_resurrected(item) and _has_dur(item, item.ts_finalize_garbage_stop, item.ts_handle_resurrected_stop):
        events.append(
            ir_inc_event(
                pid,
                tid,
                f"Handle Resurrected (gen={gen})",
                f"gc.resurrect(gen={gen})",
                item.ts_finalize_garbage_stop,
                float(item.ts_handle_resurrected_stop - item.ts_finalize_garbage_stop),
                {"generation": gen, "iid": iid, **extra},
            )
        )

    if has_clear_weakrefs(item) and _has_dur(item, item.ts_handle_resurrected_stop, item.ts_clear_weakrefs_stop):
        events.append(
            ir_inc_event(
                pid,
                tid,
                f"Clear Weakrefs (gen={gen})",
                f"gc.clear_weakrefs(gen={gen})",
                item.ts_handle_resurrected_stop,
                float(item.ts_clear_weakrefs_stop - item.ts_handle_resurrected_stop),
                {"generation": gen, "iid": iid, **extra},
            )
        )

    if has_delete_garbage(item) and _has_dur(item, item.ts_delete_garbage_start, item.ts_delete_garbage_stop):
        events.append(
            ir_inc_event(
                pid,
                tid,
                f"Delete Garbage (gen={gen})",
                f"gc.delete(gen={gen})",
                item.ts_delete_garbage_start,
                float(item.ts_delete_garbage_stop - item.ts_delete_garbage_start),
                {"generation": gen, "iid": iid, **extra},
            )
        )

    events.append(
        ir_counter_event(
            pid,
            tid,
            gen,
            f"G{gen}",
            ts_start_ns,
            counter_data,
        )
    )

    return events


def convert_instant_to_ir(pid: int, item: TInstantMsg) -> list[TraceEvent]:
    return [ir_instant_event(pid, item.name, item.ts)]


def convert_to_ir(
    items: dict[int, list[TGCStatsInfo | TInstantMsg]],
) -> list[TraceEvent]:
    events: list[TraceEvent] = []
    for pid, pid_items in items.items():
        events.append(ir_process_meta(pid, f"{pid}"))
        threads: set[int] = set()
        pid_events: list[TraceEvent] = []
        for item in pid_items:
            if is_instant(item):
                pid_events.extend(convert_instant_to_ir(pid, item))
            elif is_gc_stats(item):
                threads.add(item.iid)
                pid_events.extend(convert_item_to_ir(pid, item))

        events.extend(ir_thread_meta(pid, tid, f"{pid}:{tid}") for tid in threads)
        events.extend(pid_events)

    return events
