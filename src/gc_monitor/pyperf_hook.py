"""Pyperf hook for GC monitoring via external process.

This module provides a pyperf hook that spawns an external gc-monitor process
to collect garbage collection statistics. Temporary files are written in JSONL
format (one JSON object per line) during monitoring, and the final combined
output is written in Chrome Trace format (JSON array) for visualization.
"""

import json
import logging
import os
import re
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from random import randint
from typing import Any

from ._process_terminator import log_process_output, terminate_process
from .chrome_trace_exporter import combine_files, write_jsonl_events_to_trace

# Environment variable constants
ENV_PYPERF_HOOK_OUTPUT = "GC_MONITOR_PYPERF_HOOK_OUTPUT"
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
        env_path = env_path.format(bench_name=bench_name)
        return Path(env_path)
    return Path(f"gc_monitor_{bench_name}_combined_{pid}.json")


class GCMonitorHook:
    """
    Pyperf hook for GC monitoring via external gc-monitor process.

    The hook spawns an external `gc-monitor` CLI process that reads the
    benchmark process memory directly. Results are written to temp JSONL
    files (one JSON object per line) in the current directory with masked
    filenames, which the hook combines into a single Chrome Trace format
    file (JSON array) and injects into pyperf metadata.

    Usage:
        # Via CLI
        pyperf run --hook=gc_monitor ...

        # Via entry point registration in pyproject.toml
        [project.entry-points."pyperf.hook"]
        gc_monitor = "gc_monitor.pyperf_hook:gc_monitor_hook"
    """

    def __init__(self) -> None:
        """
        Initialize the hook (called once per process).
        """
        self._process: subprocess.Popen[bytes] | None = None
        self._run_index: int = 0
        self._temp_files: list[Path] = []
        self._pid: int = os.getpid()

    def __enter__(self) -> "GCMonitorHook":
        """
        Called immediately before running benchmark code.

        Spawns the external gc-monitor process as a background subprocess.
        """
        verbose = _get_env_pyperf_hook_verbose()

        # Build CLI command
        cmd = self._build_command()

        # Spawn external gc-monitor process
        try:
            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                # Windows-specific: CREATE_NEW_PROCESS_GROUP for clean termination
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
            )
        except FileNotFoundError as e:
            raise RuntimeError(
                "Failed to run gc-monitor module: "
                + str(e)
                + ". Ensure gc-monitor is installed: pip install gc-monitor"
            ) from e

        # Small delay to ensure gc-monitor attaches before benchmark starts
        time.sleep(0.05)
        if verbose:
            logger.info("Started: %s", cmd)

        return self

    def __exit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc_value: BaseException | None,
        _traceback: object | None,
    ) -> None:
        """
        Called immediately after running benchmark code.

        Sends SIGINT to the external gc-monitor process for graceful shutdown,
        allowing it to flush final data and exit cleanly.
        Exceptions from benchmark are ignored (we still collect GC stats).
        """
        if self._process is None:
            return

        verbose = _get_env_pyperf_hook_verbose()
        if verbose:
            logger.info("Stopping gc-monitor process: %s", self._process)

        try:
            # Terminate the process gracefully with escalating signals
            stdout_data, stderr_data = terminate_process(
                process=self._process,
                verbose=verbose,
                graceful_timeout=5.0,
                force_timeout=2.0,
            )

            # Log output based on exit code and verbose flag
            log_process_output(
                process=self._process,
                stdout_data=stdout_data,
                stderr_data=stderr_data,
                verbose=verbose,
                logger=logger,
            )
        except Exception as e:
            logger.warning("Failed to exit from gc_monitor hook: %s", e)
        finally:
            if verbose:
                logger.info("Stopped gc-monitor process: %s", self._process)
            self._process = None

    def teardown(self, metadata: dict[str, Any]) -> None:
        """
        Called when the hook is completed for a process.

        Combines all temp JSONL files into a single Chrome Trace format file,
        aggregates statistics, and adds them to pyperf metadata.

        Args:
            metadata: Pyperf metadata dictionary (modified in-place)
        """
        if not self._temp_files:
            return

        bench_name: str = metadata.get("name", "")
        bench_name = re.sub(r"[^a-zA-Z0-9_-]", "_", bench_name)
        combined_file = _get_env_pyperf_hook_output(bench_name, self._pid)

        # Check if we need to combine with existing output file
        env_output_set = os.environ.get(ENV_PYPERF_HOOK_OUTPUT) is not None
        existing_file_exists = combined_file.exists() if env_output_set else False

        try:
            # Combine all temp files into a single Chrome Trace format file
            jsonl_events: list[dict[str, Any]] = []
            for temp_file in self._temp_files:
                if temp_file.exists():
                    try:
                        with open(temp_file, "r", encoding="utf-8") as f:
                            for line in f:
                                line = line.strip()
                                if line:  # Skip empty lines
                                    event = json.loads(line)
                                    jsonl_events.append(event)
                    except (json.JSONDecodeError, IOError) as e:
                        logger.warning("Failed to read GC metrics from %s: %s", temp_file, e)

            # Write to a temp file first, then combine with existing file if needed
            if env_output_set and existing_file_exists:
                # Write new data to a temp file
                with tempfile.NamedTemporaryFile(
                    mode="w", suffix=".json", delete_on_close=False, encoding="utf-8"
                ) as tmp:
                    temp_output_path = Path(tmp.name)
                    write_jsonl_events_to_trace(temp_output_path, bench_name, jsonl_events)

                    # Combine existing file with new temp file
                    combine_files(
                        input_paths=[combined_file, temp_output_path],
                        output_path=combined_file,
                        normalize=False,
                    )
            else:
                # Write directly to output file (no existing file to combine with)
                write_jsonl_events_to_trace(combined_file, bench_name, jsonl_events)

            # Aggregate and add to metadata with gc_ prefix
            if jsonl_events:
                aggregated = _aggregate_gc_stats(jsonl_events)
                for key, value in aggregated.items():
                    metadata[f"gc_{key}"] = value

        except Exception as e:
            # Log but don't fail - benchmark results are more important
            logger.warning("Failed to aggregate GC metrics: %s", e)

        finally:
            # Cleanup temp files
            self._cleanup_temp_files()

    def _build_command(self) -> list[str]:
        """
        Build the gc-monitor CLI command with current run configuration.

        Creates a unique temp file path for each run and configures the
        gc-monitor CLI with JSONL format and thread-id for event tracking.

        Returns:
            List of command-line arguments for subprocess.Popen
        """
        rnd = randint(0, 100)
        filename = f"gc_monitor_{self._pid}_{self._run_index}_{rnd}.jsonl"

        temp_file = Path(filename)
        self._temp_files.append(temp_file)

        # Use sys.executable to run gc_monitor as a module
        # This ensures the correct Python interpreter is used
        # Use JSONL format for temporary files (simpler parsing, no array issues)
        cmd: list[str] = [
            sys.executable,
            "-m",
            "gc_monitor",
            str(self._pid),
            "-o",
            str(filename),
            "--format",
            "jsonl",
            "--thread-id",
            str(self._run_index),
            "--fallback",
            "no",
            "--flush-threshold",
            "10",
        ]

        self._run_index += 1
        return cmd

    def _cleanup_temp_files(self) -> None:
        """Remove all temp files if they exist."""
        for temp_file in self._temp_files:
            if temp_file.exists():
                try:
                    temp_file.unlink()
                except OSError:
                    pass  # Ignore cleanup errors
        self._temp_files.clear()


