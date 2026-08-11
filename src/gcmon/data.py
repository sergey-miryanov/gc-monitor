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


class LossMsg(msgspec.Struct):
    """An interval in which the target's ring overwrote one generation's
    records unread.

    One record per generation. A poll that lost records in all three writes
    three of these, sharing a left edge and nesting on the interpreter's loss
    row, so each generation keeps its own width and its own counts. Carries
    neither ``collections`` nor ``type``, so ``is_gc_stats`` and ``is_instant``
    both reject it; ``lost_count`` is the field that claims it.

    ``lost_from`` names the first collection the window is missing, so the
    record says *which* collections went unread and not only how many. The far
    end is :func:`lost_to`, derived rather than stored: two stored ends could
    disagree with the count, and the count is what ``--stats`` sums. Zero means
    a producer that predates the field, the one value no counter takes — this
    is the record JSONL and ``gcmon combine`` carry, so it has to decode from
    older captures.
    """

    iid: int
    gen: int
    ts_start: int
    ts_stop: int
    lost_count: int = 0
    lost_pause_ns: int = 0
    lost_from: int = 0


def from_mapping(data: TMapping) -> GCStatsInfo | InstantMsg | LossMsg:
    if data.get("type") == "i":
        return msgspec.convert(data, InstantMsg)
    if "lost_count" in data:
        return msgspec.convert(data, LossMsg)
    return msgspec.convert(data, GCStatsInfo)


def instant_msg(name: str, ts: int) -> InstantMsg:
    return InstantMsg("i", name, ts)


def ts_to_us(ts_ns: int) -> int:
    """Convert timestamp from nanoseconds to microseconds"""
    return int(ts_ns / 1_000)


def dur_to_ms(dur_ns: float) -> float:
    """Convert duration from nanoseconds to milliseconds"""
    return dur_ns / 1_000_000


def secs_to_ns(dur_s: float) -> int:
    """Convert duration from seconds to nanoseconds"""
    return round(dur_s * 1_000_000_000)


def missing_collections(lost_from: int, lost_count: int) -> str:
    """The collections a window is missing, named the way a reader checks them.

    ``"11"`` for one, ``"2..383"`` for a run, both ends included either way.

    One string rather than a pair of numbers. A window that lost a single
    collection carries the same counter at both ends, and a slice reading
    ``11..11`` reads as a range of nothing unless you already know the ends
    are inclusive. What the reader wants from
    this field is which collections to look for on the row above, and that is
    what it says; ``lost_count`` is the number, and it is stored apart.
    """
    to = lost_to(lost_from, lost_count)
    return str(lost_from) if to == lost_from else f"{lost_from}..{to}"


def lost_to(lost_from: int, lost_count: int) -> int:
    """The last collection a loss window is missing, counting both ends.

    Derived and never stored, so the range cannot drift from the count it was
    cut to: ``lost_from`` through here inclusive is exactly ``lost_count``
    counters. That identity is what lets every collection on a ring be charged
    to one drawn ``GC Pause`` slice or to one loss span and to nothing else.
    """
    return lost_from + lost_count - 1
