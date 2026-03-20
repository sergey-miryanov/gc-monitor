# Pyperf Hook Integration Plan (External Process Architecture)

## 1. Architecture Overview

### 1.1 External Process Model

The `GCMonitorHook` integrates gc-monitor into the pyperf benchmarking framework using an **external process architecture**. The hook spawns a separate `gc-monitor` CLI process that reads the benchmark process memory directly, eliminating in-process overhead.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        Pyperf Benchmark Runner                              │
│                                                                             │
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │                    Benchmark Execution Flow                           │  │
│  │                                                                       │  │
│  │  ┌─────────────┐                                                     │  │
│  │  │ Hook Init   │  ← GCMonitorHook.__init__()                         │  │
│  │  │ (once)      │     - Generate temp file path                        │  │
│  │  └──────┬──────┘     - Prepare CLI command                            │  │
│  │         │                                                         │  │
│  │         ▼                                                         │  │
│  │  ┌─────────────────────────────────────────────────────────────┐    │  │
│  │  │  For Each Benchmark Run:                                    │    │  │
│  │  │                                                             │    │  │
│  │  │  ┌──────────────┐                                          │    │  │
│  │  │  │ __enter__()  │  ← Spawn external gc-monitor process      │    │  │
│  │  │  │              │     - Start: gc-monitor <pid> -o <file>   │    │  │
│  │  │  │              │     - Background subprocess               │    │  │
│  │  │  └──────┬───────┘                                          │    │  │
│  │         │                                                   │    │  │
│  │         ▼                                                   │    │  │
│  │  ┌──────────────┐         ┌─────────────────────────────┐   │    │  │
│  │  │ BENCHMARK    │         │  External gc-monitor        │   │    │  │
│  │  │  CODE         │◄───────►│  Reader Process             │   │    │  │
│  │  │ (main proc)  │  PID    │  - Reads benchmark memory   │   │    │  │
│  │  └──────┬───────┘         │  - Writes JSON to temp file │   │    │  │
│  │         │                 └─────────────────────────────┘   │    │  │
│  │         │                                                   │    │  │
│  │         ▼                                                   │    │  │
│  │  ┌──────────────┐                                          │    │  │
│  │  │ __exit__()   │  ← Send SIGINT to subprocess             │    │  │
│  │  │              │     - Graceful shutdown via signal        │    │  │
│  │  │              │     - Wait for clean exit                 │    │  │
│  │  └──────────────┘                                          │    │  │
│  │                                                             │    │  │
│  └─────────────────────────────────────────────────────────────┘    │  │
│  │                                                                       │  │
│  │         ▼ (after all benchmark runs complete)                        │  │
│  │  ┌─────────────┐                                                     │  │
│  │  │ teardown()  │  ← Read temp JSON, inject to metadata              │  │
│  │  │             │     - Parse JSON file                               │  │
│  │  │             │     - Compute aggregates                            │  │
│  │  │             │     - Add to pyperf metadata dict                   │  │
│  │  │             │     - Cleanup temp file                             │  │
│  │  └─────────────┘                                                     │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 1.2 Data Flow

```
┌─────────────────────┐         ┌─────────────────────┐         ┌─────────────────────┐
│  Pyperf Benchmark   │         │  gc-monitor CLI     │         │  Temp JSON File     │
│  Process            │         │  (External)         │         │                     │
│                     │         │                     │         │                     │
│  ┌───────────────┐  │         │  ┌───────────────┐  │         │  ┌───────────────┐  │
│  │ Benchmark     │  │         │  │ Process       │  │         │  │ GC Events     │  │
│  │ Code          │  │         │  │ Reader        │  │         │  │ (JSON Array)  │  │
│  │               │  │         │  │               │  │         │  │               │  │
│  │ [Running]     │──┼────────►│  │ [Monitors]    │──┼────────►│  │ [Written]     │  │
│  │               │  │  PID    │  │ via _gc_monitor│ │  JSON   │  │               │  │
│  └───────────────┘  │         │  └───────────────┘  │         │  └───────────────┘  │
│                     │         │                     │         │                     │
│                     │         │  ┌───────────────┐  │         │                     │
│                     │         │  │ CLI Args:     │  │         │                     │
│                     │         │  │ -o <output>   │  │         │                     │
│                     │         │  │ -d <duration> │  │         │                     │
│                     │         │  └───────────────┘  │         │                     │
│                     │         └─────────────────────┘         │                     │
│                     │                                          │                     │
│                     │         ┌─────────────────────┐         │                     │
│                     │         │  GCMonitorHook      │         │                     │
│                     │         │  (In Pyperf)        │         │                     │
│                     │         │                     │         │                     │
│                     │         │  [Reads JSON] ◄─────┼─────────┤                     │
│                     │         │  [Parses & Aggs]    │         │                     │
│                     │         │  [Injects Metadata] │         │                     │
│                     │         └─────────────────────┘         │                     │
└─────────────────────┘                                          └─────────────────────┘
```

### 1.3 Key Architectural Benefits

| Benefit | Description |
|---------|-------------|
| **Zero In-Process Overhead** | No threads, no imports, no memory pressure in benchmark process |
| **Clean Separation** | gc-monitor CLI is independent; benchmark code unchanged |
| **Crash Isolation** | gc-monitor crashes don't affect benchmark results |
| **Reusability** | Same CLI used for standalone monitoring and pyperf integration |
| **No IPC Complexity** | File-based communication avoids pipes/sockets complexity |

### 1.4 Relationship to Existing Exporters

| Component | Purpose | Output | Lifecycle |
|-----------|---------|--------|-----------|
| `TraceExporter` | Chrome DevTools visualization | JSON file | Manual start/stop |
| `GrafanaExporter` | Real-time monitoring dashboards | OTLP metrics | Continuous streaming |
| `gc-monitor CLI` | External process reader | JSON file | Per-benchmark run |
| `GCMonitorHook` | Pyperf integration | In-memory metadata | Per-benchmark run |

**Key Differences:**
- **gc-monitor CLI** reads benchmark process memory via `_gc_monitor` API (same as in-process)
- **GCMonitorHook** manages external process lifecycle, not collection
- **Temp file** is the communication channel (no network, no pipes)
- **File format** compatible with `TraceExporter` for tool interoperability

---

