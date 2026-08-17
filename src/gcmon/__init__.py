from .child_process_runner import ChildProcess, ChildProcessRunner
from .exporters import EventsExporter, JsonlExporter, StdoutExporter, TraceExporter
from .monitor import EventsMonitor


def __getattr__(name: str) -> str:
    """Resolve ``__version__`` from the installed distribution, on first read.

    ``pyproject.toml`` is the only place gcmon's version is written; this reads it back out of
    the metadata the install carries. On demand, not at import: the lookup scans ``sys.path``
    and costs tens of milliseconds, which every ``import gcmon`` would otherwise pay for a
    string only ``gcmon --version`` reads.
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
