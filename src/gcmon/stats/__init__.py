"""What a run of the monitoring loop adds up to.

The accumulator, the streaming aggregation and the stats table. A sibling of
``exporters`` rather than a stage after it: neither layer imports the other.

``gcmon.stats`` used to be the accumulator module itself, which now sits at
``gcmon.stats.stats``. The forwarding below keeps that path answering for one
release.
"""

from . import stats as _moved


def __getattr__(name: str) -> object:
    """Answer with whatever ``gcmon.stats.stats`` holds under *name*."""
    return getattr(_moved, name)