## 2. Hook Implementation Design

### 2.1 GCMonitorHook Class Structure

```python
"""Pyperf hook for GC monitoring via external process."""

import json
import os
import signal
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Optional


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
        output_dir: Optional[str] = None,
    ) -> None:
        """
        Initialize the hook (called once per process).

        Args:
            duration: Monitoring duration in seconds (0 = until benchmark ends)
            output_dir: Directory for temp files (default: system temp)
        """
        self._duration = duration
        self._output_dir = Path(output_dir) if output_dir else None
        self._temp_file: Optional[Path] = None
        self._process: Optional[subprocess.Popen[bytes]] = None
        self._pid: int = 0
        self._start_time: float = 0.0

    def __enter__(self) -> "GCMonitorHook":
        """
        Called immediately before running benchmark code.

        Spawns the external gc-monitor process as a background subprocess.
        """
        import os
        import tempfile

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
                "gc-monitor CLI not found. Ensure gc-monitor is installed: "
                "pip install gc-monitor"
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
            import logging
            logger = logging.getLogger("gc_monitor")
            logger.warning(f"Failed to read GC metrics: {e}")

        finally:
            # Cleanup temp file
            self._cleanup_temp_file()

    def _build_command(self) -> list[str]:
        """Build the gc-monitor CLI command."""
        cmd = [
            "gc-monitor",
            str(self._pid),
            "-o", str(self._temp_file),
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


def _aggregate_gc_stats(events: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Aggregate GC statistics from JSON events.

    Returns a dictionary of aggregated metrics ready for pyperf metadata.
    """
    if not events:
        return {}

    import statistics

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

    result: dict[str, Any] = {}

    # Per-generation collection counts
    for gen, gen_events in by_gen.items():
        result[f"collections_by_gen_{gen}"] = sum(e.get("collections", 0) for e in gen_events)

    result.update({
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
        "std_pause_duration_sec": statistics.stdev(durations) if len(durations) > 1 else 0.0,

        # Event count
        "event_count": event_count,
    })

    return result


# Entry point factory function
def gc_monitor_hook(
    duration: float = 0.0,
    output_dir: Optional[str] = None,
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
```

### 2.2 Subprocess Management

| Method | Responsibility |
|--------|----------------|
| `_build_command()` | Construct CLI args with PID, output file, duration |
| `subprocess.Popen()` | Spawn background process with proper flags |
| `__exit__().send_signal(SIGINT)` | Send SIGINT for graceful shutdown (Unix) or CTRL_BREAK_EVENT (Windows) |
| `__exit__().wait()` | Wait for clean exit with timeout |
| `__exit__().kill()` | Force kill on timeout (fallback) |
| `_cleanup_temp_file()` | Remove temp file after reading |

**Signal Handling Notes:**
- The gc-monitor CLI already handles SIGINT via `signal.signal(signal.SIGINT, ...)` for graceful shutdown
- SIGINT allows the process to flush final data and close files cleanly
- Fallback to SIGTERM/SIGKILL ensures termination even if signal handling fails

### 2.3 Monitoring Lifecycle

| Phase | Action | Timing |
|-------|--------|--------|
| `__init__` | Generate temp file path, prepare config | Once per pyperf process |
| `__enter__` | Spawn `gc-monitor` subprocess | Before each benchmark run |
| Benchmark | External process reads memory | During benchmark execution |
| `__exit__` | Send SIGINT to subprocess | After each benchmark run |
| `teardown` | Read JSON, aggregate, inject, cleanup | Once after all benchmarks complete |

### 2.4 Temp File Format

The external gc-monitor process writes JSON in a format compatible with `TraceExporter`:

```json
{
  "version": "1.0",
  "pid": 12345,
  "start_time": 1679875200.0,
  "end_time": 1679875210.0,
  "events": [
    {
      "gen": 0,
      "ts": 1679875200.123,
      "collections": 1,
      "collected": 10,
      "uncollectable": 0,
      "candidates": 5,
      "object_visits": 100,
      "heap_size": 1048576,
      "duration": 0.0023,
      "total_duration": 0.0023
    }
  ]
}
```

---

## 3. Entry Point Configuration

### 3.1 pyproject.toml Changes

Add the following to `pyproject.toml`:

```toml
[project.entry-points."pyperf.hook"]
gc_monitor = "gc_monitor.pyperf_hook:gc_monitor_hook"
```

**Full updated pyproject.toml section:**

```toml
[tool.poetry.scripts]
gc-monitor = "gc_monitor.cli:main"

[project.entry-points."pyperf.hook"]
gc_monitor = "gc_monitor.pyperf_hook:gc_monitor_hook"
```

### 3.2 CLI Dependency

The hook now depends on the `gc-monitor` CLI being available in the system PATH:

```python
# In GCMonitorHook.__enter__()
try:
    self._process = subprocess.Popen(cmd, ...)
except FileNotFoundError as e:
    raise RuntimeError(
        "gc-monitor CLI not found. Ensure gc-monitor is installed: "
        "pip install gc-monitor"
    ) from e
```

### 3.3 Package Structure

```
src/gc_monitor/
├── __init__.py              # Updated exports
├── _gc_monitor.py           # GCMonitorStatsItem, GCMonitorHandler (unchanged)
├── exporter.py              # GCMonitorExporter base class (unchanged)
├── chrome_trace_exporter.py # TraceExporter (unchanged)
├── core.py                  # GCMonitor, connect() (unchanged)
├── cli.py                   # Updated: supports -d, --format flags
├── pyperf_hook.py           # NEW: GCMonitorHook (external process model)
└── _aggregation.py          # NEW: _aggregate_gc_stats (internal)

tests/
├── test_chrome_trace.py     # Existing
├── test_cli.py              # Updated: test new flags
├── test_pyperf_hook.py      # NEW: Hook tests (mock subprocess)
└── test_aggregation.py      # NEW: Aggregation tests
```

### 3.4 Updated __init__.py

```python
"""gc_monitor package init."""
__version__ = "0.1.0"

from .core import GCMonitor, connect
from .exporter import GCMonitorExporter
from .chrome_trace_exporter import TraceExporter
from .pyperf_hook import GCMonitorHook, gc_monitor_hook

__all__ = [
    "connect",
    "GCMonitor",
    "GCMonitorExporter",
    "TraceExporter",
    "GCMonitorHook",
    "gc_monitor_hook",
    "__version__",
]
```

