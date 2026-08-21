"""The child process runner moved to :mod:`gcmon.monitoring.child_process_runner`.

A shim for the deep path, which goes one release from now. Import
``gcmon.monitoring.child_process_runner`` instead.
"""

from gcmon.monitoring import child_process_runner as _moved


def __getattr__(name: str) -> object:
    """Answer with whatever ``gcmon.monitoring.child_process_runner`` holds under *name*."""
    return getattr(_moved, name)
