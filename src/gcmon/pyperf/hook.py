"""Pyperf hook that marks where each benchmark ran.

The hook writes a begin and an end mark per measured region into the trace a
monitor is already keeping, and does nothing else: it spawns no process,
writes no file and computes no statistics. ``gcmon run`` over the whole suite
is what it annotates.
"""

import itertools
import logging
import os
import time
from collections.abc import Mapping, Sequence
from functools import partial
from typing import Any

from ..control.control_client import ControlClient, connect_with_retry
from ..model.marks import BEGIN, END, format_mark
from ..model.protocol import TGCStatsInfo, TItem, is_gc_stats, is_loss
from ..stats.streaming_stats import StreamingStats

ENV_PYPERF_HOOK_VERBOSE = "GCMON_PYPERF_HOOK_VERBOSE"
ENV_PYPERF_HOOK_CONTROL_TIMEOUT = "GCMON_PYPERF_HOOK_CONTROL_TIMEOUT"

logger = logging.getLogger("gcmon")

_regions = itertools.count(1)
"""Regions counted per process, not per hook.

A worker builds one hook for its warmups and another for its values, and both
are handed the same benchmark name, so an instance-scoped counter would emit
one mark name twice meaning two different things.
"""


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


def _replay(stats: StreamingStats, parsed: Mapping[int, Sequence[TItem]]) -> None:
    """Rebuild a session's statistics from the records it wrote.

    The monitor folds loss and the cumulative counters as it polls, but the
    hook meets the session only as a file, so both have to come back off it.
    Loss rides in records of its own. The counters ride on every GC record,
    whose ``collections`` and ``duration`` are the target's cumulative totals,
    so the newest record of each ring carries what the monitor observed live.

    Loss is summed per ``(pid, iid, gen)`` before it goes in: one record covers
    one interpreter's poll interval and names every generation active in it, so
    its entries sum rather than its records.

    Order between the two guards does not matter, since no record answers to
    both. Were they ever to overlap, a loss record would fold in here as a
    collection and inflate the very numbers it carries to correct.
    """
    lost: dict[tuple[int, int, int], tuple[int, int]] = {}
    newest: dict[tuple[int, int, int], TGCStatsInfo] = {}

    for pid, items in parsed.items():
        for item in items:
            if is_gc_stats(item):
                stats.update(pid, item)
                ring = (pid, item.iid, item.gen)
                if ring not in newest or item.collections > newest[ring].collections:
                    newest[ring] = item
            elif is_loss(item):
                for entry in item.gens:
                    ring = (pid, item.iid, entry.gen)
                    seen_count, seen_pause = lost.get(ring, (0, 0))
                    lost[ring] = (seen_count + entry.lost_count, seen_pause + entry.lost_pause_ns)

    for (pid, iid, gen), record in newest.items():
        stats.observe_cumulative(pid, iid, gen, record.collections, record.duration)

    for (pid, iid, gen), (count, pause_ns) in lost.items():
        if count or pause_ns:
            stats.record_loss(pid, iid, gen, count, pause_ns)


class GCMonitorHook:
    """Pyperf hook that marks the benchmark in a running monitor's trace.

    The monitor is the operator's, started over the whole suite, and the hook
    reaches it through the control address that monitor set in the
    environment.

    Usage:
        # Entry point registration in pyproject.toml
        [project.entry-points."pyperf.hook"]
        gcmon = "gcmon.pyperf.hook:gcmon_hook"

        # Then use in CLI
        gcmon run -o suite.pftrace -- pyperf run --hook=gcmon ...
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

    def __enter__(self) -> GCMonitorHook:
        """Open a region, immediately before the benchmark runs."""
        self._running = (next(_regions), time.monotonic_ns())
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
            self._control_client.instant_msg(format_mark(bench_name, region, BEGIN), ts=began)
            self._control_client.instant_msg(format_mark(bench_name, region, END), ts=ended)
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