---

## 4. GC Metrics for Pyperf

### 4.1 Exposed GCMonitorStatsItem Fields

| Field | Type | Aggregation | Metadata Key | Rationale |
|-------|------|-------------|--------------|-----------|
| `gen` | int | Count per gen | `gc_collections_by_gen_{0,1,2}` | Generation-specific analysis |
| `collections` | int | Sum | `gc_collections_total` | Total GC cycles |
| `collected` | int | Sum | `gc_objects_collected_total` | Objects freed |
| `uncollectable` | int | Sum | `gc_objects_uncollectable_total` | Memory leaks indicator |
| `candidates` | int | Avg | `gc_avg_candidates` | Collection pressure |
| `object_visits` | int | Avg, Max | `gc_avg_object_visits`, `gc_max_object_visits` | GC work performed |
| `heap_size` | int | Avg, Max, Min | `gc_avg_heap_size`, `gc_max_heap_size`, `gc_min_heap_size` | Memory footprint |
| `duration` | float | Avg, Max, Min, Sum | `gc_avg_pause_duration_sec`, `gc_max_pause_duration_sec`, `gc_min_pause_duration_sec`, `gc_total_duration_sec` | Pause time analysis |
| `total_duration` | float | Last value | `gc_cumulative_duration_sec` | Total time in GC |

### 4.2 JSON File Format Compatibility

The external gc-monitor process writes JSON in a format compatible with `TraceExporter`:

```python
# CLI output format (--format pyperf)
{
    "version": "1.0",
    "pid": <benchmark_pid>,
    "start_time": <unix_timestamp>,
    "end_time": <unix_timestamp>,
    "events": [
        {
            "gen": 0,
            "ts": 1679875200.123,
            "collections": 1,
            "collected": 10,
            "uncollectable": 0,
            "candidates": 5,
            "object_visits": 100,
            "objects_transitively_reachable": 50,
            "objects_not_transitively_reachable": 50,
            "heap_size": 1048576,
            "work_to_do": 10,
            "duration": 0.0023,
            "total_duration": 0.0023
        }
    ]
}
```

**Benefits:**
- Same file can be loaded by Chrome DevTools (via TraceExporter compatibility)
- Hook reads same format as standalone CLI users
- Easy debugging: inspect temp file manually if needed

### 4.3 Aggregation Strategy

```python
def _aggregate_gc_stats(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate GC statistics from JSON events."""

    # Group by generation
    by_gen: dict[int, list[dict[str, Any]]] = {0: [], 1: [], 2: []}
    for event in events:
        gen = event.get("gen", 0)
        if gen in by_gen:
            by_gen[gen].append(event)

    result: dict[str, Any] = {}

    # Per-generation collection counts
    for gen, gen_events in by_gen.items():
        result[f"collections_by_gen_{gen}"] = sum(e.get("collections", 0) for e in gen_events)

    # Overall aggregations
    durations = [e.get("duration", 0.0) for e in events]
    heap_sizes = [e.get("heap_size", 0) for e in events]
    object_visits = [e.get("object_visits", 0) for e in events]

    result.update({
        # Sums (cumulative)
        "collections_total": sum(e.get("collections", 0) for e in events),
        "objects_collected_total": sum(e.get("collected", 0) for e in events),
        "objects_uncollectable_total": sum(e.get("uncollectable", 0) for e in events),
        "total_duration_sec": sum(durations),

        # Averages
        "avg_pause_duration_sec": statistics.mean(durations) if durations else 0.0,
        "avg_heap_size": statistics.mean(heap_sizes) if heap_sizes else 0,
        "avg_object_visits": statistics.mean(object_visits) if object_visits else 0,

        # Maximums (worst-case)
        "max_pause_duration_sec": max(durations) if durations else 0.0,
        "max_heap_size": max(heap_sizes) if heap_sizes else 0,
        "max_object_visits": max(object_visits) if object_visits else 0,

        # Minimums
        "min_pause_duration_sec": min(durations) if durations else 0.0,
        "min_heap_size": min(heap_sizes) if heap_sizes else 0,

        # Statistics
        "std_pause_duration_sec": statistics.stdev(durations) if len(durations) > 1 else 0.0,

        # Counts
        "event_count": len(events),
    })

    return result
```

### 4.4 Metadata Key Naming Convention

**Format:** `gc_<metric>_<aggregation>[_<unit>]`

| Pattern | Example | Description |
|---------|---------|-------------|
| `gc_<name>_total` | `gc_collections_total` | Cumulative sum |
| `gc_avg_<name>[_<unit>]` | `gc_avg_pause_duration_sec` | Arithmetic mean |
| `gc_max_<name>[_<unit>]` | `gc_max_heap_size` | Maximum value |
| `gc_min_<name>[_<unit>]` | `gc_min_heap_size` | Minimum value |
| `gc_std_<name>[_<unit>]` | `gc_std_pause_duration_sec` | Standard deviation |
| `gc_<name>_by_<category>_<id>` | `gc_collections_by_gen_0` | Breakdown by category |

---

## 5. Implementation Tasks (TDD)

### 5.1 Test File Structure

```
tests/
├── test_pyperf_hook/
│   ├── __init__.py
│   ├── test_hook_lifecycle.py      # Test __enter__, __exit__, teardown
│   ├── test_subprocess_management.py # Mock subprocess, test spawning
│   └── test_integration.py         # End-to-end with pyperf
├── test_aggregation/
│   ├── __init__.py
│   ├── test_basic_aggregation.py   # Sum, avg, max, min
│   ├── test_edge_cases.py          # Empty events, single event
│   └── test_generation_breakdown.py # Per-gen aggregations
├── test_cli/
│   └── test_pyperf_format.py       # Test --format pyperf output
└── test_metadata/
    └── test_metadata_injection.py  # Verify metadata dict updates
```

### 5.2 Implementation Phases

#### Phase 1: Core Hook Implementation (TDD)

**Task 1.1: Write Tests First**

