"""The events reader moved to :mod:`gcmon.monitoring.events_reader`.

A shim for the deep path, which goes one release from now. Import
``gcmon.monitoring.events_reader`` instead.
"""

from gcmon.monitoring import events_reader as _moved


def __getattr__(name: str) -> object:
    """Answer with whatever ``gcmon.monitoring.events_reader`` holds under *name*."""
    return getattr(_moved, name)
