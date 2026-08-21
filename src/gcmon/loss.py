"""The loss accumulator moved to :mod:`gcmon.model.loss`.

A shim for the deep path, which goes one release from now. Import
``gcmon.model.loss`` instead.
"""

from gcmon.model import loss as _moved


def __getattr__(name: str) -> object:
    """Answer with whatever ``gcmon.model.loss`` holds under *name*."""
    return getattr(_moved, name)
