"""The tick grid moved to :mod:`gcmon.model.schedule`.

A shim for the deep path, which goes one release from now. Import
``gcmon.model.schedule`` instead.
"""

from gcmon.model import schedule as _moved


def __getattr__(name: str) -> object:
    """Answer with whatever ``gcmon.model.schedule`` holds under *name*."""
    return getattr(_moved, name)
