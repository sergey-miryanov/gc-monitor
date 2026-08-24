"""Pyperf hook that marks where each benchmark ran.

The hook writes a begin and an end mark per measured region into the trace a
monitor is already keeping, and does nothing else: it spawns no process,
writes no file and computes no statistics. ``gcmon run`` over the whole suite
is what it annotates.
"""

import logging
import os
import time
from functools import partial
from typing import Any

from ..control.control_client import ControlClient, connect_with_retry
from ..control.control_server import CONTROL_ADDRESS_ENV
from ..model.marks import Side, format_mark

ENV_PYPERF_HOOK_VERBOSE = "GCMON_PYPERF_HOOK_VERBOSE"
ENV_PYPERF_HOOK_CONTROL_TIMEOUT = "GCMON_PYPERF_HOOK_CONTROL_TIMEOUT"

logger = logging.getLogger("gcmon")

_regions = 0
"""Regions counted per process, not per hook.

A worker builds one hook for its warmups and another for its values, and both
are handed the same benchmark name, so an instance-scoped counter would emit
one mark name twice meaning two different things.
"""


def _next_region() -> int:
    """The next region number in this process."""
    global _regions
    _regions += 1
    return _regions


NO_MONITOR = (
    f"gcmon: no monitor is listening on {CONTROL_ADDRESS_ENV}. Start one over "
    f"the whole run: `gcmon run -o suite.pftrace -s my_benchmark.py "
    f"--hook=gcmon --inherit-environ={CONTROL_ADDRESS_ENV}`, or -m for a "
    "module. pyperf carries the address through to its workers from there."
)


def _hook_error() -> type[Exception]:
    """The exception pyperf's loader catches to print one line and exit 1.

    ``pyperf.__all__`` does not carry ``HookError``, so this reaches into a
    private module and settles for failing the run some other way if that
    module ever moves. Importing it here rather than at module scope also
    keeps pyperf off the path a working hook takes.
    """
    try:
        from pyperf._hooks import HookError
    except Exception:
        return RuntimeError
    refusal: type[Exception] = HookError
    return refusal


def _get_env_pyperf_hook_verbose() -> bool:
    value = os.environ.get(ENV_PYPERF_HOOK_VERBOSE, "").lower()
    return value in ("1", "yes", "on", "true")


def _get_env_pyperf_hook_control_timeout() -> float:
    value = os.environ.get(ENV_PYPERF_HOOK_CONTROL_TIMEOUT, "")
    if value:
        try:
            return float(value)
        except ValueError:
            pass
    return 10.0


class GCMonitorHook:
    """Pyperf hook that marks the benchmark in a running monitor's trace.

    The monitor is the operator's, started over the whole suite, and the hook
    reaches it through the control address that monitor set in the
    environment.

    Building one connects, and refuses the run where nothing answers.

    Usage:
        # Entry point registration in pyproject.toml
        [project.entry-points."pyperf.hook"]
        gcmon = "gcmon.pyperf.hook:gcmon_hook"

        # Then use in CLI
        gcmon run -o suite.pftrace -m pyperf run --hook=gcmon ...
    """

    def __init__(self) -> None:
        self._marked: list[tuple[int, int, int]] = []
        self._running: tuple[int, int] | None = None
        self._control_client = ControlClient(
            connection_factory=partial(
                connect_with_retry,
                timeout=_get_env_pyperf_hook_control_timeout(),
            ),
        )
        # Eagerly, before a benchmark is running and outside anything pyperf
        # times. Refusing here costs a second; the alternative costs a suite,
        # because a client with nowhere to send makes every send a no-op.
        if self._control_client._ensure_connected() is None:
            raise _hook_error()(NO_MONITOR)

    def __enter__(self) -> GCMonitorHook:
        """Open a region, immediately before the benchmark runs."""
        self._running = (_next_region(), time.monotonic_ns())
        return self

    def __exit__(self, *args: object) -> None:
        """Close it, immediately after.

        The pair is held rather than sent: the benchmark name arrives at
        teardown, and nothing crosses a process boundary until then.
        """
        if self._running is not None:
            region, began = self._running
            self._running = None
            self._marked.append((region, began, time.monotonic_ns()))

    def _send_marks(self, bench_name: str) -> None:
        """Land every region that finished, now that they can be named."""
        for region, began, ended in self._marked:
            self._control_client.instant_msg(format_mark(bench_name, region, Side.BEGIN), ts=began)
            self._control_client.instant_msg(format_mark(bench_name, region, Side.END), ts=ended)
        self._marked.clear()

    def teardown(self, metadata: dict[str, Any]) -> None:
        """Land the marks.

        Pyperf calls this once the hook is done with a process, and it is the
        first point at which ``metadata['name']`` names the benchmark. The
        dict is read and not written: a benchmark's own numbers are pyperf's,
        and gcmon's are in the trace.
        """
        self._send_marks(metadata.get("name", ""))
        self._control_client.close()


def gcmon_hook() -> GCMonitorHook:
    """The entry point, called by pyperf with no arguments."""
    _setup_logging()
    return GCMonitorHook()


def _setup_logging() -> None:
    """Configure the `gcmon` logger for the pyperf hook entry point.

    Attaches a stderr handler if the logger has none and takes its level
    from ``GCMON_PYPERF_HOOK_VERBOSE``. Only the entry point calls this, so
    a test that builds a ``GCMonitorHook`` leaves global logging alone.
    """
    level = logging.DEBUG if _get_env_pyperf_hook_verbose() else logging.WARNING
    logger.setLevel(level)

    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setLevel(level)
        formatter = logging.Formatter("[%(name)s] %(levelname)s: %(message)s")
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    else:
        for handler in logger.handlers:  # type: ignore[assignment]
            handler.setLevel(level)