```python
# tests/test_pyperf_hook/test_hook_lifecycle.py

import pytest
from unittest.mock import MagicMock, patch, mock_open
from gc_monitor.pyperf_hook import GCMonitorHook


class TestGCMonitorHookLifecycle:
    """Test hook lifecycle methods."""

    def test_hook_init(self) -> None:
        """Hook initializes with None values."""
        hook = GCMonitorHook()
        assert hook._temp_file is None
        assert hook._process is None
        assert hook._pid == 0

    @patch("subprocess.Popen")
    def test_enter_spawns_process(self, mock_popen: MagicMock) -> None:
        """__enter__ spawns gc-monitor subprocess."""
        mock_popen.return_value = MagicMock()

        hook = GCMonitorHook()
        with hook:
            assert hook._process is not None
            assert hook._temp_file is not None
            mock_popen.assert_called_once()

    @patch("subprocess.Popen")
    @patch("os.name", "posix")
    @patch("os.kill")
    def test_exit_sends_sigint(self, mock_kill: MagicMock, mock_popen: MagicMock) -> None:
        """__exit__ sends SIGINT to the subprocess."""
        import signal

        mock_process = MagicMock()
        mock_popen.return_value = mock_process

        hook = GCMonitorHook()
        with hook:
            pass

        # Verify SIGINT was sent
        mock_kill.assert_called_once_with(mock_process.pid, signal.SIGINT)
        mock_process.wait.assert_called()

    @patch("subprocess.Popen")
    @patch("os.name", "nt")
    def test_exit_sends_ctrl_break_on_windows(self, mock_popen: MagicMock) -> None:
        """__exit__ sends CTRL_BREAK_EVENT on Windows."""
        import signal

        mock_process = MagicMock()
        mock_popen.return_value = mock_process

        hook = GCMonitorHook()
        with hook:
            pass

        # Verify CTRL_BREAK_EVENT was sent
        mock_process.send_signal.assert_called_once_with(signal.CTRL_BREAK_EVENT)

    @patch("subprocess.Popen")
    @patch("pathlib.Path.exists", return_value=True)
    @patch("builtins.open", new_callable=mock_open)
    def test_teardown_reads_json(
        self,
        mock_file: MagicMock,
        mock_exists: MagicMock,
        mock_popen: MagicMock,
    ) -> None:
        """teardown() reads temp JSON and injects to metadata."""
        import json

        mock_process = MagicMock()
        mock_popen.return_value = mock_process

        # Mock JSON content
        mock_data = {
            "events": [
                {
                    "gen": 0, "ts": 1, "collections": 1, "collected": 10,
                    "uncollectable": 0, "candidates": 5, "object_visits": 100,
                    "heap_size": 10000, "duration": 0.001, "total_duration": 0.001,
                }
            ]
        }
        mock_file.return_value.__enter__.return_value.read.return_value = json.dumps(mock_data)

        hook = GCMonitorHook()
        with hook:
            pass

        metadata: dict[str, Any] = {}
        hook.teardown(metadata)

        assert "gc_collections_total" in metadata
        assert metadata["gc_collections_total"] == 1

    @patch("subprocess.Popen")
    def test_cli_not_found_error(self, mock_popen: MagicMock) -> None:
        """Error raised when gc-monitor CLI not found."""
        mock_popen.side_effect = FileNotFoundError("gc-monitor")

        hook = GCMonitorHook()
        with pytest.raises(RuntimeError, match="gc-monitor CLI not found"):
            with hook:
                pass
```

**Task 1.2: Implement Hook**

- [ ] Create `src/gc_monitor/pyperf_hook.py`
- [ ] Implement `GCMonitorHook` class with subprocess management
- [ ] Add `gc_monitor_hook()` factory function
- [ ] Run tests, iterate until passing

#### Phase 2: CLI Updates for External Process Mode

**Task 2.1: CLI Must Support Non-Interactive Mode**

```python
# tests/test_cli/test_pyperf_format.py

import pytest
from click.testing import CliRunner
from gc_monitor.cli import main


class TestCliPyperfFormat:
    """Test CLI --format pyperf output."""

    def test_format_pyperf_flag(self) -> None:
        """CLI accepts --format pyperf flag."""
        runner = CliRunner()
        # Test with a mock PID (will fail but should parse args correctly)
        result = runner.invoke(main, ["99999", "-o", "test.json", "--format", "pyperf"])
        # Should not fail on argument parsing
        assert "Usage:" not in result.output or result.exit_code == 0

    def test_duration_flag(self) -> None:
        """CLI accepts -d duration flag."""
        runner = CliRunner()
        result = runner.invoke(main, ["99999", "-d", "5", "-o", "test.json"])
        assert "Usage:" not in result.output or result.exit_code == 0
```

**Task 2.2: Update CLI**

- [ ] Add `-d/--duration` flag to CLI
- [ ] Add `--format` flag (options: `trace`, `pyperf`)
- [ ] Implement non-interactive mode (run for duration, exit cleanly)
- [ ] Write JSON output compatible with hook expectations

#### Phase 3: Aggregation Logic

**Task 3.1: Write Aggregation Tests**

```python
# tests/test_aggregation/test_basic_aggregation.py

import pytest
from gc_monitor.pyperf_hook import _aggregate_gc_stats


class TestAggregation:
    """Test GC stats aggregation from JSON events."""

    def test_empty_events(self) -> None:
        """Empty event list returns empty dict."""
        result = _aggregate_gc_stats([])
        assert result == {}

    def test_single_event(self) -> None:
        """Single event produces correct aggregations."""
        event = {
            "gen": 0, "ts": 1, "collections": 5, "collected": 50, "uncollectable": 2,
            "candidates": 10, "object_visits": 200, "heap_size": 20000,
            "duration": 0.005, "total_duration": 0.005,
        }
        result = _aggregate_gc_stats([event])

        assert result["collections_total"] == 5
        assert result["avg_pause_duration_sec"] == 0.005
        assert result["max_pause_duration_sec"] == 0.005
        assert result["min_pause_duration_sec"] == 0.005
        assert result["max_heap_size"] == 20000
        assert result["event_count"] == 1

    def test_multiple_events_sum(self) -> None:
        """Multiple events: sum is cumulative."""
        events = [
            {
                "gen": 0, "ts": i, "collections": 5, "collected": 50, "uncollectable": 2,
                "candidates": 10, "object_visits": 200, "heap_size": 20000,
                "duration": 0.005, "total_duration": 0.005 * (i + 1),
            }
            for i in range(3)
        ]
        result = _aggregate_gc_stats(events)

        assert result["collections_total"] == 15  # 5 * 3
        assert result["objects_collected_total"] == 150  # 50 * 3
```

