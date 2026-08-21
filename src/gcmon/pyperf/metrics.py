"""The flat metric names a benchmark reads a gcmon run through.

Durations arrive in nanoseconds and leave in the milliseconds pyperf
reports.
"""

from __future__ import annotations

import msgspec

from ..stats.streaming_stats import StreamingStats
from ..support.time_units import dur_to_ms

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
    for gen in StreamingStats.GENS
}


def to_metrics(stats: StreamingStats) -> dict[str, int | float]:
    """Summarize pause metrics, with durations converted to milliseconds.

    Sums and counts are exact: what gcmon saw plus what the target's own
    counters say it missed. ``p99`` stays sampled and reads high, since a
    long run delays the next one and its record survives in the ring more
    often than a short one's. No scale factor corrects a quantile.
    """
    result: dict[str, int | float] = {}
    pauses = stats.pause_totals_by_gen()
    cumulative = stats.cumulative_totals_by_gen()
    pause_stats = stats.metrics["pause"]
    exact_total = 0
    for gen in stats.GENS:
        pause = pauses[gen]
        keys = _GEN_KEYS[gen]
        # Read once each and summed here. Asking the totals for a derived
        # number per key summed the exact count three times a generation.
        sampled_count = pause.sampled_count
        exact_count = sampled_count + pause.lost_count
        exact_total += exact_count
        if sampled_count > 0:
            result[keys.p99] = dur_to_ms(pause_stats[gen].percentile(99))
            result[keys.total] = dur_to_ms(pause.sampled_pause_ns + pause.lost_pause_ns)
            result[keys.count] = exact_count
            # A sampled count above zero is why this needs no zero guard.
            result[keys.coverage] = sampled_count / exact_count
        # The `lifetime` in the key names is the wire vocabulary pyperf
        # publishes and reads as "since start"; the counters behind it are
        # `CumulativeCounters`.
        counters = cumulative.get(gen)
        if counters is not None and counters.collections > 0:
            result[keys.lifetime_count] = counters.collections
            result[keys.lifetime_sum] = dur_to_ms(counters.pause_ns)
    heap_p99 = stats.heap_size_p99()
    if heap_p99 is not None:
        result["heap_size_p99"] = heap_p99
    result["pause_count"] = exact_total
    return result
