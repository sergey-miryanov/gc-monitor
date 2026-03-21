"""Pyperf hook for GC monitoring via external process."""

import json
import logging
import os
import signal
import statistics
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional


class GCMonitorHook:
    """
    Pyperf hook for GC monitoring via external gc-monitor process.

    The hook spawns an external `gc-monitor` CLI process that reads the
    benchmark process memory directly. Results are written to a temp JSON
    file, which the hook reads and injects into pyperf metadata.

    Usage:
        # Via CLI
        pyperf run --hook=gc_monitor ...

        # Via entry point registration in pyproject.toml
        [project.entry-points."pyperf.hook"]
        gc_monitor = "gc_monitor.pyperf_hook:gc_monitor_hook"
    """

    def __init__(
        self,
        duration: float = 0.0,
        output_dir: Optional[Path] = None,
    ) -> None:
        """
        Initialize the hook (called once per process).

        Args:
            duration: Monitoring duration in seconds (0 = until benchmark ends)
            output_dir: Directory for temp files (default: system temp)
        """
        self._duration = duration
        self._output_dir = output_dir
        self._temp_file: Optional[Path] = None
        self._process: Optional[subprocess.Popen[bytes]] = None
        self._pid: int = 0
        self._start_time: float = 0.0

    def __enter__(self) -> "GCMonitorHook":
        """
        Called immediately before running benchmark code.

        Spawns the external gc-monitor process as a background subprocess.
        """
        self._pid = os.getpid()
        self._start_time = time.monotonic()

        # Generate temp file path
        temp_dir = self._output_dir or Path(tempfile.gettempdir())
        self._temp_file = temp_dir / f"gc_monitor_{self._pid}_{int(time.time() * 1000)}.json"

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
        _exc_type: Optional[type[BaseException]],
        _exc_value: Optional[BaseException],
        _traceback: Optional[object],
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
                        os.kill(self._process.pid, signal.SIGKILL)
                else:
                    self._process.kill()
                    self._process.wait(timeout=2.0)

        except Exception:
            # Ignore cleanup errors - benchmark exception is more important
            pass
        finally:
            self._process = None

    def teardown(self, metadata: Dict[str, Any]) -> None:
        """
        Called when the hook is completed for a process.

        Reads the temp JSON file, aggregates statistics, and adds them
        to pyperf metadata.

        Args:
            metadata: Pyperf metadata dictionary (modified in-place)
        """
        if not self._temp_file or not self._temp_file.exists():
            return

        try:
            # Read JSON file
            with open(self._temp_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            # Parse and aggregate
            if "events" in data:
                aggregated = _aggregate_gc_stats(data["events"])

                # Add to metadata with gc_ prefix
                for key, value in aggregated.items():
                    metadata[f"gc_{key}"] = value

        except (json.JSONDecodeError, IOError) as e:
            # Log but don't fail - benchmark results are more important
            logger = logging.getLogger("gc_monitor")
            logger.warning("Failed to read GC metrics: %s", e)

        finally:
            # Cleanup temp file
            self._cleanup_temp_file()

    def _build_command(self) -> list[str]:
        """Build the gc-monitor CLI command."""
        cmd = [
            "gc-monitor",
            str(self._pid),
            "-o",
            str(self._temp_file),
        ]

        if self._duration > 0:
            cmd.extend(["-d", str(self._duration)])
        else:
            # Run until terminated (benchmark controls lifecycle)
            cmd.extend(["-d", "0"])

        # Add format flag for pyperf-compatible JSON
        cmd.extend(["--format", "pyperf"])

        return cmd

    def _cleanup_temp_file(self) -> None:
        """Remove temp file if it exists."""
        if self._temp_file and self._temp_file.exists():
            try:
                self._temp_file.unlink()
            except OSError:
                pass  # Ignore cleanup errors


def _aggregate_gc_stats(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Aggregate GC statistics from JSON events.

    Returns a dictionary of aggregated metrics ready for pyperf metadata.

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
    durations: List[float] = []
    heap_sizes: List[int] = []
    object_visits: List[int] = []

    # Group by generation
    by_gen: Dict[int, List[Dict[str, Any]]] = {0: [], 1: [], 2: []}

    for event in events:
        total_collections += event.get("collections", 0)
        total_collected += event.get("collected", 0)
        total_uncollectable += event.get("uncollectable", 0)
        duration = event.get("duration", 0.0)
        total_duration += duration
        durations.append(duration)
        heap_sizes.append(event.get("heap_size", 0))
        object_visits.append(event.get("object_visits", 0))

        gen = event.get("gen", 0)
        if gen in by_gen:
            by_gen[gen].append(event)

    event_count = len(events)

    result: Dict[str, Any] = {}

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
            # Event count
            "event_count": event_count,
        }
    )

    return result


# Entry point factory function
def gc_monitor_hook(
    duration: float = 0.0,
    output_dir: Optional[Path] = None,
) -> GCMonitorHook:
    """
    Factory function for pyperf entry point.

    Args:
        duration: Monitoring duration in seconds (0 = until benchmark ends)
        output_dir: Directory for temp files (default: system temp)

    Returns:
        GCMonitorHook instance
    """
    return GCMonitorHook(duration=duration, output_dir=output_dir)
