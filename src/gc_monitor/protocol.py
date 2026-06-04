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


class TIncrementalInfo(Protocol):
    increment_size: int
    ts_fill_increment_start: int
    ts_fill_increment_stop: int


class TMarkAliveInfo(Protocol):
    alive_size: int
    ts_mark_alive_start: int
    ts_mark_alive_stop: int


class TExtraTimes(Protocol):
    ts_deduce_unreachable_start: int
    ts_deduce_unreachable_stop: int

class TInstantMsg(Protocol):
    type: str
    name: str
    ts: int


def has_pause_ts(item: object) -> TypeGuard[TGCStatsInfo]:
    return getattr(item, "ts_start", None) is not None

def has_incremental(item: object) -> TypeGuard[TIncrementalInfo]:
    return getattr(item, "increment_size", None) is not None

def has_mark_alive(item: object) -> TypeGuard[TMarkAliveInfo]:
    return getattr(item, "alive_size", None) is not None

def has_deduce_unreachable(item: object) -> TypeGuard[TExtraTimes]:
    return getattr(item, "ts_deduce_unreachable_start", None) is not None

def has_gen(item: object) -> TypeGuard[TGCStatsInfo]:
    return hasattr(item, "gen")

def is_gc_stats(item: TGCStatsInfo | TIncrementalGCStatsInfo | TInstantMsg) -> TypeGuard[TGCStatsInfo | TIncrementalGCStatsInfo]:
    return hasattr(item, "gen")

def is_incremental(item: TGCStatsInfo | TIncrementalGCStatsInfo) -> TypeGuard[TIncrementalGCStatsInfo]:
    return getattr(item, "increment_size", None) is not None


def is_instant(item: TGCStatsInfo | TIncrementalGCStatsInfo | TInstantMsg) -> TypeGuard[TInstantMsg]:
    return hasattr(item, "type")


def to_mapping(item: TGCStatsInfo | TInstantMsg) -> Mapping[str, str | int | float]:
    if is_instant(item):
        return {
            "type": item.type,
            "name": item.name,
            "ts": item.ts,
        }

    if has_gen(item):
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

        if has_incremental(item):
            m["increment_size"] = item.increment_size
            m["ts_fill_increment_start"] = item.ts_fill_increment_start
            m["ts_fill_increment_stop"] = item.ts_fill_increment_stop

        if has_mark_alive(item):
            m["alive_size"] = item.alive_size
            m["ts_mark_alive_start"] = item.ts_mark_alive_start
            m["ts_mark_alive_stop"] = item.ts_mark_alive_stop

        if has_deduce_unreachable(item):
            m["ts_deduce_unreachable_start"] = item.ts_deduce_unreachable_start
            m["ts_deduce_unreachable_stop"] = item.ts_deduce_unreachable_stop

        return m

    raise NotImplementedError(f"Unknown item type: {type(item)}")
