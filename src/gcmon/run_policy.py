"""The run policy moved to :mod:`gcmon.monitoring.run_policy`.

A shim for the deep path, which goes one release from now. Import
``gcmon.monitoring.run_policy`` instead.
"""

from gcmon.monitoring import run_policy as _moved


def __getattr__(name: str) -> object:
    """Answer with whatever ``gcmon.monitoring.run_policy`` holds under *name*."""
    return getattr(_moved, name)
