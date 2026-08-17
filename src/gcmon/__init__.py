from .child_process_runner import ChildProcess, ChildProcessRunner
from .exporters import EventsExporter, JsonlExporter, StdoutExporter, TraceExporter
from .monitor import EventsMonitor


def __getattr__(name: str) -> str:
    """Read ``__version__`` from the installed distribution's metadata.

    ``pyproject.toml`` is the only place gcmon's version is written. The lookup scans
    ``sys.path`` and costs tens of milliseconds, so it waits for a read of ``__version__``
    instead of running at import: ``gcmon --version`` is the only caller.
    """
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
