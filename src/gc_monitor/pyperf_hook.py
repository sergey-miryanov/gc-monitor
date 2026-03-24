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
import time
from pathlib import Path
from random import randint
from typing import Any

from ._process_terminator import log_process_output, terminate_process

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

            # Write combined Chrome Trace format file (preserving thread names from each file)
            self._write_combined_trace(combined_file, bench_name, jsonl_events)

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

    def _write_combined_trace(
        self,
        output_path: Path,
        bench_name: str,
        events: list[dict[str, Any]],
    ) -> None:
        r"""Write combined events to a Chrome Trace format file.

        Events are written as a JSON array compatible with Chrome DevTools
        Performance panel. Thread name metadata is included first, followed
        by all events.

        Args:
            output_path: Path to output file
            bench_name: Name of the benchmark (used for process name)
            events: List of JSONL event dictionaries
        """
        pids: set[str] = set()
        trace_events: dict[tuple[str, str], list[dict[str, Any]]] = {}
        last_ts: int = 0
        for event in events:
            ts = event.get("ts", -1)
            if ts > last_ts:
                tid = str(event.get("tid", "<null>"))
                pid = str(event.get("pid", "<null>"))
                pids.add(pid)
                # Convert JSONL event to Chrome Trace format
                if (pid, tid) not in trace_events:
                    trace_events[(pid, tid)] = []
                trace_events[(pid, tid)].extend(_convert_jsonl_to_trace_format(event))
                last_ts = ts

        linesep = "\n"
        with open(output_path, "w", encoding="utf-8") as f:
            f.write("[")
            # Write opening bracket and process name metadata
            for idx, pid in enumerate(pids):
                process_name = {
                    "name": "process_name",
                    "ph": "M",
                    "pid": pid,
                    "args": {"name": f"{pid}: {bench_name} benchmark"},
                }
                if idx > 0:
                    f.write(",")
                f.write(linesep + json.dumps(process_name))

            # Write thread name metadata for each unique thread
            for pid, tid in trace_events.keys():
                f.write(f",{linesep}")
                thread_name = {
                    "name": "thread_name",
                    "ph": "M",
                    "pid": pid,
                    "tid": tid,
                    "args": {"name": f"run {tid}"},
                }
                f.write(json.dumps(thread_name))

            # Write all events in Chrome Trace format
            for (pid, tid), thread_events in trace_events.items():
                if thread_events:
                    ts_us = 0
                    gens : set[int] = set()
                    for event in thread_events:
                        f.write(f",{linesep}")
                        f.write(json.dumps(event))
                        ts_us = event["ts"]
                        if "generation" in event["args"]:
                            gens.add(event["args"]["generation"])

                    finish_events = [
                        {
                            "name": "Heap Size",
                            "cat": "gc.heap_size",
                            "ph": "C",
                            "ts": ts_us + 1_000,
                            "pid": pid,
                            "tid": tid,
                            "args": {
                                "heap_size": 0,
                            },
                        },
                    ]
                    for gen in gens:
                        counter_data = {
                            "heap_size": 0,
                            "collected": 0,
                            "uncollectable": 0,
                            "candidates": 0,
                            "object_visits": 0,
                        }
                        if gen == 1:
                            counter_data.update(
                                {
                                    "objects_transitively_reachable": 0,
                                    "objects_not_transitively_reachable": 0,
                                    "work_to_do": 0,
                                }
                            )

                        finish_events.append(
                            {
                                "name": f"Memory Counters (gen={gen})",
                                "cat": f"gc.memory(gen={gen})",
                                "ph": "C",
                                "ts": ts_us + 1_000,
                                "pid": pid,
                                "tid": tid,
                                "args": counter_data,
                            }
                        )

                    for event in finish_events:
                        f.write(f",{linesep}")
                        f.write(json.dumps(event))

            # Write closing bracket
            f.write(f"{linesep}]{linesep}")

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


def _convert_jsonl_to_trace_format(event: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Convert a JSONL event to Chrome Trace format events.

    JSONL format has flat fields (gen, ts, collections, etc.).
    Chrome Trace format has structured events with 'name', 'cat', 'ph', 'args', etc.

    Args:
        event: JSONL event dictionary with flat fields

    Returns:
        List of Chrome Trace format event dictionaries
    """
    # Convert timestamp from nanoseconds to microseconds
    ts_us = int(event.get("ts", 0) / 1_000)
    # Convert duration from seconds to microseconds
    dur_us = int(event.get("duration", 0.0) * 1_000_000)

    gen = event.get("gen", 0)

    pause_data = {
        "generation": gen,
        "collections": event.get("collections", 0),
        "total_duration": event.get("total_duration", 0.0),
        "heap_size": event.get("heap_size", 0),
        "collected": event.get("collected", 0),
        "uncollectable": event.get("uncollectable", 0),
        "candidates": event.get("candidates", 0),
        "object_visits": event.get("object_visits", 0),
        "objects_transitively_reachable": event.get("objects_transitively_reachable", 0),
        "objects_not_transitively_reachable": event.get("objects_not_transitively_reachable", 0),
        "work_to_do": event.get("work_to_do", 0),
    }

    counter_data = {
        "heap_size": event.get("heap_size", 0),
        "collected": event.get("collected", 0),
        "uncollectable": event.get("uncollectable", 0),
        "candidates": event.get("candidates", 0),
        "object_visits": event.get("object_visits", 0),
    }

    if gen == 1:
        counter_data.update(
            {
                "objects_transitively_reachable": event.get("objects_transitively_reachable", 0),
                "objects_not_transitively_reachable": event.get("objects_not_transitively_reachable", 0),
                "work_to_do": event.get("work_to_do", 0),
            }
        )

    pid = event.get("pid", "<null>")
    tid = event.get("tid", "<null>")
    trace_events: list[dict[str, Any]] = [
        # Complete event for GC pause visualization
        {
            "name": "GC Pause",
            "cat": "gc.pause",
            "ph": "X",
            "ts": ts_us,
            "dur": dur_us,
            "pid": pid,
            "tid": tid,
            "args": pause_data,
        },
        {
            "name": f"GC Pause (gen={gen})",
            "cat": f"gc.pause(gen={gen})",
            "ph": "X",
            "ts": ts_us,
            "dur": dur_us,
            "pid": pid,
            "tid": tid,
            "args": pause_data,
        },
        # Counter event for memory metrics
        {
            "name": f"Memory Counters (gen={gen})",
            "cat": f"gc.memory(gen={gen})",
            "ph": "C",
            "ts": ts_us,
            "pid": pid,
            "tid": tid,
            "args": counter_data,
        },
        {
            "name": "Heap Size",
            "cat": "gc.heap_size",
            "ph": "C",
            "ts": ts_us,
            "pid": pid,
            "tid": tid,
            "args": {
                "heap_size": event.get("heap_size", 0),
            },
        },
    ]

    return trace_events


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
