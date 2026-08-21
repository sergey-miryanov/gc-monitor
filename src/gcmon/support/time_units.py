"""Converting gcmon's nanoseconds into the unit a format or a caller asks for (ADR-0009)."""


def ts_to_us(ts_ns: int) -> int:
    return int(ts_ns / 1_000)


def dur_to_ms(dur_ns: float) -> float:
    return dur_ns / 1_000_000


def secs_to_ns(dur_s: float) -> int:
    return round(dur_s * 1_000_000_000)
