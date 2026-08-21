"""The monitoring loop moved to :mod:`gcmon.monitoring.monitor_loop`.

A shim for the deep path, which goes one release from now. Import
``gcmon.monitoring.monitor_loop`` instead.
"""

from gcmon.monitoring import monitor_loop as _moved


def __getattr__(name: str) -> object:
    """Answer with whatever ``gcmon.monitoring.monitor_loop`` holds under *name*."""
    return getattr(_moved, name)
