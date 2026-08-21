"""The stats table moved to :mod:`gcmon.stats.stats_output`.

A shim for the deep path, which goes one release from now. Import
``gcmon.stats.stats_output`` instead.
"""

import warnings

from gcmon.stats import stats_output as _moved

__all__ = list(getattr(_moved, "__all__", None) or [name for name in dir(_moved) if not name.startswith("_")])
"""A star import consults this and never ``__getattr__``, so without it the
old path would import cleanly and bind nothing."""


def __dir__() -> list[str]:
    """What the module used to hold, for `help` and for completion."""
    return dir(_moved)


def __getattr__(name: str) -> object:
    """Answer with whatever ``gcmon.stats.stats_output`` holds under *name*."""
    warnings.warn(
        "gcmon.stats_output moved to gcmon.stats.stats_output; this path goes one release from now",
        DeprecationWarning,
        stacklevel=2,
    )
    return getattr(_moved, name)
