"""The grammar of a mark: what a workload writes into a trace to say where it was.

The formatter and the parser sit together because a mark is a string on the
wire, and a writer that drifts from a reader produces marks nothing selects.
"""

import re
from enum import StrEnum
from typing import Final, NamedTuple

PREFIX: Final = "gcmon"
"""Reserved, so ``name LIKE 'gcmon:%'`` selects marks and nothing else."""

_NAME_CHARS: Final = "a-zA-Z0-9_-"
"""What a benchmark name may hold, in both directions.

The separator is not among them, which is what keeps the grammar unambiguous
whatever a workload calls itself.
"""


class Side(StrEnum):
    """Which end of a region a mark is."""

    BEGIN = "begin"
    END = "end"


class Mark(NamedTuple):
    """One end of one region of one benchmark."""

    bench: str
    region: int
    side: Side


_NOT_A_NAME: Final = re.compile(f"[^{_NAME_CHARS}]")
_MARK: Final = re.compile(f"{PREFIX}:([{_NAME_CHARS}]+):([0-9]+):({'|'.join(Side)})")


def format_mark(bench: str, region: int, side: Side) -> str:
    """The name of one mark.

    *bench* keeps only what a field can hold, and an empty result becomes
    ``_``: a writer that emits a name its own reader refuses is the failure
    this module exists to prevent.
    """
    return f"{PREFIX}:{_NOT_A_NAME.sub('_', bench) or '_'}:{region}:{side}"


def parse_mark(name: str) -> Mark | None:
    """What *name* says, or ``None`` where it is not a mark.

    Instants from elsewhere share the trace, so this answers rather than
    raises.
    """
    found = _MARK.fullmatch(name)
    if found is None:
        return None

    bench, region, side = found.groups()
    return Mark(bench, int(region), Side(side))
