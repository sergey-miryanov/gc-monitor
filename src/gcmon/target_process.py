"""The process handles moved to :mod:`gcmon.monitoring.target_process`.

A shim for the deep path, which goes one release from now. Import
``gcmon.monitoring.target_process`` instead.
"""

from gcmon.monitoring import target_process as _moved


def __getattr__(name: str) -> object:
    """Answer with whatever ``gcmon.monitoring.target_process`` holds under *name*."""
    return getattr(_moved, name)
