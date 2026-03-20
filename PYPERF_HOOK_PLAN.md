# Pyperf Hook Integration Plan

## 1. Architecture Overview

### 1.1 GCMonitorHook in Pyperf Lifecycle

The `GCMonitorHook` integrates gc-monitor into the pyperf benchmarking framework as a **hook** that automatically monitors garbage collection during benchmark execution.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        Pyperf Benchmark Runner                              │
│                                                                             │
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │                    Benchmark Execution Flow                           │  │
│  │                                                                       │  │
│  │  ┌─────────────┐                                                     │  │
│  │  │ Hook Init   │  ← GCMonitorHook.__init__()                         │  │
│  │  │ (once)      │     - Initialize in-memory collector                │  │
│  │  └──────┬──────┘     - Configure sampling rate                       │  │
│  │         │                                                         │  │
│  │         ▼                                                         │  │
│  │  ┌─────────────────────────────────────────────────────────────┐    │  │
│  │  │  For Each Benchmark Run:                                    │    │  │
│  │  │                                                             │    │  │
│  │  │  ┌──────────────┐                                          │    │  │
│  │  │  │ __enter__()  │  ← Start GC monitoring                    │    │  │
│  │  │  │              │     - Connect to gc_monitor handler       │    │  │
│  │  │  │              │     - Start background polling thread     │    │  │
│  │  │  └──────┬───────┘                                          │    │  │
│  │  │         │                                                   │    │  │
│  │  │         ▼                                                   │    │  │
│  │  │  ┌──────────────┐                                          │    │  │
│  │  │  │ BENCHMARK    │  ← GC events collected in-memory          │    │  │
│  │  │  │ CODE         │                                          │    │  │
│  │  │  └──────┬───────┘                                          │    │  │
│  │  │         │                                                   │    │  │
│  │  │         ▼                                                   │    │  │
│  │  │  ┌──────────────┐                                          │    │  │
│  │  │  │ __exit__()   │  ← Stop GC monitoring                     │    │  │
│  │  │  │              │     - Stop polling thread                 │    │  │
│  │  │  │              │     - Disconnect handler                  │    │  │
│  │  │  └──────────────┘                                          │    │  │
│  │  │                                                             │    │  │
│  │  └─────────────────────────────────────────────────────────────┘    │  │
│  │                                                                       │  │
│  │         ▼ (after all benchmark runs complete)                        │  │
│  │  ┌─────────────┐                                                     │  │
│  │  │ teardown()  │  ← Aggregate and inject metrics                    │  │
│  │  │             │     - Compute aggregates (sum, avg, max, min)       │  │
│  │  │             │     - Add to pyperf metadata dict                   │  │
│  │  └─────────────┘                                                     │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 1.2 Data Flow

```
GC Events → GCMonitorHandler → GCMonitor (polling) → InMemoryCollector → Aggregation → Pyperf Metadata
     │                                                                      │
     │                                                                      ▼
     │                                                      { "gc_collections_total": 42,
     │                                                        "gc_avg_pause_duration": 0.0023,
     │                                                        "gc_max_heap_size": 1048576,
     │                                                        ... }
     │
     └─→ Python Runtime (gc callbacks)
```

### 1.3 Relationship to Existing Exporters

| Exporter | Purpose | Output | Lifecycle |
|----------|---------|--------|-----------|
| `TraceExporter` | Chrome DevTools visualization | JSON file | Manual start/stop |
| `GrafanaExporter` | Real-time monitoring dashboards | OTLP metrics | Continuous streaming |
| `InMemoryCollector` (new) | Pyperf benchmark metadata | In-memory dict | Per-benchmark run |

**Key Differences:**
- **InMemoryCollector** does NOT write to files or network
- Collects events only during benchmark execution window
- Aggregates statistics and injects into pyperf metadata
- Minimal overhead (no I/O during benchmark)

---

## 2. Hook Implementation Design

### 2.1 GCMonitorHook Class Structure