**Task 3.2: Implement Aggregation**

- [ ] Implement `_aggregate_gc_stats()` function (works with dict, not dataclass)
- [ ] Run tests, iterate until passing

#### Phase 4: Integration Tests

**Task 4.1: Pyperf Integration**

```python
# tests/test_pyperf_hook/test_integration.py

import subprocess
import sys
import json
import tempfile
from pathlib import Path


class TestPyperfIntegration:
    """Test hook integration with pyperf."""

    def test_hook_entry_point_registered(self) -> None:
        """Verify hook is registered as entry point."""
        import importlib.metadata

        entry_points = importlib.metadata.entry_points()

        # Get pyperf.hook group
        if hasattr(entry_points, 'select'):
            hooks = entry_points.select(group='pyperf.hook')
        else:
            hooks = entry_points.get('pyperf.hook', [])

        hook_names = [ep.name for ep in hooks]
        assert 'gc_monitor' in hook_names

    def test_cli_available(self) -> None:
        """Verify gc-monitor CLI is available."""
        result = subprocess.run(
            ["gc-monitor", "--help"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
```

### 5.3 Type Checking Considerations

**Mypy Configuration:**

```python
# In pyperf_hook.py

from typing import Any, Dict, List, Optional
import subprocess


class GCMonitorHook:
    def __init__(
        self,
        duration: float = 0.0,
        output_dir: Optional[str] = None,
    ) -> None:
        self._duration = duration
        self._output_dir: Optional[Path] = Path(output_dir) if output_dir else None
        self._temp_file: Optional[Path] = None
        self._process: Optional[subprocess.Popen[bytes]] = None
        self._pid: int = 0
        self._start_time: float = 0.0
```

**Pyright Strict Mode:**

Ensure all of the following pass:
- [ ] No `Any` types in public API
- [ ] All function parameters typed
- [ ] All return types specified
- [ ] No implicit `Optional`
- [ ] Proper handling of `None` in aggregations

---

## 6. Dependencies

### 6.1 Pyperf as Optional Dependency

**Rationale:**
- Users may only want CLI/Grafana export without pyperf integration
- Reduces package size for non-benchmark users
- Avoids dependency conflicts

**Implementation:**

```toml
# pyproject.toml

[tool.poetry.group.dev.dependencies]
pytest = "^8.0.0"
pyright = "^1.1.408"
mypy = "^1.1.19"
typing-extensions = "^4.15.0"
pyperf = "^2.10.0"  # For testing

[tool.poetry.extras]
pyperf = ["pyperf"]
```

**Lazy Import Pattern:**

```python
# In pyperf_hook.py - no pyperf import needed!
# Hook is pure Python stdlib + gc_monitor package
```

### 6.2 Version Requirements

| Dependency | Version | Rationale |
|------------|---------|-----------|
| `pyperf` | `>=2.10.0` | Hook API stabilized in 2.10 |
| `python` | `>=3.12` | Existing project requirement |
| `gc-monitor` | `>=0.1.0` | CLI must be installed for hook to work |

**Entry Point Compatibility:**

The `pyperf.hook` entry point group has been stable since pyperf 2.0, but we recommend 2.10+ for bug fixes.

---

## 7. Process Management

### 7.1 Spawning gc-monitor as Background Process

```python
def __enter__(self) -> "GCMonitorHook":
    """Spawn external gc-monitor process."""
    self._pid = os.getpid()

    # Generate unique temp file path
    self._temp_file = Path(tempfile.gettempdir()) / f"gc_monitor_{self._pid}_{int(time.time() * 1000)}.json"

    # Build command
    cmd = [
        "gc-monitor",
        str(self._pid),
        "-o", str(self._temp_file),
        "-d", "0",  # Run until terminated
        "--format", "pyperf",
    ]

    # Spawn with platform-specific flags
    if os.name == "nt":
        # Windows: CREATE_NEW_PROCESS_GROUP for clean termination
        self._process = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )
    else:
        # Unix: start new session for isolation
        self._process = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

    # Ensure gc-monitor attaches before benchmark starts
    time.sleep(0.05)
```

### 7.2 Signal-Based Termination

The hook uses **signal-based termination** for graceful shutdown of the gc-monitor subprocess. This approach is preferred over immediate termination because:

**Why SIGINT is Preferred:**

| Benefit | Description |
|---------|-------------|
| **Clean Shutdown** | Allows gc-monitor to flush final data to JSON file |
| **Signal Handler** | gc-monitor CLI already handles SIGINT via `signal.signal()` |
| **Final Flush** | Ensures all buffered GC events are written before exit |
| **Graceful Cleanup** | Process can close file handles and release resources properly |

**Termination Flow:**

```python
def __exit__(self, ...) -> None:
    """Terminate external process gracefully."""
    if self._process is None:
        return

    try:
        # Step 1: Send SIGINT for graceful shutdown
        if os.name == "nt":
            # Windows: Use CTRL_BREAK_EVENT for console processes
            self._process.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            # Unix: SIGINT
            os.kill(self._process.pid, signal.SIGINT)

        # Step 2: Wait for clean exit (5 second timeout)
        try:
            self._process.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            # Step 3: Fallback to SIGTERM (Unix only)
            if os.name != "nt":
                os.kill(self._process.pid, signal.SIGTERM)
                try:
                    self._process.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    # Step 4: Force kill as last resort
                    os.kill(self._process.pid, signal.SIGKILL)
            else:
                # Windows: direct kill
                self._process.kill()
                self._process.wait(timeout=2.0)

    except Exception:
        # Ignore cleanup errors
        pass
    finally:
        self._process = None
```

### 7.3 Cross-Platform Signal Considerations

| Platform | Primary Signal | Fallback | Notes |
|----------|----------------|----------|-------|
| **Linux/Unix** | `SIGINT` | `SIGTERM` → `SIGKILL` | Native signal support |
| **macOS** | `SIGINT` | `SIGTERM` → `SIGKILL` | Same as Unix |
| **Windows** | `CTRL_BREAK_EVENT` | `kill()` | Console event, not true signal |

