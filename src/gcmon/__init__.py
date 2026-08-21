from .cli._version import installed_version
from .exporters import EventsExporter, JsonlExporter, StdoutExporter, TraceExporter
from .monitoring.child_process_runner import ChildProcess, ChildProcessRunner
from .monitoring.monitor import EventsMonitor


def __getattr__(name: str) -> str:
    """Resolve ``__version__`` on first use, so importing the package does not read it."""
    if name == "__version__":
        return installed_version()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "ChildProcess",
    "ChildProcessRunner",
    "EventsExporter",
    "EventsMonitor",
    "JsonlExporter",
    "StdoutExporter",
    "TraceExporter",
    "__version__",
]
