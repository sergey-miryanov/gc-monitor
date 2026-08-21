"""The stats table moved to :mod:`gcmon.stats.stats_output`.

A shim for the deep path, which goes one release from now. Import
``gcmon.stats.stats_output`` instead.
"""

from gcmon.stats import stats_output as _moved


def __getattr__(name: str) -> object:
    """Answer with whatever ``gcmon.stats.stats_output`` holds under *name*."""
    return getattr(_moved, name)