```python
"""Pyperf hook for GC monitoring."""

import threading
from typing import Any, Dict, List, Optional

from ._gc_monitor import GCMonitorStatsItem, connect as _connect, disconnect as _disconnect
from .exporter import GCMonitorExporter


class InMemoryCollector(GCMonitorExporter):
    """
    In-memory collector for pyperf hook.
    
    Collects GC events during benchmark execution without I/O overhead.
    """
    
    def __init__(self, pid: int) -> None:
        super().__init__(pid, thread_name="GC Monitor")
        self._events: List[GCMonitorStatsItem] = []
        self._lock = threading.Lock()
    
    def add_event(self, stats_item: GCMonitorStatsItem) -> None:
        """Thread-safe event collection."""
        with self._lock:
            self._events.append(stats_item)
    
    def close(self) -> None:
        """No-op for in-memory collector."""
        pass
    
    def get_events(self) -> List[GCMonitorStatsItem]:
        """Return all collected events."""
        with self._lock:
            return list(self._events)
    
    def clear(self) -> None:
        """Clear collected events."""
        with self._lock:
            self._events.clear()


class GCMonitorHook:
    """
    Pyperf hook for GC monitoring.
    
    Usage:
        # Via CLI
        pyperf run --hook=gc_monitor ...
        
        # Via entry point registration in pyproject.toml
        [project.entry-points."pyperf.hook"]
        gc_monitor = "gc_monitor.pyperf_hook:gc_monitor_hook"
    """
    
    def __init__(self) -> None:
        """Initialize the hook (called once per process)."""
        self._collector: Optional[InMemoryCollector] = None
        self._monitor: Optional[Any] = None  # GCMonitor instance
        self._handler: Optional[Any] = None  # GCMonitorHandler instance
        self._pid: int = 0
    
    def __enter__(self) -> "GCMonitorHook":
        """
        Called immediately before running benchmark code.
        
        Starts GC monitoring for the current process.
        """
        import os
        self._pid = os.getpid()
        self._collector = InMemoryCollector(self._pid)
        
        # Connect to GC monitor
        self._handler = _connect(self._pid)
        from .core import GCMonitor
        self._monitor = GCMonitor(self._handler, self._collector, rate=0.01)  # 10ms polling
        
        return self
    
    def __exit__(
        self,
        _exc_type: Optional[type[BaseException]],
        _exc_value: Optional[BaseException],
        _traceback: Optional[object],
    ) -> None:
        """
        Called immediately after running benchmark code.
        
        Stops GC monitoring. Exceptions from benchmark are ignored
        (we still want to collect GC stats even if benchmark fails).
        """
        if self._monitor:
            self._monitor.stop()
            self._monitor = None
        
        if self._handler:
            _disconnect(self._handler)
            self._handler = None
    
    def teardown(self, metadata: Dict[str, Any]) -> None:
        """
        Called when the hook is completed for a process.
        
        Aggregates collected GC statistics and adds them to pyperf metadata.
        
        Args:
            metadata: Pyperf metadata dictionary (modified in-place)
        """
        if not self._collector:
            return
        
        events = self._collector.get_events()
        if not events:
            return
        
        # Aggregate statistics
        aggregated = _aggregate_gc_stats(events)
        
        # Add to metadata with gc_ prefix
        for key, value in aggregated.items():
            metadata[f"gc_{key}"] = value
        
        # Clear collector for next benchmark
        self._collector.clear()


def _aggregate_gc_stats(events: List[GCMonitorStatsItem]) -> Dict[str, Any]:
    """
    Aggregate GC statistics from collected events.
    
    Returns a dictionary of aggregated metrics ready for pyperf metadata.
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
    
    for event in events:
        total_collections += event.collections
        total_collected += event.collected
        total_uncollectable += event.uncollectable
        total_duration += event.duration
        durations.append(event.duration)
        heap_sizes.append(event.heap_size)
        object_visits.append(event.object_visits)
    
    event_count = len(events)
    
    return {
        # Cumulative metrics
        "collections_total": total_collections,
        "objects_collected_total": total_collected,
        "objects_uncollectable_total": total_uncollectable,
        "total_duration_sec": total_duration,
        
        # Average metrics
        "avg_pause_duration_sec": total_duration / event_count,
        "avg_heap_size": sum(heap_sizes) // event_count,
        "avg_object_visits": sum(object_visits) // event_count,
        
        # Max metrics
        "max_pause_duration_sec": max(durations),
        "max_heap_size": max(heap_sizes),
        "max_object_visits": max(object_visits),
        
        # Min metrics
        "min_pause_duration_sec": min(durations),
        "min_heap_size": min(heap_sizes),
        
        # Event count
        "event_count": event_count,
    }


# Entry point factory function
def gc_monitor_hook() -> GCMonitorHook:
    """Factory function for pyperf entry point."""
    return GCMonitorHook()
```

### 2.2 Exporter Choice: InMemoryCollector

**Rationale:**
- **No I/O during benchmark**: File writes or network calls would add noise to benchmark results
- **Minimal memory footprint**: Only stores GC event metadata (small structs)
- **Thread-safe**: Uses locks to protect shared state
- **Compatible interface**: Implements `GCMonitorExporter` base class

### 2.3 Monitoring Lifecycle

