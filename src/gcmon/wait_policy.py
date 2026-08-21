"""The wait policy moved to :mod:`gcmon.monitoring.wait_policy`.

A shim for the deep path, which goes one release from now. Import
``gcmon.monitoring.wait_policy`` instead.
"""

from gcmon.monitoring import wait_policy as _moved


def __getattr__(name: str) -> object:
    """Answer with whatever ``gcmon.monitoring.wait_policy`` holds under *name*."""
    return getattr(_moved, name)
