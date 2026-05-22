"""Pyperf hook for GC monitoring via external process.

This module provides a pyperf hook that spawns an external gc-monitor process
to collect garbage collection statistics. Temporary files are written in JSONL
format (one JSON object per line) during monitoring, and the final combined
output is written in Chrome Trace format (JSON array) for visualization.
"""

import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from ..exporters.chrome_trace_io import read_jsonl
from ..stats import StreamingStats
from ..utils.process_terminator import log_process_output, terminate_process

GRACEFUL_TIMEOUT = 5.0
FORCE_TIMEOUT = 2.0

# Environment variable constants
ENV_PYPERF_HOOK_OUTPUT = "GC_MONITOR_PYPERF_HOOK_OUTPUT"
ENV_PYPERF_HOOK_TEMP_DIR = "GC_MONITOR_PYPERF_HOOK_TEMP_DIR"
ENV_PYPERF_HOOK_VERBOSE = "GC_MONITOR_PYPERF_HOOK_VERBOSE"

logger = logging.getLogger("gc_monitor")


def _get_env_pyperf_hook_verbose() -> bool:
    """
    Check if verbose mode is enabled via environment variable.

    Returns:
        True if GC_MONITOR_PYPERF_HOOK_VERBOSE is set to '1', 'yes', 'on', or 'true'
        (case-insensitive), False otherwise.
    """
    value = os.environ.get(ENV_PYPERF_HOOK_VERBOSE, "").lower()
    return value in ("1", "yes", "on", "true")


def _get_env_pyperf_hook_temp_dir() -> str | None:
    """
    Get the directory for temporary files.

    Returns the value of GC_MONITOR_PYPERF_HOOK_TEMP_DIR if set,
    or None to use the system default temp directory.
    """
    return os.environ.get(ENV_PYPERF_HOOK_TEMP_DIR) or None


def _get_env_pyperf_hook_output(bench_name: str, pid: int) -> Path:
    """
    Get the output path for the combined GC trace file.

    Uses the environment variable GC_MONITOR_PYPERF_HOOK_OUTPUT if set,
    otherwise returns the default path.

    Args:
        bench_name: Name of the benchmark (sanitized)
        pid: Process ID

    Returns:
        Path to the output file
    """
    env_path = os.environ.get(ENV_PYPERF_HOOK_OUTPUT)
    if env_path:
        env_path = env_path.format(bench_name=bench_name, pid=pid)
        return Path(env_path)
    return Path(f"gc_monitor_{bench_name}_combined_{pid}.jsonl")


class GCMonitorHook:
    """
    Pyperf hook for GC monitoring via external gc-monitor process.

    The hook spawns an external `gc-monitor` CLI process that reads the
    benchmark process memory directly. Results are written to temp JSONL
    files (one JSON object per line) in the current directory with masked
    filenames, which the hook combines into a single JSONL file
    and injects into pyperf metadata.

    Usage:
        # Entry point registration in pyproject.toml
        [project.entry-points."pyperf.hook"]
        gc_monitor = "gc_monitor.pyperf.hook:gc_monitor_hook"

        # Then use in CLI
        pyperf run --hook=gc_monitor ...
    """

    def __init__(self) -> None:
        """
        Initialize the hook (called once per process).
        """
        self._process: subprocess.Popen[bytes] | None = None
        self._temp_files: list[Path] = []
        self._temp_dir = tempfile.TemporaryDirectory(dir=_get_env_pyperf_hook_temp_dir())
        self._pid: int = os.getpid()

    def __enter__(self) -> GCMonitorHook:
        """
        Called immediately before running benchmark code.

        Spawns the external gc-monitor process as a background subprocess.
        """
        cmd = self._build_command()

        if sys.platform == "win32":
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            creationflags = 0

        try:
            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                creationflags=creationflags,
            )
        except Exception as e:
            raise RuntimeError(
                "Failed to run gc-monitor module: "
                + str(e)
                + ". Ensure gc-monitor is installed: pip install gc-monitor"
            ) from e

        verbose = _get_env_pyperf_hook_verbose()
        if verbose:
            logger.debug("Started: %s", cmd)

        return self

    def __exit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc_value: BaseException | None,
        _traceback: object | None,
    ) -> None:
        """
        Called immediately after running benchmark code.
        """
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
                logger.warning("Failed to exit from gc_monitor hook: %s", e)
        finally:
            if verbose and self._process:
                logger.debug("Stopped gc-monitor process: %s", self._process)
            self._process = None

    def teardown(self, metadata: dict[str, Any]) -> None:
        """
        Called when the hook is completed for a process.

        Combines all temp JSONL files into a single JSONL file,
        aggregates statistics, and adds them to pyperf metadata.
        """
        if not self._temp_files:
            return

        bench_name = re.sub(r"[^a-zA-Z0-9_-]", "_", metadata.get("name", ""))
        output_path = _get_env_pyperf_hook_output(bench_name, self._pid)

        try:
            # Combine all temp JSONL files into one via raw byte copy
            with open(output_path, "wb") as out:
                for temp_file in self._temp_files:
                    if temp_file.exists():
                        with open(temp_file, "rb") as f:
                            shutil.copyfileobj(f, out)
                        out.write(b"\n")

            # Read combined file and aggregate statistics
            ss = StreamingStats()
            if output_path.exists():
                try:
                    parsed = read_jsonl(output_path)
                    for pid, items in parsed.items():
                        for item in items:
                            ss.update(pid, item)
                except Exception as e:
                    logger.warning("Failed to read combined GC metrics: %s", e)

            if ss.count():
                aggregated = ss.aggregate()
                for key, value in aggregated.items():
                    metadata[f"gc_{key}"] = value

        except Exception as e:
            # Log but don't fail - benchmark results are more important
            logger.warning("Failed to aggregate GC metrics: %s", e)

        finally:
            self._temp_dir.cleanup()

    def _build_command(self) -> list[str]:
        fd, filename = tempfile.mkstemp(
            dir=self._temp_dir.name,
            prefix=f"gc_monitor_{self._pid}_",
            suffix=".jsonl",
        )
        os.close(fd)
        self._temp_files.append(Path(filename))

        return [
            sys.executable,
            "-m",
            "gc_monitor",
            "monitor",
            str(self._pid),
            "-o",
            filename,
            "--format",
            "jsonl",
            "--flush-threshold",
            "10",
        ]

# Entry point factory function
def gc_monitor_hook() -> GCMonitorHook:
    return GCMonitorHook()
