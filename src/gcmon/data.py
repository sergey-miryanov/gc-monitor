import msgspec

from gcmon.protocol import TMapping


class GCStatsInfo(msgspec.Struct):
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
    increment_size: int | None = None
    alive_size: int | None = None
    ts_mark_alive_start: int | None = None
    ts_mark_alive_stop: int | None = None
    ts_fill_increment_start: int | None = None
    ts_fill_increment_stop: int | None = None
    ts_deduce_unreachable_start: int | None = None
    ts_deduce_unreachable_stop: int | None = None
    ts_handle_weakref_callbacks_start: int | None = None
    ts_handle_weakref_callbacks_stop: int | None = None
    ts_finalize_garbage_stop: int | None = None
    finalized_garbage_count: int | None = None
    ts_handle_resurrected_stop: int | None = None
    ts_clear_weakrefs_stop: int | None = None
    clear_weakrefs_count: int | None = None
    ts_delete_garbage_start: int | None = None
    ts_delete_garbage_stop: int | None = None
    deleted_garbage_count: int | None = None


class InstantMsg(msgspec.Struct):
    type: str
    name: str
    ts: int


def from_mapping(data: TMapping) -> GCStatsInfo | InstantMsg:
    if data.get("type") == "i":
        return msgspec.convert(data, InstantMsg)
    return msgspec.convert(data, GCStatsInfo)


def instant_msg(name: str, ts: int) -> InstantMsg:
    return InstantMsg("i", name, ts)


def ts_to_us(ts_ns: int) -> int:
    """Convert timestamp from nanoseconds to microseconds"""
    return int(ts_ns / 1_000)


def dur_to_us(ts_start_ns: int, ts_stop_ns: int) -> int:
    """Convert duration from nanoseconds to microseconds"""
    return int((ts_stop_ns - ts_start_ns) / 1_000)