| Phase | Action | Timing |
|-------|--------|--------|
| `__init__` | Create hook instance | Once per pyperf process |
| `__enter__` | Start monitoring | Before each benchmark run |
| Benchmark | Collect GC events | During benchmark execution |
| `__exit__` | Stop monitoring | After each benchmark run |
| `teardown` | Aggregate & inject | Once after all benchmarks complete |

### 2.4 Aggregation Strategy

Events are aggregated **per benchmark run** (between `__enter__` and `__exit__`):

```python
# Per-benchmark aggregation (in teardown)
{
    "gc_collections_total": 15,           # Sum
    "gc_avg_pause_duration_sec": 0.0023,  # Average
    "gc_max_pause_duration_sec": 0.015,   # Maximum
    "gc_min_pause_duration_sec": 0.0001,  # Minimum
    "gc_max_heap_size": 2097152,          # Maximum
    ...
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

### 3.2 Package Structure

```
src/gc_monitor/
├── __init__.py              # Updated exports
├── _gc_monitor.py           # GCMonitorStatsItem, GCMonitorHandler (unchanged)
├── exporter.py              # GCMonitorExporter base class (unchanged)
├── chrome_trace_exporter.py # TraceExporter (unchanged)
├── core.py                  # GCMonitor, connect() (unchanged)
├── cli.py                   # CLI (unchanged)
├── pyperf_hook.py           # NEW: GCMonitorHook, InMemoryCollector
└── _aggregation.py          # NEW: _aggregate_gc_stats (internal)