**Windows Considerations:**

On Windows, true POSIX signals are not available. The `CTRL_BREAK_EVENT` is used instead:

```python
if os.name == "nt":
    # Windows: CTRL_BREAK_EVENT works for console processes
    # Requires CREATE_NEW_PROCESS_GROUP flag during spawn
    self._process.send_signal(signal.CTRL_BREAK_EVENT)
else:
    # Unix: Standard SIGINT
    os.kill(self._process.pid, signal.SIGINT)
```

**Important:** The `CREATE_NEW_PROCESS_GROUP` flag must be set during process spawning for `CTRL_BREAK_EVENT` to work correctly on Windows.

### 7.4 Temp File Lifecycle Management

```python
def teardown(self, metadata: dict[str, Any]) -> None:
    """Read results and cleanup."""
    if not self._temp_file or not self._temp_file.exists():
        return

    try:
        # Read JSON
        with open(self._temp_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        # Process...
        if "events" in data:
            aggregated = _aggregate_gc_stats(data["events"])
            for key, value in aggregated.items():
                metadata[f"gc_{key}"] = value

    except (json.JSONDecodeError, IOError) as e:
        logger.warning(f"Failed to read GC metrics: {e}")

    finally:
        # Always cleanup temp file
        self._cleanup_temp_file()


def _cleanup_temp_file(self) -> None:
    """Remove temp file."""
    if self._temp_file and self._temp_file.exists():
        try:
            self._temp_file.unlink()
        except OSError:
            pass  # Ignore cleanup errors
```

### 7.5 Cross-Platform Considerations

| Platform | Process Spawning | Termination | Notes |
|----------|------------------|-------------|-------|
| **Windows** | `CREATE_NEW_PROCESS_GROUP` | `send_signal(CTRL_BREAK_EVENT)` | Need process group for clean signal |
| **Linux/Unix** | `start_new_session=True` | `os.kill(pid, SIGINT)` | Session leader for isolation |
| **macOS** | Same as Unix | Same as Unix | No special handling needed |

**Implementation:**

```python
if os.name == "nt":
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP
    start_new_session = False
else:
    creationflags = 0
    start_new_session = True

self._process = subprocess.Popen(
    cmd,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
    creationflags=creationflags,
    start_new_session=start_new_session,
)
```

---

## 8. Example Usage

### 8.1 CLI Command Examples

**Basic Usage:**

```bash
# Run pyperf benchmarks with GC monitoring
pyperf run --hook=gc_monitor my_benchmark.py

# Run with custom duration (gc-monitor runs for 10 seconds)
pyperf run --hook=gc_monitor:duration=10 my_benchmark.py

# Run with custom temp directory
pyperf run --hook=gc_monitor:output_dir=/tmp my_benchmark.py

# Run and save to file
pyperf run --hook=gc_monitor -o benchmark_with_gc.json my_benchmark.py
```

**What Happens Behind the Scenes:**

```bash
# User runs:
pyperf run --hook=gc_monitor my_benchmark.py

# Hook automatically spawns:
gc-monitor <benchmark_pid> -o <temp_file.json> -d 0 --format pyperf

# After benchmark completes:
# 1. Hook sends SIGINT to gc-monitor (CTRL_BREAK_EVENT on Windows)
# 2. gc-monitor handles signal, flushes data, exits cleanly
# 3. Hook reads <temp_file.json>
# 4. Metrics injected to pyperf metadata
# 5. Temp file is deleted
```

### 8.2 Pyperformance Integration

```bash
# Run pyperformance suite with GC monitoring
pyperformance run --hook=gc_monitor -o gc_metrics.json

# Compare two runs with GC metrics
pyperformance compare run1.json run2.json --metadata gc_
```

### 8.3 Expected Output in Pyperf JSON

**Sample Metadata Section:**

```json
{
  "metadata": {
    "benchmark": "my_benchmark",
    "python_version": "3.12.0",
    "platform": "win32",

    "gc_collections_total": 42,
    "gc_objects_collected_total": 1250,
    "gc_objects_uncollectable_total": 0,
    "gc_total_duration_sec": 0.089,

    "gc_avg_pause_duration_sec": 0.00212,
    "gc_max_pause_duration_sec": 0.0156,
    "gc_min_pause_duration_sec": 0.00034,
    "gc_std_pause_duration_sec": 0.00187,

    "gc_avg_heap_size": 1048576,
    "gc_max_heap_size": 2097152,
    "gc_min_heap_size": 524288,

    "gc_avg_object_visits": 450,
    "gc_max_object_visits": 1200,

    "gc_collections_by_gen_0": 35,
    "gc_collections_by_gen_1": 5,
    "gc_collections_by_gen_2": 2,

    "gc_event_count": 42
  },
  "benchmarks": [
    {
      "name": "my_benchmark",
      "runs": [
        {
          "values": [0.0012, 0.0013, 0.0011]
        }
      ]
    }
  ]
}
```

### 8.4 Analyzing GC Metrics

**Extract GC metrics from results:**

```python
import pyperf
import json

# Load benchmark results
suite = pyperf.BenchmarkSuite.load("gc_metrics.json")

# Access GC metadata
for benchmark in suite:
    metadata = benchmark.get_metadata()

    print(f"Benchmark: {benchmark.get_name()}")
    print(f"  GC Collections: {metadata.get('gc_collections_total', 'N/A')}")
    print(f"  Avg Pause: {metadata.get('gc_avg_pause_duration_sec', 'N/A'):.6f}s")
    print(f"  Max Pause: {metadata.get('gc_max_pause_duration_sec', 'N/A'):.6f}s")
    print(f"  Max Heap: {metadata.get('gc_max_heap_size', 'N/A')} bytes")
```

**Compare GC impact between implementations:**

```bash
# Run benchmarks on two implementations
pyperformance run --hook=gc_monitor -o impl_v1.json my_bench.py
pyperformance run --hook=gc_monitor -o impl_v2.json my_bench.py

# Compare with focus on GC metrics
pyperformance compare impl_v1.json impl_v2.json \
  --metadata gc_collections_total \
  --metadata gc_avg_pause_duration_sec \
  --metadata gc_max_heap_size
```

---

## 9. Challenges & Mitigations

### 9.1 Challenge: Process Synchronization

