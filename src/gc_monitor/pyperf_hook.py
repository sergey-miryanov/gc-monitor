"""Pyperf hook for GC monitoring via external process."""

import json
import logging
import os
import signal
import statistics
import subprocess
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger("gc_monitor")


class GCMonitorHook:
    """
    Pyperf hook for GC monitoring via external gc-monitor process.

    The hook spawns an external `gc-monitor` CLI process that reads the
    benchmark process memory directly. Results are written to temp JSON
    files in the current directory with masked filenames, which the hook
    combines and injects into pyperf metadata.

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
        # Build CLI command
        cmd = self._build_command()

        # Spawn external gc-monitor process
        try:
            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                # Windows-specific: CREATE_NEW_PROCESS_GROUP for clean termination
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
            )
        except FileNotFoundError as e:
            raise RuntimeError(
                "gc-monitor CLI not found. Ensure gc-monitor is installed: pip install gc-monitor"
            ) from e

        # Small delay to ensure gc-monitor attaches before benchmark starts
        time.sleep(0.05)

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

        try:
            # Send SIGINT for graceful shutdown
            if os.name == "nt":
                # Windows: Use CTRL_BREAK_EVENT for console processes
                self._process.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                # Unix: SIGINT
                os.kill(self._process.pid, signal.SIGINT)

            # Wait for clean exit (timeout: 5 seconds)
            try:
                self._process.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                # Fallback to SIGTERM, then SIGKILL
                if os.name != "nt":
                    os.kill(self._process.pid, signal.SIGTERM)
                    try:
                        self._process.wait(timeout=2.0)
                    except subprocess.TimeoutExpired:
                        os.kill(self._process.pid, signal.SIGKILL)  # type: ignore[attr-defined]
                else:
                    self._process.kill()
                    self._process.wait(timeout=2.0)

        except Exception as e:
            logger.warning("Failed to exit from gc_monitor hook: %s", e)
        finally:
            self._process = None

    def teardown(self, metadata: dict[str, Any]) -> None:
        """
        Called when the hook is completed for a process.

        Combines all temp JSON files into a single Chrome Trace format file,
        aggregates statistics, and adds them to pyperf metadata.

        Args:
            metadata: Pyperf metadata dictionary (modified in-place)
        """
        if not self._temp_files:
            return

        bench_name: str = metadata.get("name", "")
        combined_file = Path(f"gc_monitor_{bench_name}_combined_{self._pid}.json")

        try:
            # Combine all temp files into a single Chrome Trace format file
            all_events: list[dict[str, Any]] = []
            threads: list[dict[str, Any]] = []

            for temp_file in self._temp_files:
                if temp_file.exists():
                    try:
                        with open(temp_file, "r", encoding="utf-8") as f:
                            filedata = f.read()
                            try:
                                data: list[dict[str, Any]] = json.loads(filedata)
                            except json.JSONDecodeError:
                                filedata = f"{filedata}]"

                            data = json.loads(filedata)
                            # Extract events from Chrome Trace format array
                            for item in data:
                                ph: str = item.get("ph", '')
                                if ph == "M" and item.get("name") == "thread_name":
                                    threads.append(item)
                                # Collect only event entries (ph=X or ph=C), not metadata
                                if ph in ("X", "C"):
                                    all_events.append(item)
                    except (json.JSONDecodeError, IOError) as e:
                        logger.warning("Failed to read GC metrics from %s: %s", temp_file, e)

            # Write combined Chrome Trace format file (preserving thread names from each file)
            self._write_combined_trace(combined_file, bench_name, threads, all_events)

            # Aggregate and add to metadata with gc_ prefix
            if all_events:
                aggregated = _aggregate_gc_stats(all_events)
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
        threads: list[dict[str, Any]],
        events: list[dict[str, Any]],
    ) -> None:
        """
        Write combined events to a Chrome Trace format file.

        Preserves thread names from each source file.

        Args:
            output_path: Path to output file
            threads: List of thread name metadata items (tid values)
            events: List of event dictionaries (each with its own tid)
        """

        process_name = {
            "name": "process_name",
            "ph": "M",
            "pid": self._pid,
            "tid": "GC Monitor",
            "args": {"name": f"{bench_name} benchmark"},
        }

        linesep = "\n"
        with open(output_path, "w", encoding="utf-8") as f:
            # Write opening bracket and metadata
            process_name_str = json.dumps(process_name)
            f.write(f"[{linesep}{process_name_str}")

            # Write thread name metadata for each unique thread
            for thread in threads:
                f.write(f",{linesep}")
                f.write(json.dumps(thread))

            # Write all events (each with its original tid)
            for event in events:
                f.write(f",{linesep}")
                f.write(json.dumps(event))

            # Write closing bracket
            f.write(f"{linesep}]{linesep}")

    def _build_command(self) -> list[str]:
        """Build the gc-monitor CLI command."""
        filename = f"gc_monitor_{self._pid}_{self._run_index}.json"
        thread_name = f"GC Monitor (run={self._run_index})"
        self._run_index += 1

        temp_file = Path(filename)
        self._temp_files.append(temp_file)

        cmd: list[str] = [
            "gc-monitor",
            str(self._pid),
            "-o",
            str(filename),
            "--format",
            "chrome",
            "--thread-name",
            thread_name,
        ]

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

    # Group by generation
    by_gen: dict[int, list[dict[str, Any]]] = {0: [], 1: [], 2: []}

    for event in events:
        # Chrome Trace format: metrics are in 'args' field
        # Flat format: metrics are at top level
        metrics = event.get("args", event)

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

    event_count = len(events)

    result: dict[str, Any] = {}

    # Per-generation collection counts
    for gen, gen_events in by_gen.items():
        result[f"collections_by_gen_{gen}"] = sum(
            e.get("args", e).get("collections", 0) for e in gen_events
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
            # Event count
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
