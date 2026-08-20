__all__ = ["MIN_IDLE_NS", "MIN_RATE_NS", "idle_to_next_position", "position_of"]

MIN_IDLE_NS = 1_000_000
"""The least idle gcmon leaves the target between one tick and the next."""

MIN_RATE_NS = MIN_IDLE_NS
"""The smallest rate gcmon accepts."""


def position_of(instant_ns: int, start_ns: int, rate_ns: int) -> int:
    """Which position on the grid `start_ns + k * rate_ns` *instant_ns* falls on."""
    assert rate_ns > 0, "a schedule needs a rate"

    return (instant_ns - start_ns) // rate_ns


def idle_to_next_position(instant_ns: int, start_ns: int, rate_ns: int) -> int:
    """How long to wait for the position after the one *instant_ns* falls on.

    The positions a slow tick ran through are dropped, never made up (ADR-0019).
    """
    next_ns = start_ns + (position_of(instant_ns, start_ns, rate_ns) + 1) * rate_ns

    return max(next_ns - instant_ns, MIN_IDLE_NS)
