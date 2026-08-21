"""The run report moved to :mod:`gcmon.model.run_report`.

A shim for the deep path, which goes one release from now. Import
``gcmon.model.run_report`` instead.
"""

from gcmon.model import run_report as _moved


def __getattr__(name: str) -> object:
    """Answer with whatever ``gcmon.model.run_report`` holds under *name*."""
    return getattr(_moved, name)
