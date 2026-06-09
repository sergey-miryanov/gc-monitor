"""Pyperf integration for GC monitoring.

This package provides pyperf hooks for collecting GC statistics during benchmarks.
"""

from .hook import GCMonitorHook, gcmon_hook

__all__ = [
    "GCMonitorHook",
    "gcmon_hook",
]
