"""Shared conversion from GC stats items to TraceEvent objects."""

from collections.abc import Mapping, Sequence

from ..data import duration_text, missing_collections, seen_text
from ..protocol import (
    TGCStatsInfo,
    TGenLoss,
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
    ArgGroup,
    EventArgs,
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

    pause_data: EventArgs = {
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
        inc_data: EventArgs = {"generation": gen, "iid": iid, "alive_size": item.alive_size}
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


def _gen_loss_args(gen: TGenLoss) -> ArgGroup:
    """One generation's group inside a ``GC Loss`` slice's args.

    A generation that lost nothing gets ``observed_count`` and a zero, so the
    groups still add up to the slice's totals. One that lost something also
    gets ``missing_collections``, naming them on that generation's own counter
    with both ends included, and the pause they came to.
    """
    args: ArgGroup = {"observed_count": gen.observed_count}
    if not gen.lost_count:
        args["missing_count"] = 0
        return args

    args["missing_collections"] = missing_collections(gen.lost_from, gen.lost_count)
    args["missing_count"] = gen.lost_count
    args["missing_pause_total"] = duration_text(gen.lost_pause_ns)
    args["missing_pause_total_ns"] = gen.lost_pause_ns
    return args


def convert_loss_to_trace_format(pid: int, item: TLossMsg) -> list[TraceEvent]:
    """One ``GC Loss`` slice covering a poll interval gcmon went into blind.

    Spans the interval end to end, on interpreter *iid*'s loss track rather
    than among its collections. Named ``GC Loss(0,2)`` for the generations that
    lost records, which also gives each combination a colour of its own since
    Perfetto hashes the slice name.

    The args carry the interval's totals and then one group per generation.
    ``missing_pause_total_ns`` sums the lost collections and is not the slice's
    duration: a 29 s bar can carry 3 s of it. It goes out twice, in nanoseconds
    for SQL and as ``missing_pause_total`` for reading.

    See ADR-0015 for the width, the track and the grouping.
    """
    tid = loss_tid(item.iid)
    blind = [gen.gen for gen in item.gens if gen.lost_count]
    name = f"GC Loss({','.join(str(gen) for gen in blind)})" if blind else "GC Loss"
    category = "gc.loss"

    observed_count = sum(gen.observed_count for gen in item.gens)
    missing_count = sum(gen.lost_count for gen in item.gens)
    missing_pause_ns = sum(gen.lost_pause_ns for gen in item.gens)

    args: EventArgs = {
        "iid": item.iid,
        "observed_count": observed_count,
        "missing_count": missing_count,
        "seen": seen_text(observed_count, missing_count),
        "missing_pause_total": duration_text(missing_pause_ns),
        "missing_pause_total_ns": missing_pause_ns,
    }
    for gen in item.gens:
        args[f"gen{gen.gen}"] = _gen_loss_args(gen)

    return [
        begin_event(pid, tid, name, category, item.ts_start, args),
        end_event(pid, tid, name, category, item.ts_stop),
    ]


def _loss_in_time_order(items: Sequence[TItem]) -> Sequence[TItem]:
    """*items* with its loss records in time order, everything else in place.

    Load-bearing rather than tidy. Consecutive intervals touch, so one span's
    END shares a timestamp with the next one's BEGIN, and a trace processor
    sorting by timestamp leaves those two in the order they were emitted; the
    wrong way round they read as nested. `_ingest` emits them in order, but a
    capture read back from JSONL carries that only in its line order.
    """
    spans = [item for item in items if is_loss(item)]
    if len(spans) < 2:
        return items

    at = [index for index, item in enumerate(items) if is_loss(item)]
    ordered = list(items)
    for index, span in zip(at, sorted(spans, key=lambda span: span.ts_start), strict=True):
        ordered[index] = span

    return ordered


def convert_to_trace_format(items: Mapping[int, Sequence[TItem]]) -> list[TraceEvent]:
    events: list[TraceEvent] = []
    for pid, pid_items in items.items():
        events.append(process_meta(pid, f"{pid}"))
        threads: set[int] = set()
        pid_events: list[TraceEvent] = []
        # The guards are mutually exclusive, so the order is free to follow the
        # capture: GC records outnumber the other two by orders of magnitude,
        # and a guard that misses pays for the `AttributeError` behind
        # `hasattr`.
        for item in _loss_in_time_order(pid_items):
            if is_gc_stats(item):
                threads.add(item.iid)
                pid_events.extend(convert_item_to_trace_format(pid, item))
            elif is_loss(item):
                # No `thread_meta`: the loss track is not a thread, and
                # `perfetto_format` describes it off the slices themselves.
                pid_events.extend(convert_loss_to_trace_format(pid, item))
            elif is_instant(item):
                pid_events.append(instant_event(pid, item.name, item.ts))

        events.extend(thread_meta(pid, tid, f"{pid}:{tid}") for tid in threads)
        events.extend(pid_events)

    return events
