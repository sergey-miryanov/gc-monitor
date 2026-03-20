"""gc_monitor package init."""
__version__ = "0.1.0"
from .core import GCMonitor, connect
from .exporter import GCMonitorExporter
from .chrome_trace_exporter import TraceExporter

__all__ = ["connect", "GCMonitor", "GCMonitorExporter", "TraceExporter", "__version__"]