tests/
├── test_chrome_trace.py     # Existing
├── test_cli.py              # Existing
├── test_pyperf_hook.py      # NEW: Hook tests
└── test_aggregation.py      # NEW: Aggregation tests
```

### 3.3 Updated __init__.py

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

### 4.2 Aggregation Strategy

```python
def _aggregate_gc_stats(events: List[GCMonitorStatsItem]) -> Dict[str, Any]:
    """Aggregate GC statistics with multiple strategies."""
    
    # Group by generation
    by_gen: Dict[int, List[GCMonitorStatsItem]] = {0: [], 1: [], 2: []}
    for event in events:
        by_gen[event.gen].append(event)
    
    # Per-generation collection counts
    result: Dict[str, Any] = {}
    for gen, gen_events in by_gen.items():
        result[f"collections_by_gen_{gen}"] = sum(e.collections for e in gen_events)
    
    # Overall aggregations
    durations = [e.duration for e in events]
    heap_sizes = [e.heap_size for e in events]
    
    result.update({
        # Sums (cumulative)
        "collections_total": sum(e.collections for e in events),
        "objects_collected_total": sum(e.collected for e in events),
        "objects_uncollectable_total": sum(e.uncollectable for e in events),
        "total_duration_sec": sum(durations),
        
        # Averages
        "avg_pause_duration_sec": statistics.mean(durations) if durations else 0.0,
        "avg_heap_size": statistics.mean(heap_sizes) if heap_sizes else 0,
        "avg_object_visits": statistics.mean(e.object_visits for e in events),
        
        # Maximums (worst-case)
        "max_pause_duration_sec": max(durations) if durations else 0.0,
        "max_heap_size": max(heap_sizes) if heap_sizes else 0,
        "max_object_visits": max(e.object_visits for e in events),
        
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

### 4.3 Metadata Key Naming Convention

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
│   ├── test_hook_lifecycle.py      # __enter__, __exit__, teardown
│   ├── test_in_memory_collector.py # Collector thread safety, API
│   └── test_integration.py         # End-to-end with pyperf
├── test_aggregation/
│   ├── __init__.py
│   ├── test_basic_aggregation.py   # Sum, avg, max, min
│   ├── test_edge_cases.py          # Empty events, single event
│   └── test_generation_breakdown.py # Per-gen aggregations
└── test_metadata/
    ├── __init__.py
    └── test_metadata_injection.py  # Verify metadata dict updates
```

### 5.2 Implementation Phases

#### Phase 1: Core Hook Implementation (TDD)

**Task 1.1: Write Tests First**

```python
# tests/test_pyperf_hook/test_hook_lifecycle.py

import pytest
from gc_monitor.pyperf_hook import GCMonitorHook, InMemoryCollector


class TestGCMonitorHookLifecycle:
    """Test hook lifecycle methods."""
    
    def test_hook_init(self) -> None:
        """Hook initializes with None values."""
        hook = GCMonitorHook()
        assert hook._collector is None
        assert hook._monitor is None
        assert hook._handler is None
    
    def test_enter_starts_monitoring(self) -> None:
        """__enter__ initializes collector and starts monitoring."""
        hook = GCMonitorHook()
        with hook:
            assert hook._collector is not None
            assert hook._monitor is not None
            assert hook._handler is not None
    
    def test_exit_stops_monitoring(self) -> None:
        """__exit__ stops monitoring and cleans up."""
        hook = GCMonitorHook()
        with hook:
            monitor = hook._monitor
            handler = hook._handler
        
        # After __exit__, monitor should be stopped
        assert not monitor._running  # type: ignore[attr-defined]
    
    def test_teardown_adds_metadata(self) -> None:
        """teardown() aggregates stats and adds to metadata dict."""
        hook = GCMonitorHook()
        with hook:
            # Simulate GC events
            from gc_monitor._gc_monitor import GCMonitorStatsItem
            event = GCMonitorStatsItem(
                gen=0, ts=1, collections=1, collected=10, uncollectable=0,
                candidates=5, object_visits=100, objects_transitively_reachable=50,
                objects_not_transitively_reachable=50, heap_size=10000,
                work_to_do=10, duration=0.001, total_duration=0.001,
            )
            hook._collector.add_event(event)  # type: ignore[union-attr]
        
        metadata: Dict[str, Any] = {}
        hook.teardown(metadata)
        
        assert "gc_collections_total" in metadata
        assert metadata["gc_collections_total"] == 1
        assert "gc_avg_pause_duration_sec" in metadata
    
    def test_teardown_empty_events(self) -> None:
        """teardown() handles empty event list gracefully."""
        hook = GCMonitorHook()
        with hook:
            pass  # No events collected
        
        metadata: Dict[str, Any] = {}
        hook.teardown(metadata)
        
        # Should not add any keys if no events
        assert metadata == {}
    
    def test_context_manager_protocol(self) -> None:
        """Hook works as context manager."""
        with GCMonitorHook() as hook:
            assert hook._collector is not None
```

**Task 1.2: Implement Hook**

- [ ] Create `src/gc_monitor/pyperf_hook.py`
- [ ] Implement `InMemoryCollector` class
- [ ] Implement `GCMonitorHook` class
- [ ] Add `gc_monitor_hook()` factory function
- [ ] Run tests, iterate until passing

#### Phase 2: Aggregation Logic

**Task 2.1: Write Aggregation Tests**

```python
# tests/test_aggregation/test_basic_aggregation.py

import pytest
from gc_monitor.pyperf_hook import _aggregate_gc_stats
from gc_monitor._gc_monitor import GCMonitorStatsItem


class TestAggregation:
    """Test GC stats aggregation."""
    
    def test_empty_events(self) -> None:
        """Empty event list returns empty dict."""
        result = _aggregate_gc_stats([])
        assert result == {}
    
    def test_single_event(self) -> None:
        """Single event produces correct aggregations."""
        event = GCMonitorStatsItem(
            gen=0, ts=1, collections=5, collected=50, uncollectable=2,
            candidates=10, object_visits=200, objects_transitively_reachable=100,
            objects_not_transitively_reachable=100, heap_size=20000,
            work_to_do=20, duration=0.005, total_duration=0.005,
        )
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
            GCMonitorStatsItem(
                gen=0, ts=i, collections=5, collected=50, uncollectable=2,
                candidates=10, object_visits=200, objects_transitively_reachable=100,
                objects_not_transitively_reachable=100, heap_size=20000,
                work_to_do=20, duration=0.005, total_duration=0.005 * (i + 1),
            )
            for i in range(3)
        ]
        result = _aggregate_gc_stats(events)
        
        assert result["collections_total"] == 15  # 5 * 3
        assert result["objects_collected_total"] == 150  # 50 * 3
    
    def test_multiple_events_average(self) -> None:
        """Multiple events: average is correct."""
        events = [
            GCMonitorStatsItem(
                gen=0, ts=i, collections=5, collected=50, uncollectable=2,
                candidates=10, object_visits=200, objects_transitively_reachable=100,
                objects_not_transitively_reachable=100, heap_size=20000 + i * 1000,
                work_to_do=20, duration=0.005 + i * 0.001, total_duration=0.005,
            )
            for i in range(3)
        ]
        result = _aggregate_gc_stats(events)
        
        # Avg heap: (20000 + 21000 + 22000) / 3 = 21000
        assert result["avg_heap_size"] == 21000
        # Avg duration: (0.005 + 0.006 + 0.007) / 3 = 0.006
        assert result["avg_pause_duration_sec"] == pytest.approx(0.006)
    
    def test_multiple_events_max_min(self) -> None:
        """Multiple events: max and min are correct."""
        events = [
            GCMonitorStatsItem(
                gen=0, ts=i, collections=5, collected=50, uncollectable=2,
                candidates=10, object_visits=200 + i * 50,
                objects_transitively_reachable=100, objects_not_transitively_reachable=100,
                heap_size=20000 + i * 5000, work_to_do=20,
                duration=0.005 + i * 0.002, total_duration=0.005,
            )
            for i in range(3)
        ]
        result = _aggregate_gc_stats(events)
        
        assert result["max_heap_size"] == 30000  # 20000 + 2*5000
        assert result["min_heap_size"] == 20000
        assert result["max_pause_duration_sec"] == pytest.approx(0.009)  # 0.005 + 2*0.002
        assert result["min_pause_duration_sec"] == pytest.approx(0.005)
```

**Task 2.2: Implement Aggregation**

- [ ] Create `src/gc_monitor/_aggregation.py` (or inline in `pyperf_hook.py`)
- [ ] Implement `_aggregate_gc_stats()` function
- [ ] Run tests, iterate until passing

#### Phase 3: Integration Tests

**Task 3.1: Pyperf Integration**

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
            # Python 3.10+
            hooks = entry_points.select(group='pyperf.hook')
        else:
            # Python 3.9 compatibility
            hooks = entry_points.get('pyperf.hook', [])
        
        hook_names = [ep.name for ep in hooks]
        assert 'gc_monitor' in hook_names
    
    def test_hook_loadable(self) -> None:
        """Verify hook can be loaded via entry point."""
        import importlib.metadata
        
        entry_points = importlib.metadata.entry_points()
        if hasattr(entry_points, 'select'):
            hooks = entry_points.select(group='pyperf.hook')
        else:
            hooks = entry_points.get('pyperf.hook', [])
        
        gc_monitor_ep = next(ep for ep in hooks if ep.name == 'gc_monitor')
        hook_factory = gc_monitor_ep.load()
        
        # Call factory
        hook = hook_factory()
        from gc_monitor.pyperf_hook import GCMonitorHook
        assert isinstance(hook, GCMonitorHook)
```

### 5.3 Type Checking Considerations

**Mypy Configuration:**

The existing strict type checking should be maintained. Key considerations:

```python
# In pyperf_hook.py

from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .core import GCMonitor  # Avoid circular import
    from ._gc_monitor import GCMonitorHandler

# Use explicit types for attributes
class GCMonitorHook:
    def __init__(self) -> None:
        self._collector: Optional[InMemoryCollector] = None
        self._monitor: Optional["GCMonitor"] = None
        self._handler: Optional["GCMonitorHandler"] = None
        self._pid: int = 0
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
mypy = "^1.19.1"
typing-extensions = "^4.15.0"
pyperf = "^2.10.0"  # For testing

[tool.poetry.extras]
pyperf = ["pyperf"]
```

**Lazy Import Pattern:**

```python
# In pyperf_hook.py

def __enter__(self) -> "GCMonitorHook":
    """Start monitoring."""
    # Lazy import to avoid requiring pyperf at runtime
    import os
    from ._gc_monitor import connect as _connect  # noqa: PLC0415
    
    self._pid = os.getpid()
    # ... rest of implementation
```

### 6.2 Version Requirements

| Dependency | Version | Rationale |
|------------|---------|-----------|
| `pyperf` | `>=2.10.0` | Hook API stabilized in 2.10 |
| `python` | `>=3.12` | Existing project requirement |

**Entry Point Compatibility:**

The `pyperf.hook` entry point group has been stable since pyperf 2.0, but we recommend 2.10+ for bug fixes.

---

## 7. Example Usage

### 7.1 CLI Command Examples

**Basic Usage:**

```bash
# Run pyperf benchmarks with GC monitoring
pyperf run --hook=gc_monitor my_benchmark.py

# Run with multiple hooks (GC + perf_record)
pyperf run --hook=gc_monitor --hook=perf_record my_benchmark.py

# Run and save to file
pyperf run --hook=gc_monitor -o benchmark_with_gc.json my_benchmark.py
```

**Pyperformance Integration:**

```bash
# Run pyperformance suite with GC monitoring
pyperformance run --hook=gc_monitor -o gc_metrics.json

# Compare two runs with GC metrics
pyperformance compare run1.json run2.json --metadata gc_
```

### 7.2 Expected Output in Pyperf JSON

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

### 7.3 Analyzing GC Metrics

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

## 8. Challenges & Mitigations

### 8.1 Challenge: Monitoring Overhead Impact on Benchmarks

**Problem:**
GC monitoring adds overhead (polling thread, event collection) that could skew benchmark results.

**Mitigation Strategies:**

| Strategy | Implementation | Trade-off |
|----------|----------------|-----------|
| **High polling rate** | Use 10ms default (adjustable) | More accurate capture vs. more overhead |
| **Lazy initialization** | Only start monitoring when benchmark runs | Reduces setup/teardown noise |
| **Document overhead** | Add disclaimer in README | Users understand limitations |
| **Optional disable** | Allow `--hook=gc_monitor:rate=0.1` | Flexibility for precision needs |

**Implementation:**

```python
class GCMonitorHook:
    def __init__(
        self,
        rate: float = 0.01,  # 10ms polling (configurable via entry point args)
    ) -> None:
        self._rate = rate
        # ... rest of init
```

**Overhead Measurement:**

Add a benchmark to measure hook overhead:

```python
# benchmarks/hook_overhead.py
import pyperf

def benchmark_without_hook():
    # Empty benchmark
    pass

def benchmark_with_hook():
    # Same empty benchmark, compare metadata
    pass

if __name__ == "__main__":
    runner = pyperf.Runner()
    runner.bench_func("empty", benchmark_without_hook)
```

### 8.2 Challenge: Correlation of GC Events with Benchmark Iterations

**Problem:**
GC events are collected continuously, but pyperf runs multiple iterations. Hard to attribute GC to specific iterations.

**Mitigation:**

```python
class GCMonitorHook:
    def __init__(self) -> None:
        self._per_run_collectors: Dict[int, InMemoryCollector] = {}
        self._current_run: int = 0
    
    def __enter__(self) -> "GCMonitorHook":
        self._current_run += 1
        self._collector = InMemoryCollector(self._pid)
        self._per_run_collectors[self._current_run] = self._collector
        # ... start monitoring
        return self
    
    def teardown(self, metadata: Dict[str, Any]) -> None:
        # Aggregate per-run stats
        for run_id, collector in self._per_run_collectors.items():
            events = collector.get_events()
            run_stats = _aggregate_gc_stats(events)
            
            # Add per-run keys: gc_run_1_collections_total, etc.
            for key, value in run_stats.items():
                metadata[f"gc_run_{run_id}_{key}"] = value
        
        # Also add overall stats
        all_events = []
        for collector in self._per_run_collectors.values():
            all_events.extend(collector.get_events())
        
        overall_stats = _aggregate_gc_stats(all_events)
        for key, value in overall_stats.items():
            metadata[f"gc_{key}"] = value
```

### 8.3 Challenge: Thread Safety

**Problem:**
GC monitoring runs in a background thread while benchmark runs in main thread. Race conditions possible.

**Mitigation:**

```python
class InMemoryCollector(GCMonitorExporter):
    """Thread-safe in-memory collector."""
    
    def __init__(self, pid: int) -> None:
        super().__init__(pid, thread_name="GC Monitor")
        self._events: List[GCMonitorStatsItem] = []
        self._lock = threading.Lock()  # Protects _events
    
    def add_event(self, stats_item: GCMonitorStatsItem) -> None:
        """Thread-safe event collection."""
        with self._lock:
            self._events.append(stats_item)
    
    def get_events(self) -> List[GCMonitorStatsItem]:
        """Thread-safe event retrieval (returns copy)."""
        with self._lock:
            return list(self._events)  # Return copy to avoid race conditions
    
    def clear(self) -> None:
        """Thread-safe clear."""
        with self._lock:
            self._events.clear()
```

**Additional Thread Safety Measures:**

- [ ] Use `threading.Lock()` for all shared state
- [ ] Return copies of collections (not references)
- [ ] Handle exceptions in background thread gracefully
- [ ] Ensure clean shutdown in `__exit__`

```python
def __exit__(self, ...) -> None:
    """Ensure clean shutdown even on exceptions."""
    try:
        if self._monitor:
            self._monitor.stop()
    except Exception as e:
        # Log but don't propagate - benchmark exception is more important
        import logging
        logging.getLogger("gc_monitor").warning(
            f"Error stopping GC monitor: {e}"
        )
    finally:
        self._monitor = None
        self._handler = None
```

### 8.4 Challenge: Entry Point Argument Parsing

**Problem:**
Pyperf supports hook arguments (e.g., `--hook=gc_monitor:rate=0.1`), but entry points don't natively support constructor arguments.

**Mitigation:**

Check pyperf's hook loading mechanism. If arguments are supported:

```python
# Entry point can return a factory that accepts args
def gc_monitor_hook(rate: float = 0.01) -> GCMonitorHook:
    """Factory function supporting optional arguments."""
    return GCMonitorHook(rate=rate)
```

If not supported, document environment variable configuration:

```python
import os

class GCMonitorHook:
    def __init__(self) -> None:
        self._rate = float(os.environ.get("GC_MONITOR_RATE", "0.01"))
```

---

## 9. Implementation Checklist

### Phase 1: Core Implementation
- [ ] Create `src/gc_monitor/pyperf_hook.py`
- [ ] Implement `InMemoryCollector` class
- [ ] Implement `GCMonitorHook` class
- [ ] Add `gc_monitor_hook()` factory function
- [ ] Write unit tests (TDD)
- [ ] Pass mypy/pyright type checking

### Phase 2: Aggregation
- [ ] Implement `_aggregate_gc_stats()` function
- [ ] Write aggregation tests
- [ ] Test edge cases (empty, single event, many events)
- [ ] Add per-generation breakdown

### Phase 3: Integration
- [ ] Update `pyproject.toml` with entry point
- [ ] Update `src/gc_monitor/__init__.py` exports
- [ ] Write integration tests
- [ ] Test with actual pyperf installation

### Phase 4: Documentation
- [ ] Add README section on pyperf integration
- [ ] Create example benchmark script
- [ ] Document GC metrics in metadata
- [ ] Add troubleshooting section

### Phase 5: Polish
- [ ] Measure and document overhead
- [ ] Add logging for debugging
- [ ] Create CI test with pyperf
- [ ] Release notes

---

## 10. Future Enhancements

### 10.1 Multiple Exporters Simultaneously

Allow combining pyperf hook with file export:

```python
class MultiExporter(GCMonitorExporter):
    """Delegate to multiple exporters."""
    
    def __init__(self, *exporters: GCMonitorExporter) -> None:
        self._exporters = exporters
    
    def add_event(self, stats_item: GCMonitorStatsItem) -> None:
        for exporter in self._exporters:
            exporter.add_event(stats_item)
    
    def close(self) -> None:
        for exporter in self._exporters:
            exporter.close()
```

### 10.2 Real-time Metrics During Benchmark

Export to Grafana **during** benchmark for live monitoring:

```python
class GCMonitorHook:
    def __init__(
        self,
        grafana_endpoint: Optional[str] = None,  # Enable via env or arg
    ) -> None:
        self._grafana_endpoint = grafana_endpoint
    
    def __enter__(self) -> "GCMonitorHook":
        # ... existing setup
        
        # Optional: also export to Grafana
        if self._grafana_endpoint:
            from .grafana_exporter import GrafanaExporter
            self._grafana_exporter = GrafanaExporter(self._pid, self._grafana_endpoint)
            self._monitor._exporter = MultiExporter(
                self._collector, self._grafana_exporter
            )
        
        return self
```

### 10.3 GC Event Filtering

Filter which GC events to record (reduce overhead):

```python
class GCMonitorHook:
    def __init__(
        self,
        min_generation: int = 0,  # Only record Gen 2 collections
        min_duration: float = 0.001,  # Only record pauses > 1ms
    ) -> None:
        self._min_generation = min_generation
        self._min_duration = min_duration
    
    def __enter__(self) -> "GCMonitorHook":
        # ... setup
        self._collector = FilteredCollector(
            self._pid,
            min_generation=self._min_generation,
            min_duration=self._min_duration,
        )
        # ...
```

---

## Appendix A: Complete File Templates

### A.1 `src/gc_monitor/pyperf_hook.py`

```python
"""Pyperf hook for GC monitoring."""

import logging
import os
import threading
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from ._gc_monitor import GCMonitorStatsItem, connect as _connect, disconnect as _disconnect
from .exporter import GCMonitorExporter

if TYPE_CHECKING:
    from .core import GCMonitor


_logger = logging.getLogger(__name__)


class InMemoryCollector(GCMonitorExporter):
    """
    Thread-safe in-memory collector for pyperf hook.
    
    Collects GC events during benchmark execution without I/O overhead.
    """
    
    def __init__(self, pid: int) -> None:
        super().__init__(pid, thread_name="GC Monitor")
        self._events: List[GCMonitorStatsItem] = []
        self._lock = threading.Lock()
    
    def add_event(self, stats_item: GCMonitorStatsItem) -> None:
        """Thread-safe event collection."""
        with self._lock:
            self._events.append(stats_item)
    
    def close(self) -> None:
        """No-op for in-memory collector."""
        pass
    
    def get_events(self) -> List[GCMonitorStatsItem]:
        """Thread-safe event retrieval (returns copy)."""
        with self._lock:
            return list(self._events)
    
    def clear(self) -> None:
        """Thread-safe clear."""
        with self._lock:
            self._events.clear()


class GCMonitorHook:
    """
    Pyperf hook for GC monitoring.
    
    Usage:
        # Via CLI
        pyperf run --hook=gc_monitor ...
        
        # Via entry point registration in pyproject.toml
        [project.entry-points."pyperf.hook"]
        gc_monitor = "gc_monitor.pyperf_hook:gc_monitor_hook"
    """
    
    def __init__(self, rate: float = 0.01) -> None:
        """
        Initialize the hook (called once per process).
        
        Args:
            rate: Polling rate in seconds (default: 0.01 = 10ms)
        """
        self._rate = rate
        self._collector: Optional[InMemoryCollector] = None
        self._monitor: Optional["GCMonitor"] = None
        self._handler: Any = None
        self._pid: int = 0
    
    def __enter__(self) -> "GCMonitorHook":
        """
        Called immediately before running benchmark code.
        
        Starts GC monitoring for the current process.
        """
        self._pid = os.getpid()
        self._collector = InMemoryCollector(self._pid)
        
        # Connect to GC monitor
        try:
            self._handler = _connect(self._pid)
            from .core import GCMonitor  # noqa: PLC0415
            self._monitor = GCMonitor(self._handler, self._collector, self._rate)
        except Exception as e:
            _logger.warning(f"Failed to start GC monitoring: {e}")
            self._handler = None
            self._monitor = None
        
        return self
    
    def __exit__(
        self,
        _exc_type: Optional[type[BaseException]],
        _exc_value: Optional[BaseException],
        _traceback: Optional[object],
    ) -> None:
        """
        Called immediately after running benchmark code.
        
        Stops GC monitoring. Exceptions from benchmark are ignored.
        """
        try:
            if self._monitor:
                self._monitor.stop()
                self._monitor = None
            
            if self._handler:
                _disconnect(self._handler)
                self._handler = None
        except Exception as e:
            _logger.warning(f"Error stopping GC monitor: {e}")
    
    def teardown(self, metadata: Dict[str, Any]) -> None:
        """
        Called when the hook is completed for a process.
        
        Aggregates collected GC statistics and adds them to pyperf metadata.
        
        Args:
            metadata: Pyperf metadata dictionary (modified in-place)
        """
        if not self._collector:
            return
        
        events = self._collector.get_events()
        if not events:
            return
        
        # Aggregate statistics
        aggregated = _aggregate_gc_stats(events)
        
        # Add to metadata with gc_ prefix
        for key, value in aggregated.items():
            metadata[f"gc_{key}"] = value
        
        # Clear collector for next benchmark
        self._collector.clear()


def _aggregate_gc_stats(events: List[GCMonitorStatsItem]) -> Dict[str, Any]:
    """
    Aggregate GC statistics from collected events.
    
    Returns a dictionary of aggregated metrics ready for pyperf metadata.
    """
    if not events:
        return {}
    
    import statistics
    
    # Group by generation
    by_gen: Dict[int, List[GCMonitorStatsItem]] = {0: [], 1: [], 2: []}
    for event in events:
        by_gen[event.gen].append(event)
    
    result: Dict[str, Any] = {}
    
    # Per-generation collection counts
    for gen, gen_events in by_gen.items():
        result[f"collections_by_gen_{gen}"] = sum(e.collections for e in gen_events)
    
    # Overall aggregations
    durations = [e.duration for e in events]
    heap_sizes = [e.heap_size for e in events]
    object_visits = [e.object_visits for e in events]
    
    event_count = len(events)
    
    result.update({
        # Sums (cumulative)
        "collections_total": sum(e.collections for e in events),
        "objects_collected_total": sum(e.collected for e in events),
        "objects_uncollectable_total": sum(e.uncollectable for e in events),
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
        "event_count": event_count,
    })
    
    return result


# Entry point factory function
def gc_monitor_hook(rate: float = 0.01) -> GCMonitorHook:
    """
    Factory function for pyperf entry point.
    
    Args:
        rate: Polling rate in seconds (default: 0.01)
    
    Returns:
        GCMonitorHook instance
    """
    return GCMonitorHook(rate=rate)
```

### A.2 Updated `pyproject.toml` Entry Points Section

```toml
[project.entry-points."pyperf.hook"]
gc_monitor = "gc_monitor.pyperf_hook:gc_monitor_hook"
```

---

**Document Version:** 1.0  
**Last Updated:** 2026-03-21  
**Author:** Architecture Review
