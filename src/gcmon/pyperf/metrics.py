"""The flat metric names a benchmark reads a gcmon run through.

`StreamingStats` keeps the run; this turns it into the names
[`docs/pyperf.md`](../../../docs/pyperf.md) publishes. The two are apart on
purpose: what a benchmark author calls a number is a contract of its own, and
the module that folds GC records should not have to spell it.

Durations arrive in nanoseconds and leave in milliseconds, which is what
pyperf reports in.
"""

from __future__ import annotations

import msgspec

from ..data import dur_to_ms
from ..stats import StreamingStats

__all__ = ["to_metrics"]


class _GenKeys(msgspec.Struct, frozen=True):
    """The result keys one generation writes."""

    p99: str
    total: str
    count: str
    coverage: str
    lifetime_count: str
    lifetime_sum: str


# A generation's keys never change, so they are formatted once here rather
# than on every call: a projection writes up to six per generation and does
# little else besides three quantiles.
_GEN_KEYS: dict[int, _GenKeys] = {
    gen: _GenKeys(
        f"pause_gen_{gen}_p99",
        f"pause_gen_{gen}_sum",
        f"pause_gen_{gen}_count",
        f"pause_gen_{gen}_coverage",
        f"pause_gen_{gen}_lifetime_count",
        f"pause_gen_{gen}_lifetime_sum",
    )
    for gen in (0, 1, 2)
}


def to_metrics(stats: StreamingStats) -> dict[str, int | float]:
    """Summarize pause metrics, with durations converted to milliseconds.

    Sums and counts are exact: what gcmon saw plus what the target's own
    counters say it missed. ``p99`` stays sampled and reads high, since a
    long run delays the next one, so its record survives in the ring more
    often than a short one's. No scale factor corrects a quantile.

    Both totals fold per generation once, up front. Reading them a field at a
    time would rescan each dict per field and dominate a call that is
    otherwise three quantiles. A generation that recorded nothing is left out
    rather than published as a zero.
    """
    result: dict[str, int | float] = {}
    pauses = stats.pause_totals_by_gen()
    lifetimes = stats.lifetime_totals_by_gen()
    exact_total = 0
    for gen in stats.GENS:
        pause = pauses[gen]
        keys = _GEN_KEYS[gen]
        exact_total += pause.exact_count
        if pause.sampled_count > 0:
            result[keys.p99] = dur_to_ms(stats.metrics["pause"][gen].percentile(99))
            result[keys.total] = dur_to_ms(pause.exact_pause_ns)
            result[keys.count] = pause.exact_count
            result[keys.coverage] = pause.coverage
        lifetime = lifetimes.get(gen)
        if lifetime is not None and lifetime.collections > 0:
            result[keys.lifetime_count] = lifetime.collections
            result[keys.lifetime_sum] = dur_to_ms(lifetime.pause_ns)
    heap_p99 = stats.heap_size_p99()
    if heap_p99 is not None:
        result["heap_size_p99"] = heap_p99
    result["pause_count"] = exact_total
    return result
