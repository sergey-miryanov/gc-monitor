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


class GenLoss(msgspec.Struct):
    """What one generation did over one poll interval, seen and unseen.

    ``observed_count`` is how many of its records the poll handed back, and is
    the only field set on a generation that collected without losing anything.
    ``lost_from`` is the first collection gcmon missed and ``lost_count`` how
    many; the far end is :func:`lost_to`, derived from the two so a stored pair
    cannot drift from the count ``--stats`` sums.
    """

    gen: int
    observed_count: int
    lost_count: int = 0
    lost_pause_ns: int = 0
    lost_from: int = 0


class LossMsg(msgspec.Struct):
    """One poll interval on one interpreter, in which collections ran that
    gcmon never read.

    ``ts_start`` and ``ts_stop`` are two consecutive polls, and every
    collection the record names ran between them. ``gens`` holds one
    :class:`GenLoss` per generation that collected or lost anything in the
    interval; see ADR-0015 for why the counts ride there rather than on a span
    each.

    Carries neither ``collections`` nor ``type``, so ``is_gc_stats`` and
    ``is_instant`` both reject it; ``gens`` is the field that claims it.
    """

    iid: int
    ts_start: int
    ts_stop: int
    gens: list[GenLoss]


def from_mapping(data: TMapping) -> GCStatsInfo | InstantMsg | LossMsg:
    if data.get("type") == "i":
        return msgspec.convert(data, InstantMsg)
    if "gens" in data:
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


_DURATION_UNITS: tuple[tuple[int, str], ...] = (
    (3_600_000_000_000, "h"),
    (60_000_000_000, "m"),
    (1_000_000_000, "s"),
    (1_000_000, "ms"),
    (1_000, "µs"),
    (1, "ns"),
)


def duration_text(ns: int) -> str:
    """*ns* broken into units, the way the Perfetto UI writes a duration.

    ``3316458100`` comes out as ``3s 316ms 458µs 100ns``. Units contributing
    nothing are left out, so ``5000000`` is ``5ms`` and zero is ``0ns``.
    """
    if ns == 0:
        return "0ns"

    sign = "-" if ns < 0 else ""
    rest = abs(ns)
    parts: list[str] = []
    for size, unit in _DURATION_UNITS:
        value, rest = divmod(rest, size)
        if value:
            parts.append(f"{value}{unit}")

    return sign + " ".join(parts)


def seen_text(observed_count: int, lost_count: int) -> str:
    """The share of an interval's collections gcmon actually read.

    ``87.0% (47 of 54)``, carrying the two counts the percentage came from.
    One poll interval wide, unlike the ``--stats`` table's ``Cov``.
    """
    total = observed_count + lost_count
    if total == 0:
        return "100.0% (0 of 0)"
    return f"{100.0 * observed_count / total:.1f}% ({observed_count} of {total})"


def missing_collections(lost_from: int, lost_count: int) -> str:
    """The collections an interval is missing, as one string.

    ``"11"`` for a single collection, ``"2..383"`` for a run, both ends
    included either way. A pair of numbers would meet at the same counter
    whenever one collection went missing, and ``11..11`` reads as a range of
    nothing.
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
