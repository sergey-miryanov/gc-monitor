"""The trace-event union moved to :mod:`gcmon.model.trace_event`.

A shim for the deep path, which goes one release from now. Import
``gcmon.model.trace_event`` instead.
"""

from gcmon.model import trace_event as _moved


def __getattr__(name: str) -> object:
    """Answer with whatever ``gcmon.model.trace_event`` holds under *name*."""
    return getattr(_moved, name)
