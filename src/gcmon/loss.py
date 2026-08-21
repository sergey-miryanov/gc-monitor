"""The loss accumulator moved to :mod:`gcmon.model.loss`.

A shim for the deep path, which goes one release from now. Import
``gcmon.model.loss`` instead.
"""

import warnings

from gcmon.model import loss as _moved

__all__ = list(getattr(_moved, "__all__", None) or [name for name in dir(_moved) if not name.startswith("_")])
"""A star import consults this and never ``__getattr__``, so without it the
old path would import cleanly and bind nothing."""


def __dir__() -> list[str]:
    """What the module used to hold, for `help` and for completion."""
    return dir(_moved)


def __getattr__(name: str) -> object:
    """Answer with whatever ``gcmon.model.loss`` holds under *name*."""
    warnings.warn(
        "gcmon.loss moved to gcmon.model.loss; this path goes one release from now",
        DeprecationWarning,
        stacklevel=2,
    )
    return getattr(_moved, name)