**Problem:**
Ensure gc-monitor starts and attaches before benchmark code begins executing.

**Mitigation:**

```python
def __enter__(self) -> "GCMonitorHook":
    # ... spawn process ...

    # Small delay to ensure gc-monitor attaches
    time.sleep(0.05)  # 50ms

    return self
```

**Trade-offs:**
- Adds 50ms overhead to benchmark startup (acceptable for one-time cost)
- Could be made configurable via `startup_delay` parameter

### 9.2 Challenge: Temp File Race Conditions

**Problem:**
Multiple benchmarks running simultaneously could conflict on temp file names.

**Mitigation:**

```python
# Unique filename per process + timestamp + random suffix
self._temp_file = (
    temp_dir / f"gc_monitor_{self._pid}_{int(time.time() * 1000)}_{os.urandom(4).hex()}.json"
)
```

**Additional Measures:**
- Use `tempfile.mkstemp()` for atomic file creation if needed
- Include PID in filename for easy debugging

### 9.3 Challenge: Cleanup on Benchmark Crashes

**Problem:**
If benchmark crashes, temp file and gc-monitor process may be left orphaned.

**Mitigation:**

```python
def __exit__(self, _exc_type, _exc_value, _traceback) -> None:
    """Always cleanup, even on exception."""
    try:
        if self._process:
            # Send SIGINT for graceful shutdown
            if os.name == "nt":
                self._process.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                os.kill(self._process.pid, signal.SIGINT)
            try:
                self._process.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
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
        pass  # Ignore cleanup errors
    finally:
        self._process = None

def teardown(self, metadata: dict[str, Any]) -> None:
    """Always cleanup temp file."""
    try:
        # ... read and process ...
        pass
    finally:
        self._cleanup_temp_file()  # Always runs
```

### 9.4 Challenge: gc-monitor Process Crashes

**Problem:**
External process may crash during benchmark execution.

**Mitigation:**

```python
def teardown(self, metadata: dict[str, Any]) -> None:
    """Handle missing/corrupt temp file gracefully."""
    if not self._temp_file or not self._temp_file.exists():
        # gc-monitor crashed or never wrote output
        logger.warning("GC metrics file not found - gc-monitor may have crashed")
        return

    try:
        with open(self._temp_file, "r") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        logger.warning(f"Corrupt GC metrics file: {e}")
        return
    except IOError as e:
        logger.warning(f"Failed to read GC metrics: {e}")
        return

    # Process normally...
```

### 9.5 Challenge: Performance Impact of External Process

**Problem:**
External process reading memory could impact benchmark performance.

**Mitigation:**

| Strategy | Implementation | Impact |
|----------|----------------|--------|
| **External process** | No in-process overhead | Zero impact on benchmark memory/threads |
| **Efficient polling** | gc-monitor uses 10ms default rate | Minimal CPU overhead |
| **Direct memory read** | Uses `_gc_monitor` API (same as in-process) | Same overhead as in-process monitoring |
| **Async file writes** | gc-monitor buffers and writes periodically | Reduced I/O during benchmark |

**Key Benefit:**
The external process architecture ensures **zero in-process overhead** - no threads, no imports, no memory pressure in the benchmark process itself.

### 9.6 Challenge: Signal Delivery Reliability

**Problem:**
Signals may not be delivered reliably in all scenarios (e.g., process in uninterruptible sleep, Windows console limitations).

**Mitigation:**

1. **gc-monitor CLI handles SIGINT explicitly:**
   ```python
   # In gc-monitor CLI
   import signal

   def handle_sigint(signum, frame):
       """Handle SIGINT for graceful shutdown."""
       exporter.flush()  # Ensure all data is written
       sys.exit(0)

   signal.signal(signal.SIGINT, handle_sigint)
   ```

2. **Fallback chain ensures termination:**
   - SIGINT → wait 5s → SIGTERM → wait 2s → SIGKILL
   - Windows: CTRL_BREAK_EVENT → wait 5s → kill()

3. **Timeout-based escalation:**
   - Prevents hanging indefinitely if signal is ignored
   - Ensures benchmark completion even with misbehaving subprocess

**Cross-Platform Notes:**
- **Unix:** SIGINT is reliable for normal processes
- **Windows:** `CTRL_BREAK_EVENT` requires `CREATE_NEW_PROCESS_GROUP` and works only for console processes
- **Edge case:** On Windows, if the process has no console, SIGINT may not work; fallback to `kill()` is essential

---

## 10. Implementation Checklist

### Phase 1: Core Implementation
- [ ] Create `src/gc_monitor/pyperf_hook.py`
- [ ] Implement `GCMonitorHook` class with subprocess management
- [ ] Add `gc_monitor_hook()` factory function
- [ ] Write unit tests (TDD) with mocked subprocess
- [ ] Pass mypy/pyright type checking

### Phase 2: CLI Updates
- [ ] Add `-d/--duration` flag to CLI
- [ ] Add `--format` flag (options: `trace`, `pyperf`)
- [ ] Implement non-interactive mode (run for duration, exit cleanly)
- [ ] Write JSON output compatible with hook expectations
- [ ] Test CLI with mock PID

### Phase 3: Aggregation
- [ ] Implement `_aggregate_gc_stats()` function (dict-based)
- [ ] Write aggregation tests
- [ ] Test edge cases (empty, single event, many events)
- [ ] Add per-generation breakdown

### Phase 4: Integration
- [ ] Update `pyproject.toml` with entry point
- [ ] Update `src/gc_monitor/__init__.py` exports
- [ ] Write integration tests
- [ ] Test with actual pyperf installation
- [ ] Test end-to-end with real benchmark

### Phase 5: Documentation
- [ ] Add README section on pyperf integration
- [ ] Create example benchmark script
- [ ] Document GC metrics in metadata
- [ ] Document external process architecture
- [ ] Add troubleshooting section

### Phase 6: Polish
- [ ] Measure and document overhead (should be minimal)
- [ ] Add logging for debugging
- [ ] Create CI test with pyperf
- [ ] Test cross-platform (Windows, Linux, macOS)
- [ ] Release notes

---

## 11. Future Enhancements

### 11.1 Multiple Exporters Simultaneously

Allow combining pyperf hook with file export:

