"""Shared conversion from GC stats items to TraceEvent objects."""

from collections.abc import Mapping, Sequence

from ..protocol import (
    TGCStatsInfo,
    TItem,
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
from ..trace_event import (
    TraceEvent,
    begin_event,
    counter_event,
    end_event,
    instant_event,
    process_meta,
    thread_meta,
)

__all__ = [
    "convert_item_to_trace_format",
    "convert_to_trace_format",
]


def convert_item_to_trace_format(pid: int, item: TGCStatsInfo) -> list[TraceEvent]:
    gen = item.gen
    iid = item.iid
    tid = iid
    ts_start_ns = item.ts_start
    ts_stop_ns = item.ts_stop

    pause_data: dict[str, int] = {
        "generation": gen,
        "iid": iid,
        "collections": item.collections,
        "heap_size": item.heap_size,
        "collected": item.collected,
        "uncollectable": item.uncollectable,
        "candidates": item.candidates,
    }

    counter_data: dict[str, int | float] = {
        "collected": item.collected,
        "candidates": item.candidates,
        "duration": item.duration,
    }
    if item.uncollectable:
        counter_data["uncollectable"] = item.uncollectable

    if has_incremental(item) and gen < 2:
        pause_data["increment_size"] = item.increment_size

    if has_mark_alive(item) and gen > 0:
        pause_data["alive_size"] = item.alive_size

    if has_finalize_garbage(item):
        pause_data["finalized_garbage_count"] = item.finalized_garbage_count

    if has_delete_garbage(item):
        pause_data["deleted_garbage_count"] = item.deleted_garbage_count

    if has_clear_weakrefs(item):
        pause_data["clear_weakrefs_count"] = item.clear_weakrefs_count

    events: list[TraceEvent] = []
    events.append(
        begin_event(
            pid,
            tid,
            f"GC Pause (gen={gen})",
            f"gc.pause(gen={gen})",
            ts_start_ns,
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
                item.ts_mark_alive_start,
                inc_data,
            )
        )
        events.append(
            end_event(
                pid,
                tid,
                f"Mark Alive (gen={gen})",
                f"gc.mark.alive(gen={gen})",
                item.ts_mark_alive_stop,
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
                item.ts_fill_increment_start,
                inc_data,
            )
        )
        events.append(
            end_event(
                pid,
                tid,
                f"Fill increment (gen={gen})",
                f"gc.increment(gen={gen})",
                item.ts_fill_increment_stop,
            )
        )

    if has_deduce_unreachable(item) and item.ts_deduce_unreachable_stop - item.ts_deduce_unreachable_start > 0:
        inc_data = {"generation": gen, "iid": iid, "candidates": item.candidates}
        events.append(
            begin_event(
                pid,
                tid,
                f"Deduce Unreachable (gen={gen})",
                f"gc.deduce(gen={gen})",
                item.ts_deduce_unreachable_start,
                inc_data,
            )
        )
        events.append(
            end_event(
                pid,
                tid,
                f"Deduce Unreachable (gen={gen})",
                f"gc.deduce(gen={gen})",
                item.ts_deduce_unreachable_stop,
            )
        )

    if has_handle_weakrefs(item) and item.ts_handle_weakref_callbacks_stop - item.ts_handle_weakref_callbacks_start > 0:
        inc_data = {"generation": gen, "iid": iid}
        events.append(
            begin_event(
                pid,
                tid,
                f"Handle Weakrefs Callbacks (gen={gen})",
                f"gc.weakrefs(gen={gen})",
                item.ts_handle_weakref_callbacks_start,
                inc_data,
            )
        )
        events.append(
            end_event(
                pid,
                tid,
                f"Handle Weakrefs Callbacks (gen={gen})",
                f"gc.weakrefs(gen={gen})",
                item.ts_handle_weakref_callbacks_stop,
            )
        )

    if has_finalize_garbage(item) and item.ts_finalize_garbage_stop - item.ts_handle_weakref_callbacks_stop > 0:
        inc_data = {"generation": gen, "iid": iid, "finalized_garbage_count": item.finalized_garbage_count}
        events.append(
            begin_event(
                pid,
                tid,
                f"Finalize Garbage (gen={gen})",
                f"gc.finalize(gen={gen})",
                item.ts_handle_weakref_callbacks_stop,
                inc_data,
            )
        )
        events.append(
            end_event(
                pid,
                tid,
                f"Finalize Garbage (gen={gen})",
                f"gc.finalize(gen={gen})",
                item.ts_finalize_garbage_stop,
            )
        )

    if has_handle_resurrected(item) and item.ts_handle_resurrected_stop - item.ts_finalize_garbage_stop > 0:
        inc_data = {"generation": gen, "iid": iid}
        events.append(
            begin_event(
                pid,
                tid,
                f"Handle Resurrected (gen={gen})",
                f"gc.resurrect(gen={gen})",
                item.ts_finalize_garbage_stop,
                inc_data,
            )
        )
        events.append(
            end_event(
                pid,
                tid,
                f"Handle Resurrected (gen={gen})",
                f"gc.resurrect(gen={gen})",
                item.ts_handle_resurrected_stop,
            )
        )

    if has_clear_weakrefs(item) and item.ts_clear_weakrefs_stop - item.ts_handle_resurrected_stop > 0:
        inc_data = {"generation": gen, "iid": iid, "clear_weakrefs_count": item.clear_weakrefs_count}
        events.append(
            begin_event(
                pid,
                tid,
                f"Clear Weakrefs (gen={gen})",
                f"gc.clear_weakrefs(gen={gen})",
                item.ts_handle_resurrected_stop,
                inc_data,
            )
        )
        events.append(
            end_event(
                pid,
                tid,
                f"Clear Weakrefs (gen={gen})",
                f"gc.clear_weakrefs(gen={gen})",
                item.ts_clear_weakrefs_stop,
            )
        )

    if has_delete_garbage(item) and item.ts_delete_garbage_stop - item.ts_delete_garbage_start > 0:
        inc_data = {"generation": gen, "iid": iid, "deleted_garbage_count": item.deleted_garbage_count}
        events.append(
            begin_event(
                pid,
                tid,
                f"Delete Garbage (gen={gen})",
                f"gc.delete(gen={gen})",
                item.ts_delete_garbage_start,
                inc_data,
            )
        )
        events.append(
            end_event(
                pid,
                tid,
                f"Delete Garbage (gen={gen})",
                f"gc.delete(gen={gen})",
                item.ts_delete_garbage_stop,
            )
        )

    events.append(
        end_event(
            pid,
            tid,
            f"GC Pause (gen={gen})",
            f"gc.pause(gen={gen})",
            ts_stop_ns,
        )
    )

    events.append(
        counter_event(
            pid,
            tid,
            f"G{gen}",
            ts_start_ns,
            counter_data,
        )
    )

    events.append(
        counter_event(
            pid,
            tid,
            "heap_size",
            ts_start_ns,
            {"heap_size": item.heap_size},
        )
    )

    return events


def convert_to_trace_format(items: Mapping[int, Sequence[TItem]]) -> list[TraceEvent]:
    events: list[TraceEvent] = []
    for pid, pid_items in items.items():
        events.append(process_meta(pid, f"{pid}"))
        threads: set[int] = set()
        pid_events: list[TraceEvent] = []
        for item in pid_items:
            if is_instant(item):
                pid_events.append(instant_event(pid, item.name, item.ts))
            elif is_gc_stats(item):
                threads.add(item.iid)
                pid_events.extend(convert_item_to_trace_format(pid, item))

        events.extend(thread_meta(pid, tid, f"{pid}:{tid}") for tid in threads)
        events.extend(pid_events)

    return events
