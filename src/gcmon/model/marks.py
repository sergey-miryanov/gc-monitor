"""The grammar of a mark: what a workload writes into a trace to say where it was.

The formatter and the parser sit together because a mark is a string on the
wire, and a writer that drifts from a reader produces marks nothing selects.
"""

import re
from typing import Final, Literal, NamedTuple

PREFIX: Final = "gcmon"
"""Reserved, so ``name LIKE 'gcmon:%'`` selects marks and nothing else."""

SEPARATOR: Final = ":"

TSide = Literal["begin", "end"]

BEGIN: Final[TSide] = "begin"
END: Final[TSide] = "end"

_SIDES: Final[dict[str, TSide]] = {BEGIN: BEGIN, END: END}
_FIELDS: Final = 4
_NOT_IN_A_NAME: Final = re.compile(r"[^a-zA-Z0-9_-]")


class Mark(NamedTuple):
    """One end of one region of one benchmark."""

    bench: str
    region: int
    side: TSide


def sanitize(bench: str) -> str:
    """*bench* with everything a field cannot hold replaced.

    The separator goes with it, which is what keeps the grammar unambiguous
    whatever a workload calls itself.
    """
    return _NOT_IN_A_NAME.sub("_", bench)


def format_mark(bench: str, region: int, side: TSide) -> str:
    """The name of one mark, sanitized."""
    return SEPARATOR.join((PREFIX, sanitize(bench), str(region), side))


def parse_mark(name: str) -> Mark | None:
    """What *name* says, or ``None`` where it is not a mark.

    Instants from elsewhere share the trace, so this answers rather than
    raises.
    """
    parts = name.split(SEPARATOR)
    if len(parts) != _FIELDS:
        return None

    prefix, bench, region, raw_side = parts
    side = _SIDES.get(raw_side)
    if side is None or prefix != PREFIX or not bench:
        return None
    if not (region.isascii() and region.isdigit()):
        return None

    return Mark(bench, int(region), side)
