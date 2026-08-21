from .exporters import EventsExporter, JsonlExporter, StdoutExporter, TraceExporter
from .monitoring.child_process_runner import ChildProcess, ChildProcessRunner
from .monitoring.monitor import EventsMonitor


def __getattr__(name: str) -> str:
    """Read ``__version__`` from the installed distribution's metadata, on first use."""
    if name == "__version__":
        import importlib.metadata

        try:
            return importlib.metadata.version("gcmon")
        except importlib.metadata.PackageNotFoundError:
            # A source tree with no install: nothing to read a version from.
            return "0.0.0+unknown"
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