```bash
# Run gc-monitor that writes to file AND pyperf reads results
gc-monitor <pid> -o results.json --format trace &
# Hook could read same file or separate file
```

### 11.2 Real-time Metrics During Benchmark

Export to Grafana **during** benchmark for live monitoring:

```bash
# Run gc-monitor with dual output
gc-monitor <pid> -o results.json --grafana http://localhost:4317
# Hook reads results.json after benchmark
```

### 11.3 GC Event Filtering

Filter which GC events to record (reduce overhead):

```bash
# CLI supports filtering
gc-monitor <pid> -o results.json --min-generation 2 --min-duration 0.001
```

### 11.4 Persistent Temp Files for Debugging

Add option to keep temp files for post-analysis:

```python
GCMonitorHook(cleanup=False)  # Keep temp file after teardown
```

---

## Appendix A: Complete File Templates

### A.1 `src/gc_monitor/pyperf_hook.py`

```python
"""Pyperf hook for GC monitoring via external process."""

import json
import logging
import os
import signal
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Optional


_logger = logging.getLogger(__name__)


class GCMonitorHook:
    """
    Pyperf hook for GC monitoring via external gc-monitor process.

    The hook spawns an external `gc-monitor` CLI process that reads the
    benchmark process memory directly. Results are written to a temp JSON
    file, which the hook reads and injects into pyperf metadata.
    """

    def __init__(
        self,
        duration: float = 0.0,
        output_dir: Optional[str] = None,
    ) -> None:
        """
        Initialize the hook.

        Args:
            duration: Monitoring duration in seconds (0 = until benchmark ends)
            output_dir: Directory for temp files (default: system temp)
        """
        self._duration = duration
        self._output_dir = Path(output_dir) if output_dir else None
        self._temp_file: Optional[Path] = None
        self._process: Optional[subprocess.Popen[bytes]] = None
        self._pid: int = 0
        self._start_time: float = 0.0

    def __enter__(self) -> "GCMonitorHook":
        """Spawn external gc-monitor process."""
        self._pid = os.getpid()
        self._start_time = time.monotonic()

        # Generate temp file path
        temp_dir = self._output_dir or Path(tempfile.gettempdir())
        self._temp_file = temp_dir / f"gc_monitor_{self._pid}_{int(time.time() * 1000)}.json"

        # Build CLI command
        cmd = self._build_command()

        # Spawn external gc-monitor process
        try:
            if os.name == "nt":
                self._process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
                )
            else:
                self._process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
        except FileNotFoundError as e:
            raise RuntimeError(
                "gc-monitor CLI not found. Ensure gc-monitor is installed: "
                "pip install gc-monitor"
            ) from e

        # Ensure gc-monitor attaches before benchmark starts
        time.sleep(0.05)

        return self

    def __exit__(
        self,
        _exc_type: Optional[type[BaseException]],
        _exc_value: Optional[BaseException],
        _traceback: Optional[object],
    ) -> None:
        """Send SIGINT to external process for graceful shutdown."""
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
            pass
        finally:
            self._process = None

    def teardown(self, metadata: Dict[str, Any]) -> None:
        """Read results and inject to metadata."""
        if not self._temp_file or not self._temp_file.exists():
            _logger.warning("GC metrics file not found")
            return

        try:
            with open(self._temp_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            if "events" in data:
                aggregated = _aggregate_gc_stats(data["events"])
                for key, value in aggregated.items():
                    metadata[f"gc_{key}"] = value

        except (json.JSONDecodeError, IOError) as e:
            _logger.warning(f"Failed to read GC metrics: {e}")

        finally:
            self._cleanup_temp_file()

    def _build_command(self) -> list[str]:
        """Build gc-monitor CLI command."""
        cmd = [
            "gc-monitor",
            str(self._pid),
            "-o", str(self._temp_file),
        ]

        if self._duration > 0:
            cmd.extend(["-d", str(self._duration)])
        else:
            cmd.extend(["-d", "0"])

        cmd.extend(["--format", "pyperf"])
        return cmd

    def _cleanup_temp_file(self) -> None:
        """Remove temp file."""
        if self._temp_file and self._temp_file.exists():
            try:
                self._temp_file.unlink()
            except OSError:
                pass


def _aggregate_gc_stats(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate GC statistics from JSON events."""
    if not events:
        return {}

    import statistics

    by_gen: dict[int, list[dict[str, Any]]] = {0: [], 1: [], 2: []}
    durations: list[float] = []
    heap_sizes: list[int] = []
    object_visits: list[int] = []
    total_collections = 0
    total_collected = 0
    total_uncollectable = 0
    total_duration = 0.0

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
    result: dict[str, Any] = {}

    for gen, gen_events in by_gen.items():
        result[f"collections_by_gen_{gen}"] = sum(e.get("collections", 0) for e in gen_events)

    result.update({
        "collections_total": total_collections,
        "objects_collected_total": total_collected,
        "objects_uncollectable_total": total_uncollectable,
        "total_duration_sec": total_duration,
        "avg_pause_duration_sec": statistics.mean(durations) if durations else 0.0,
        "avg_heap_size": statistics.mean(heap_sizes) if heap_sizes else 0,
        "avg_object_visits": statistics.mean(object_visits) if object_visits else 0,
        "max_pause_duration_sec": max(durations) if durations else 0.0,
        "max_heap_size": max(heap_sizes) if heap_sizes else 0,
        "max_object_visits": max(object_visits) if object_visits else 0,
        "min_pause_duration_sec": min(durations) if durations else 0.0,
        "min_heap_size": min(heap_sizes) if heap_sizes else 0,
        "std_pause_duration_sec": statistics.stdev(durations) if len(durations) > 1 else 0.0,
        "event_count": event_count,
    })

    return result


def gc_monitor_hook(
    duration: float = 0.0,
    output_dir: Optional[str] = None,
) -> GCMonitorHook:
    """Factory function for pyperf entry point."""
    return GCMonitorHook(duration=duration, output_dir=output_dir)
```

### A.2 Updated `pyproject.toml` Entry Points Section

```toml
[project.entry-points."pyperf.hook"]
gc_monitor = "gc_monitor.pyperf_hook:gc_monitor_hook"
```

---

**Document Version:** 2.1 (SIGINT-Based Termination)
**Last Updated:** 2026-03-21
**Author:** Architecture Review
