"""The process handles moved to :mod:`gcmon.monitoring.target_process`.

A shim for the deep path, which goes one release from now. Import
``gcmon.monitoring.target_process`` instead.
"""

import warnings

from gcmon.monitoring import target_process as _moved

__all__ = list(getattr(_moved, "__all__", None) or [name for name in dir(_moved) if not name.startswith("_")])
"""A star import consults this and never ``__getattr__``, so without it the
old path would import cleanly and bind nothing."""


def __dir__() -> list[str]:
    """What the module used to hold, for `help` and for completion."""
    return dir(_moved)


def __getattr__(name: str) -> object:
    """Answer with whatever ``gcmon.monitoring.target_process`` holds under *name*."""
    warnings.warn(
        "gcmon.target_process moved to gcmon.monitoring.target_process; this path goes one release from now",
        DeprecationWarning,
        stacklevel=2,
    )
    return getattr(_moved, name)
