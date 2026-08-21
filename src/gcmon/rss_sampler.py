"""The RSS sampler moved to :mod:`gcmon.monitoring.rss_sampler`.

A shim for the deep path, which goes one release from now. Import
``gcmon.monitoring.rss_sampler`` instead.
"""

from gcmon.monitoring import rss_sampler as _moved


def __getattr__(name: str) -> object:
    """Answer with whatever ``gcmon.monitoring.rss_sampler`` holds under *name*."""
    return getattr(_moved, name)
