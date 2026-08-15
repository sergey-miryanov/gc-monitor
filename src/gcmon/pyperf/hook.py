"""Pyperf hook that runs gcmon against the benchmark process.

The hook spawns a ``gcmon monitor`` subprocess writing JSONL, and folds
the lines it wrote into pyperf's metadata once the benchmark ends.
"""

import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Mapping, Sequence
from functools import partial
from pathlib import Path
from typing import Any

from ..control.control_client import ControlClient, connect_with_retry
from ..control.control_server import _make_address
from ..exporters.chrome_trace_io import read_jsonl
from ..protocol import TGCStatsInfo, TItem, is_gc_stats, is_loss
from ..stats import StreamingStats
from ..utils.process_terminator import log_process_output, terminate_process
from .metrics import to_metrics

GRACEFUL_TIMEOUT = 5.0
FORCE_TIMEOUT = 2.0

ENV_PYPERF_HOOK_OUTPUT = "GCMON_PYPERF_HOOK_OUTPUT"
ENV_PYPERF_HOOK_TEMP_DIR = "GCMON_PYPERF_HOOK_TEMP_DIR"
ENV_PYPERF_HOOK_VERBOSE = "GCMON_PYPERF_HOOK_VERBOSE"
ENV_PYPERF_HOOK_CONTROL_TIMEOUT = "GCMON_PYPERF_HOOK_CONTROL_TIMEOUT"

logger = logging.getLogger("gcmon")


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


def _get_env_pyperf_hook_temp_dir() -> str | None:
    """Where the temp JSONL goes, or ``None`` for the system default."""
    return os.environ.get(ENV_PYPERF_HOOK_TEMP_DIR) or None


def _get_env_pyperf_hook_output(bench_name: str, pid: int) -> Path:
    """Where the combined JSONL goes.

    ``GCMON_PYPERF_HOOK_OUTPUT`` overrides the default and may carry
    ``{bench_name}`` and ``{pid}`` placeholders.
    """
    env_path = os.environ.get(ENV_PYPERF_HOOK_OUTPUT)
    if env_path:
        env_path = env_path.format(bench_name=bench_name, pid=pid)
        return Path(env_path)
    return Path(f"gcmon_{bench_name}_combined_{pid}.jsonl")


def _replay(stats: StreamingStats, parsed: Mapping[int, Sequence[TItem]]) -> None:
    """Rebuild a session's statistics from the records it wrote.

    The monitor folds loss and lifetime as it polls, but the hook meets the
    session only as a file, so both have to come back off it. Loss rides in
    records of its own. Lifetime rides on every GC record, whose
    ``collections`` and ``duration`` are the target's cumulative totals, so
    the newest record of each ring carries what the monitor recorded live.

    Loss is summed per ``(pid, gen)`` before it goes in: one record covers a
    poll interval and names every generation active in it, so its entries sum
    rather than its records.

    Order between the two guards does not matter, since no record answers to
    both. Were they ever to overlap, a loss record would fold in here as a
    collection and inflate the very numbers it carries to correct.
    """
    lost: dict[tuple[int, int], tuple[int, int]] = {}
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
                    seen_count, seen_pause = lost.get((pid, entry.gen), (0, 0))
                    lost[(pid, entry.gen)] = (seen_count + entry.lost_count, seen_pause + entry.lost_pause_ns)

    for (pid, iid, gen), record in newest.items():
        stats.record_lifetime(pid, iid, gen, record.collections, record.duration)

    for (pid, gen), (count, pause_ns) in lost.items():
        if count or pause_ns:
            stats.record_loss(pid, gen, count, pause_ns)


