"""What a run of the monitoring loop adds up to.

The accumulator, the streaming aggregation and the stats table. A sibling of
``exporters`` rather than a stage after it: neither layer imports the other.

``gcmon.stats`` used to be the accumulator module itself, which now sits at
``gcmon.stats.stats``. The forwarding below keeps that path answering for one
release.
"""

import warnings

from . import stats as _moved

__all__ = list(getattr(_moved, "__all__", None) or [name for name in dir(_moved) if not name.startswith("_")])
"""A star import consults this and never ``__getattr__``, so without it
``from gcmon.stats import *`` would bind the submodule and nothing else."""


def __dir__() -> list[str]:
    """What ``gcmon.stats`` used to hold, for `help` and for completion."""
    return dir(_moved)


def __getattr__(name: str) -> object:
    """Answer with whatever ``gcmon.stats.stats`` holds under *name*."""
    warnings.warn(
        "gcmon.stats is a package now; import gcmon.stats.stats, which this path answers for one more release",
        DeprecationWarning,
        stacklevel=2,
    )
    return getattr(_moved, name)
