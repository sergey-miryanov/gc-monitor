"""The monitor moved to :mod:`gcmon.monitoring.monitor`.

A shim for the deep path, which goes one release from now. Import
``gcmon.monitoring.monitor`` instead.
"""

from gcmon.monitoring import monitor as _moved


def __getattr__(name: str) -> object:
    """Answer with whatever ``gcmon.monitoring.monitor`` holds under *name*."""
    return getattr(_moved, name)
