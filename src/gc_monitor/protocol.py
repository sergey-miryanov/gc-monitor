from collections.abc import Mapping
from typing import Protocol, TypeGuard


class TGCStatsInfo(Protocol):
    gen: int
    iid: int
    ts_start: int
    ts_stop: int
    heap_size: int
    collections: int
    collected: int
    uncollectable: int
    candidates: int
    duration: float


class TIncrementalGCStatsInfo(TGCStatsInfo, Protocol):
    increment_size: int
    alive_size: int
    ts_mark_alive_start: int
    ts_mark_alive_stop: int
    ts_fill_increment_start: int
    ts_fill_increment_stop: int
    ts_deduce_unreachable_start: int
    ts_deduce_unreachable_stop: int


class TInstantMsg(Protocol):
    type: str
    name: str
    ts: int


def is_gc_stats(item: TGCStatsInfo | TIncrementalGCStatsInfo | TInstantMsg) -> TypeGuard[TGCStatsInfo | TIncrementalGCStatsInfo]:
    return hasattr(item, "gen")

def is_incremental(item: TGCStatsInfo | TIncrementalGCStatsInfo) -> TypeGuard[TIncrementalGCStatsInfo]:
    return hasattr(item, "increment_size")


def is_instant(item: TGCStatsInfo | TIncrementalGCStatsInfo | TInstantMsg) -> TypeGuard[TInstantMsg]:
    return hasattr(item, "type")


def to_mapping(item: TGCStatsInfo | TIncrementalGCStatsInfo | TInstantMsg) -> Mapping[str, str | int | float]:
    if is_instant(item):
        return {
            "type": item.type,
            "name": item.name,
            "ts": item.ts,
        }

    if is_gc_stats(item):
        m: dict[str, str | int | float] = {
            "gen": item.gen,
            "iid": item.iid,
            "ts_start": item.ts_start,
            "ts_stop": item.ts_stop,
            "heap_size": item.heap_size,
            "collections": item.collections,
            "collected": item.collected,
            "uncollectable": item.uncollectable,
            "candidates": item.candidates,
            "duration": item.duration,
        }

        if is_incremental(item):
            m["alive_size"] = item.alive_size
            m["increment_size"] = item.increment_size
            m["ts_mark_alive_start"] = item.ts_mark_alive_start
            m["ts_mark_alive_stop"] = item.ts_mark_alive_stop
            m["ts_fill_increment_start"] = item.ts_fill_increment_start
            m["ts_fill_increment_stop"] = item.ts_fill_increment_stop
            m["ts_deduce_unreachable_start"] = item.ts_deduce_unreachable_start
            m["ts_deduce_unreachable_stop"] = item.ts_deduce_unreachable_stop

        return m

    raise NotImplementedError(f"Unknown item type: {type(item)}")
