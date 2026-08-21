"""The structural protocols moved to :mod:`gcmon.model.protocol`.

A shim for the deep path, which goes one release from now. Import
``gcmon.model.protocol`` instead.
"""

from gcmon.model import protocol as _moved


def __getattr__(name: str) -> object:
    """Answer with whatever ``gcmon.model.protocol`` holds under *name*."""
    return getattr(_moved, name)
