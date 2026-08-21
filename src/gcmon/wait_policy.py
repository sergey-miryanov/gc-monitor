"""The wait policy moved to :mod:`gcmon.monitoring.wait_policy`.

A shim for the deep path, which goes one release from now. Import
``gcmon.monitoring.wait_policy`` instead.
"""

import warnings

from gcmon.monitoring import wait_policy as _moved

__all__ = list(getattr(_moved, "__all__", None) or [name for name in dir(_moved) if not name.startswith("_")])
"""A star import consults this and never ``__getattr__``, so without it the
old path would import cleanly and bind nothing."""


def __dir__() -> list[str]:
    """What the module used to hold, for `help` and for completion."""
    return dir(_moved)


def __getattr__(name: str) -> object:
    """Answer with whatever ``gcmon.monitoring.wait_policy`` holds under *name*."""
    warnings.warn(
        "gcmon.wait_policy moved to gcmon.monitoring.wait_policy; this path goes one release from now",
        DeprecationWarning,
        stacklevel=2,
    )
    return getattr(_moved, name)
