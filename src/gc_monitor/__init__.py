"""gc_monitor package init."""
__version__ = "0.1.0"
from .core import connect, GCMonitor
from .chrome_trace_exporter import TraceExporter

__all__ = ["connect", "GCMonitor", "TraceExporter", "__version__"]