class GCMonitorHook:
    """Pyperf hook for GC monitoring via external gcmon process.

    The hook spawns a `gcmon` CLI process that reads the benchmark
    process's memory directly. That process writes one JSONL file per run
    under a temp directory; the hook concatenates them and puts the
    statistics it reads back into pyperf's metadata.

    Usage:
        # Entry point registration in pyproject.toml
        [project.entry-points."pyperf.hook"]
        gcmon = "gcmon.pyperf.hook:gcmon_hook"

        # Then use in CLI
        pyperf run --hook=gcmon ...
    """

    def __init__(self, temp_dir: tempfile.TemporaryDirectory[str], pid: int | None = None) -> None:
        self._process: subprocess.Popen[bytes] | None = None
        self._temp_files: list[Path] = []
        self._temp_dir = temp_dir
        self._pid: int = pid or os.getpid()
        self._control_name = f"pyperf-hook-{self._pid}"
        self._control_address = _make_address(self._control_name)

        self._run_monitor()
        self._control_client = ControlClient(
            self._control_address,
            connection_factory=partial(
                connect_with_retry,
                timeout=_get_env_pyperf_hook_control_timeout(),
            ),
        )

    def _run_monitor(self) -> None:
        cmd = self._build_command()

        try:
            creationflags = 0
            if sys.platform == "win32":
                # Windows: Create new process group for proper signal handling
                creationflags = subprocess.CREATE_NEW_PROCESS_GROUP

            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                creationflags=creationflags,
            )

        except Exception as e:
            raise RuntimeError(
                "Failed to run gcmon module: " + str(e) + ". Ensure gcmon is installed: pip install gcmon"
            ) from e

        verbose = _get_env_pyperf_hook_verbose()
        if verbose:
            logger.debug("Started: %s", cmd)

    def _close_monitor(self) -> None:
        if self._process is None:
            return

        verbose = _get_env_pyperf_hook_verbose()
        try:
            stdout_data, _ = terminate_process(
                process=self._process,
                graceful_timeout=GRACEFUL_TIMEOUT,
                force_timeout=FORCE_TIMEOUT,
            )

            if verbose:
                log_process_output(
                    process=self._process,
                    stdout_data=stdout_data,
                )
        except Exception as e:
            if verbose:
                logger.warning("Failed to exit from gcmon hook: %s", e)
        finally:
            if verbose and self._process:
                logger.debug("Stopped gcmon process: %s", self._process)
            self._process = None

    def __enter__(self) -> GCMonitorHook:
        """Tell the monitor to start, immediately before the benchmark runs."""
        self._control_client.start_monitoring()
        return self

    def __exit__(self, *args: object) -> None:
        """Tell it to stop, immediately after."""
        self._control_client.stop_monitoring()

    def teardown(self, metadata: dict[str, Any]) -> None:
        """Combine the temp JSONL files and hand pyperf the statistics.

        Pyperf calls this once the hook is done with a process.
        """
        self._control_client.close()
        self._close_monitor()

        if not self._temp_files:
            return

        bench_name = re.sub(r"[^a-zA-Z0-9_-]", "_", metadata.get("name", ""))
        output_path = _get_env_pyperf_hook_output(bench_name, self._pid)

        try:
            # Bytes straight through, so nothing here parses a line.
            with open(output_path, "wb") as out:
                for temp_file in self._temp_files:
                    if temp_file.exists():
                        with open(temp_file, "rb") as f:
                            shutil.copyfileobj(f, out)
                        out.write(b"\n")

            ss = StreamingStats()
            if output_path.exists():
                try:
                    _replay(ss, read_jsonl(output_path))
                except Exception as e:
                    logger.warning("Failed to read combined GC metrics: %s", e)

            if ss.count():
                for key, value in to_metrics(ss).items():
                    metadata[f"gc_{key}"] = value

        except Exception as e:
            # The benchmark's own numbers outrank ours, so this warns and the
            # run stands.
            logger.warning("Failed to aggregate GC metrics: %s", e)

        finally:
            self._temp_dir.cleanup()

    def _build_command(self) -> list[str]:
        fd, filename = tempfile.mkstemp(
            dir=self._temp_dir.name,
            prefix=f"gcmon_{self._pid}_",
            suffix=".jsonl",
        )
        os.close(fd)
        filepath = Path(filename)
        self._temp_files.append(filepath)

        return [
            sys.executable,
            "-m",
            "gcmon",
            "monitor",
            str(self._pid),
            "-vvv",
            "-o",
            filepath.as_posix(),
            "--format",
            "jsonl",
            "--flush-threshold",
            "10",
            "--control-name",
            self._control_name,
        ]


def gcmon_hook(temp_dir: str | Path | None = None, pid: int | None = None) -> GCMonitorHook:
    _setup_logging()
    temp_dir_obj = tempfile.TemporaryDirectory(dir=temp_dir or _get_env_pyperf_hook_temp_dir())
    try:
        return GCMonitorHook(temp_dir=temp_dir_obj, pid=pid)
    except Exception:
        temp_dir_obj.cleanup()
        raise


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