def _aggregate_gc_stats(events: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Aggregate GC statistics from JSON events.

    Handles both flat event dictionaries and Chrome Trace format events
    (where metrics are nested in the 'args' field).

    Only aggregates from "X" phase events (GC pauses), not "C" phase events
    (counters).

    Args:
        events: List of GC event dictionaries from JSON file

    Returns:
        Dictionary of aggregated metrics
    """
    if not events:
        return {}

    # Initialize aggregations
    total_collections = 0
    total_collected = 0
    total_uncollectable = 0
    total_duration = 0.0
    durations: list[float] = []
    heap_sizes: list[int] = []
    object_visits: list[int] = []
    event_count = 0

    # Group by generation
    by_gen: dict[int, list[dict[str, Any]]] = {0: [], 1: [], 2: []}

    for event in events:
        # Flat JSONL format: metrics are at top level
        # Only process if it has the expected fields (gen, collections, etc.)
        if "gen" not in event or "collections" not in event:
            continue
        metrics = event
        event_count += 1

        total_collections += metrics.get("collections", 0)
        total_collected += metrics.get("collected", 0)
        total_uncollectable += metrics.get("uncollectable", 0)
        duration = metrics.get("duration", 0.0)
        total_duration += duration
        durations.append(duration)
        heap_sizes.append(metrics.get("heap_size", 0))
        object_visits.append(metrics.get("object_visits", 0))

        gen: int = metrics.get("gen", 0)
        if gen in by_gen:
            by_gen[gen].append(event)


    result: dict[str, Any] = {}

    # Per-generation collection counts
    for gen, gen_events in by_gen.items():
        result[f"collections_by_gen_{gen}"] = sum(
            e.get("collections", 0) for e in gen_events
        )

    result.update(
        {
            # Cumulative metrics
            "collections_total": total_collections,
            "objects_collected_total": total_collected,
            "objects_uncollectable_total": total_uncollectable,
            "total_duration_sec": total_duration,
            # Average metrics
            "avg_pause_duration_sec": statistics.mean(durations) if durations else 0.0,
            "avg_heap_size": statistics.mean(heap_sizes) if heap_sizes else 0,
            "avg_object_visits": statistics.mean(object_visits) if object_visits else 0,
            # Max metrics
            "max_pause_duration_sec": max(durations) if durations else 0.0,
            "max_heap_size": max(heap_sizes) if heap_sizes else 0,
            "max_object_visits": max(object_visits) if object_visits else 0,
            # Min metrics
            "min_pause_duration_sec": min(durations) if durations else 0.0,
            "min_heap_size": min(heap_sizes) if heap_sizes else 0,
            # Statistics
            "std_pause_duration_sec": statistics.stdev(durations)
            if len(durations) > 1
            else 0.0,
            # Event count (original JSONL events, not expanded trace events)
            "event_count": event_count,
        }
    )

    return result


# Entry point factory function
def gc_monitor_hook() -> GCMonitorHook:
    """
    Factory function for pyperf entry point.

    Returns:
        GCMonitorHook instance
    """
    return GCMonitorHook()
