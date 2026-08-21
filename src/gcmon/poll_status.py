"""The poll status moved to :mod:`gcmon.model.poll_status`.

A shim for the deep path, which goes one release from now. Import
``gcmon.model.poll_status`` instead.
"""

from gcmon.model import poll_status as _moved


def __getattr__(name: str) -> object:
    """Answer with whatever ``gcmon.model.poll_status`` holds under *name*."""
    return getattr(_moved, name)
