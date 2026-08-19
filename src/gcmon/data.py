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

    ``lost_from`` is the counter of the first record gcmon missed, and the
    far end follows from ``lost_count``.
    """

    gen: int
    observed_count: int
    lost_count: int = 0
    lost_pause_ns: int = 0
    lost_from: int = 0

    @property
    def no_loss(self) -> bool:
        return self.lost_count == 0


class LossMsg(msgspec.Struct):
    """One poll interval on one interpreter, holding the GC runs whose records
    gcmon never read.

    ``ts_start`` and ``ts_stop`` are two consecutive polls, and every
    collection the record names ran between them. ``gens`` carries one
    :class:`GenLoss` per generation that collected or lost anything; ADR-0015
    says why the counts ride there rather than on a span each.

    ``gens`` is also what ``is_loss`` looks for. This record carries neither
    ``collections`` nor ``type``, so no other guard claims it.
    """

    iid: int
    ts_start: int
    ts_stop: int
    gens: list[GenLoss]


OVERRUN_SHARE = 0.1
"""How much of a run has to go missing before gcmon calls it an overrun.

Not one skipped position: the loop waits on an event whose timeout the platform
rounds up to its scheduler tick, so a long healthy run is near certain to skip a
few. See ADR-0019.
"""


class RunReport(msgspec.Struct):
    """What one run of the monitoring loop did with its schedule.

    ``ticks_scheduled`` counts the positions the schedule offered, which is
    larger than ``ticks_run`` whenever a tick outlasted its own position and the
    loop skipped to the next one rather than making the missed ones up. See
    ADR-0019.
    """

    ticks_run: int
    ticks_scheduled: int

    @property
    def overran(self) -> bool:
        """True when enough of the run went missing that a smaller rate cannot help."""
        if self.ticks_scheduled <= 0:
            return False
        missed = self.ticks_scheduled - self.ticks_run
        return missed / self.ticks_scheduled > OVERRUN_SHARE


def from_mapping(data: TMapping) -> GCStatsInfo | InstantMsg | LossMsg:
    # A capture is GC records by orders of magnitude and the three shapes are
    # mutually exclusive, so `collections` answers first and the rest of the
    # line never reads the dict again. A record carrying none of the three
    # still falls through to the GC branch, which is where an old-format loss
    # record has to land to be refused.
    if "collections" in data:
        return msgspec.convert(data, GCStatsInfo)
    if "gens" in data:
        return msgspec.convert(data, LossMsg)
    if data.get("type") == "i":
        return msgspec.convert(data, InstantMsg)
    return msgspec.convert(data, GCStatsInfo)


def instant_msg(name: str, ts: int) -> InstantMsg:
    return InstantMsg("i", name, ts)


def ts_to_us(ts_ns: int) -> int:
    return int(ts_ns / 1_000)


def dur_to_ms(dur_ns: float) -> float:
    return dur_ns / 1_000_000


def secs_to_ns(dur_s: float) -> int:
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

    ``3316458100`` comes out as ``3s 316ms 458µs 100ns``. Units that
    contribute nothing drop out, and zero is ``0ns``.
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
    """The share of an interval's records gcmon read.

    ``87.0% (47 of 54)``. The ``--stats`` table's ``Cov`` spans a whole run;
    this one spans a single poll interval.
    """
    total = observed_count + lost_count
    if total == 0:
        return "100.0% (0 of 0)"
    return f"{100.0 * observed_count / total:.1f}% ({observed_count} of {total})"


def lost_collections(lost_from: int, lost_count: int) -> str:
    """The records an interval lost, as one string.

    ``"11"`` for a single record, ``"2..383"`` for a range, both ends
    included either way.
    """
    if lost_count == 1:
        return str(lost_from)
    return f"{lost_from}..{lost_from + lost_count - 1}"
