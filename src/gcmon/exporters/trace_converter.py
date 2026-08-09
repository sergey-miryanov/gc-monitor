"""Shared conversion from GC stats items to TraceEvent objects."""

from collections.abc import Mapping, Sequence

from ..data import lost_to
from ..protocol import (
    TGCStatsInfo,
    TItem,
    TLossMsg,
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
    is_loss,
)
from ..trace_event import (
    TraceEvent,
    begin_event,
    counter_event,
    end_event,
    instant_event,
    loss_tid,
    process_meta,
    thread_meta,
)

__all__ = [
    "convert_item_to_trace_format",
    "convert_loss_to_trace_format",
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
            f"GC Pause({gen})",
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
                f"Mark Alive({gen})",
                f"gc.mark.alive(gen={gen})",
                item.ts_mark_alive_start,
                inc_data,
            )
        )
        events.append(
            end_event(
                pid,
                tid,
                f"Mark Alive({gen})",
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
                f"Fill increment({gen})",
                f"gc.increment(gen={gen})",
                item.ts_fill_increment_start,
                inc_data,
            )
        )
        events.append(
            end_event(
                pid,
                tid,
                f"Fill increment({gen})",
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
                f"Deduce Unreachable({gen})",
                f"gc.deduce(gen={gen})",
                item.ts_deduce_unreachable_start,
                inc_data,
            )
        )
        events.append(
            end_event(
                pid,
                tid,
                f"Deduce Unreachable({gen})",
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
                f"Handle Weakrefs Callbacks({gen})",
                f"gc.weakrefs(gen={gen})",
                item.ts_handle_weakref_callbacks_start,
                inc_data,
            )
        )
        events.append(
            end_event(
                pid,
                tid,
                f"Handle Weakrefs Callbacks({gen})",
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
                f"Finalize Garbage({gen})",
                f"gc.finalize(gen={gen})",
                item.ts_handle_weakref_callbacks_stop,
                inc_data,
            )
        )
        events.append(
            end_event(
                pid,
                tid,
                f"Finalize Garbage({gen})",
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
                f"Handle Resurrected({gen})",
                f"gc.resurrect(gen={gen})",
                item.ts_finalize_garbage_stop,
                inc_data,
            )
        )
        events.append(
            end_event(
                pid,
                tid,
                f"Handle Resurrected({gen})",
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
                f"Clear Weakrefs({gen})",
                f"gc.clear_weakrefs(gen={gen})",
                item.ts_handle_resurrected_stop,
                inc_data,
            )
        )
        events.append(
            end_event(
                pid,
                tid,
                f"Clear Weakrefs({gen})",
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
                f"Delete Garbage({gen})",
                f"gc.delete(gen={gen})",
                item.ts_delete_garbage_start,
                inc_data,
            )
        )
        events.append(
            end_event(
                pid,
                tid,
                f"Delete Garbage({gen})",
                f"gc.delete(gen={gen})",
                item.ts_delete_garbage_stop,
            )
        )

    events.append(
        end_event(
            pid,
            tid,
            f"GC Pause({gen})",
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


def convert_loss_to_trace_format(pid: int, item: TLossMsg) -> list[TraceEvent]:
    """One ``GC Loss(N)`` slice covering an interval gcmon could not
    observe on one generation's ring.

    Drawn as the whole window, because that is what is known: the records are
    gone and nothing says where inside it they ran. A bar sized to the lost
    pause would be narrower than the uncertainty and would put all of it at
    the window's left edge, which is a claim the ring cannot support. The
    pause sum rides in the args instead, where it reads as a magnitude rather
    than as a position.

    Named for its generation the way ``GC Pause({gen})`` is, so each one
    keeps a stable colour of its own — Perfetto hashes the slice name. A poll
    blind in all three generations draws three of these, nested inside one
    another on the interpreter's loss row, and the row says which generation
    went blind and for how long rather than only that gcmon went blind.

    On interpreter *iid*'s loss track, not among its collections. A window can
    span an observed collection of another generation, so this would cross the
    slices on a thread track; a row of its own also keeps what is
    reconstructed apart from what was measured.

    ``collections_from`` and ``collections_to`` name the missing collections on
    that generation's counter, both ends included. Where the bar's width is
    uncertainty, the range is not: it comes from subtracting two of the ring's
    own cumulative counters, so it says exactly which collections the ring
    overwrote and lets a reader check the span against the ``GC Pause`` slices
    on the row above rather than take the count on faith.
    """
    tid = loss_tid(item.iid)
    gen = item.gen
    name = f"GC Loss({gen})"
    category = f"gc.loss(gen={gen})"
    args: dict[str, int] = {
        "iid": item.iid,
        "generation": gen,
        "lost_count": item.lost_count,
        "lost_pause_ns": item.lost_pause_ns,
        "collections_from": item.lost_from,
        "collections_to": lost_to(item.lost_from, item.lost_count),
    }

    return [
        begin_event(pid, tid, name, category, item.ts_start, args),
        end_event(pid, tid, name, category, item.ts_stop),
    ]


def convert_to_trace_format(items: Mapping[int, Sequence[TItem]]) -> list[TraceEvent]:
    events: list[TraceEvent] = []
    for pid, pid_items in items.items():
        events.append(process_meta(pid, f"{pid}"))
        threads: set[int] = set()
        pid_events: list[TraceEvent] = []
        for item in pid_items:
            if is_instant(item):
                pid_events.append(instant_event(pid, item.name, item.ts))
            elif is_loss(item):
                # No `thread_meta`: the loss track is not a thread, and
                # `perfetto_format` describes it off the slices themselves.
                pid_events.extend(convert_loss_to_trace_format(pid, item))
            elif is_gc_stats(item):
                threads.add(item.iid)
                pid_events.extend(convert_item_to_trace_format(pid, item))

        events.extend(thread_meta(pid, tid, f"{pid}:{tid}") for tid in threads)
        events.extend(pid_events)

    return events
