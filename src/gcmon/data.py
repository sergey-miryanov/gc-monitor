"""The record structs moved to :mod:`gcmon.model.data`.

A shim for the deep path, which goes one release from now. Import
``gcmon.model.data`` instead.
"""

from gcmon.model import data as _moved


def __getattr__(name: str) -> object:
    """Answer with whatever ``gcmon.model.data`` holds under *name*."""
    return getattr(_moved, name)
