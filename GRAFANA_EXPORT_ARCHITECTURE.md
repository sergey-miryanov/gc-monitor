# Grafana Export Architecture Plan

## Executive Summary

This document outlines the architectural plan for adding Grafana export functionality to the gc-monitor project using OpenTelemetry Protocol (OTLP) as the export mechanism. The recommended approach leverages **OpenTelemetry Metrics** with the **OTLP HTTP exporter** to send GC monitoring data to Grafana Mimir (or compatible backends like Prometheus via OTLP).

---

## 1. Architecture Overview

### 1.1 Recommended Approach

**Primary Recommendation: OpenTelemetry Metrics + OTLP HTTP Export**

| Decision | Choice | Rationale |
|----------|--------|-----------|
| **Grafana Component** | Grafana Mimir | Purpose-built for metrics at scale; native OTLP support; Prometheus-compatible querying |
| **Export Protocol** | OTLP over HTTP | Industry standard; better firewall compatibility than gRPC; simpler debugging |
| **Telemetry Type** | Metrics (not Logs/Traces) | GC stats are numerical measurements over time; metrics enable aggregation and alerting |
| **Export Mode** | Periodic batching | Efficient network usage; aligns with OpenTelemetry SDK patterns |
| **Temporality** | Delta (configurable) | Better for rate calculations; reduces memory footprint on server side |

### 1.2 Why Not Alternatives?

| Alternative | Why Not Recommended |
|-------------|---------------------|
| **Grafana Loki** | Designed for log aggregation; GC metrics are structured numerical data, not log entries |
| **Grafana Tempo** | Built for distributed tracing; GC events don't have trace context or span relationships |
| **Direct Prometheus Push Gateway** | Requires additional infrastructure; OTLP is more future-proof and vendor-neutral |
| **gRPC OTLP** | Slightly more complex firewall configuration; HTTP is sufficient for this use case |

### 1.3 System Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        GC Monitor Application                               │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │                    GCMonitorHandler                                  │   │
│  │              (collects GC stats from Python runtime)                 │   │
│  └─────────────────────────────┬────────────────────────────────────────┘   │
│                                │                                            │
│                                ▼                                            │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │                    GCMonitor (core.py)                               │   │
│  │              (polls handler at configured rate)                      │   │
│  └─────────────────────────────┬────────────────────────────────────────┘   │
│                                │                                            │
│                                ▼                                            │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │              GCMonitorExporter (interface)                           │   │
│  │         ┌──────────────────────┬──────────────────────┐              │   │
│  │         ▼                      ▼                      ▼              │   │
│  │  ┌──────────────┐      ┌───────────────┐      ┌──────────────────┐   │   │
│  │  │ TraceExporter│      │GrafanaExporter│      │(Future exporters)│   │   │
│  │  │ (Chrome JSON)│      │  (OTLP)       │      │                  │   │   │
│  │  └──────────────┘      └──────┬────────┘      └──────────────────┘   │   │
│  │                               │                                      │   │
│  └───────────────────────────────┼──────────────────────────────────────┘   │
│                                  │                                          │
│                                  ▼                                          │
│                    ┌─────────────────────────┐                              │
│                    │  OpenTelemetry SDK      │                              │
│                    │  - MeterProvider        │                              │
│                    │  - PeriodicExporting... │                              │
│                    │  - OTLPMetricExporter   │                              │
│                    └───────────┬─────────────┘                              │
└────────────────────────────────┼────────────────────────────────────────────┘
                                 │
                                 │ OTLP/HTTP (port 4318)
                                 │ (batched metrics every 10s)
                                 ▼
                    ┌─────────────────────────┐
                    │  Grafana Alloy /        │
                    │  OpenTelemetry Collector│
                    │  (optional aggregation) │
                    └───────────┬─────────────┘
                                │
                                ▼
                    ┌─────────────────────────┐
                    │   Grafana Mimir         │
                    │   (metrics storage)     │
                    └───────────┬─────────────┘
                                │
                                ▼
                    ┌─────────────────────────┐
                    │   Grafana Dashboard     │
                    │   (visualization)       │
                    └─────────────────────────┘
```

---

## 2. Project Structure

### 2.1 Updated Directory Layout

```
gc-monitor/
├── src/
│   └── gc_monitor/
│       ├── __init__.py              # Updated exports
│       ├── _gc_monitor.py           # GCMonitorStatsItem, GCMonitorHandler (unchanged)
│       ├── exporter.py              # GCMonitorExporter base class (unchanged)
│       ├── chrome_trace_exporter.py # TraceExporter (unchanged)
│       ├── grafana_exporter.py      # NEW: GrafanaExporter class
│       ├── otel_metrics.py          # NEW: OpenTelemetry metric definitions
│       ├── core.py                  # GCMonitor, connect() (unchanged)
│       └── cli.py                   # Updated with --exporter flag
├── tests/
│   ├── test_chrome_trace.py         # Existing tests
│   ├── test_cli.py                  # Existing tests
│   ├── test_grafana_exporter.py     # NEW: GrafanaExporter tests
│   └── test_otel_metrics.py         # NEW: OpenTelemetry mapping tests
├── examples/
│   ├── basic_usage.py               # Existing example
│   └── grafana_export_example.py    # NEW: Grafana export example
├── dashboards/
│   └── gc-monitor-dashboard.json    # NEW: Pre-built Grafana dashboard
└── pyproject.toml                   # Updated dependencies
```

---

## 3. Key Components

### 3.1 GrafanaExporter Class

**File:** `src/gc_monitor/grafana_exporter.py`

**Responsibilities:**
- Implement `GCMonitorExporter` interface
- Initialize OpenTelemetry MeterProvider with OTLP exporter
- Convert `GCMonitorStatsItem` to OpenTelemetry metrics
- Handle batching and periodic export
- Manage connection lifecycle and error handling

**Key Design Decisions:**
- Thread-safe metric recording (OpenTelemetry SDK handles this)
- Configurable export interval (default: 10 seconds)
- Graceful shutdown with flush on close
- Optional endpoint configuration via constructor or environment variables

### 3.2 OpenTelemetry Metric Definitions

**File:** `src/gc_monitor/otel_metrics.py`

**Responsibilities:**
- Define metric names following OpenTelemetry semantic conventions
- Specify metric types (Counter, Gauge, Histogram)
- Document units and descriptions
- Provide mapping from GCMonitorStatsItem fields

**Metric Mapping:**

| GCMonitorStatsItem Field | Metric Name | Type | Unit | Description |
|--------------------------|-------------|------|------|-------------|
| `gen` | `python.gc.generation` | Gauge | {generation} | GC generation (0, 1, 2) |
| `collections` | `python.gc.collections.total` | Counter | {collections} | Total number of GC collections |
| `collected` | `python.gc.objects.collected` | Counter | {objects} | Objects collected in this cycle |
| `uncollectable` | `python.gc.objects.uncollectable` | Gauge | {objects} | Objects that couldn't be collected |
| `candidates` | `python.gc.objects.candidates` | Gauge | {objects} | Objects candidate for collection |
| `object_visits` | `python.gc.object_visits` | Gauge | {visits} | Number of objects visited |
| `objects_transitively_reachable` | `python.gc.objects.transitively_reachable` | Gauge | {objects} | Transitively reachable objects |
| `objects_not_transitively_reachable` | `python.gc.objects.not_transitively_reachable` | Gauge | {objects} | Objects not transitively reachable |
| `heap_size` | `python.gc.heap.size` | Gauge | By | Current heap size in bytes |
| `work_to_do` | `python.gc.work.pending` | Gauge | {work_units} | Pending GC work units |
| `duration` | `python.gc.pause.duration` | Histogram | s | Individual GC pause duration |
| `total_duration` | `python.gc.total.duration` | Gauge | s | Cumulative GC time |

**Note on Temporality:**
- **Counters** (monotonically increasing): `collections.total`, `objects.collected`
- **Gauges** (point-in-time values): All other metrics
- **Histogram** (distribution): `pause.duration` for percentile analysis

### 3.3 Configuration Options

**Constructor Parameters:**
```python
GrafanaExporter(
    pid: int,
    endpoint: str = "http://localhost:4318/v1/metrics",  # OTLP endpoint
    service_name: str = "gc-monitor",                     # Service identifier
    export_interval: float = 10.0,                        # Export interval (seconds)
    temporality: str = "delta",                           # "delta" or "cumulative"
    headers: dict[str, str] | None = None,                # Optional auth headers
    timeout: float = 10.0,                                # Request timeout (seconds)
)
```

**Environment Variables (OpenTelemetry Standard):**
```bash
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318
OTEL_EXPORTER_OTLP_METRICS_ENDPOINT=http://localhost:4318/v1/metrics
OTEL_SERVICE_NAME=gc-monitor
OTEL_RESOURCE_ATTRIBUTES=deployment.environment=production
OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE=delta
OTEL_EXPORTER_OTLP_HEADERS=Authorization=Bearer token123
OTEL_EXPORTER_OTLP_TIMEOUT=10
```

---

## 4. Technology Stack

### 4.1 Dependencies to Add

**File:** `pyproject.toml`

```toml
[tool.poetry.dependencies]
python = ">=3.12"
opentelemetry-api = "^1.28.0"        # OpenTelemetry API
opentelemetry-sdk = "^1.28.0"        # OpenTelemetry SDK
opentelemetry-exporter-otlp-proto-http = "^1.28.0"  # OTLP HTTP exporter
```

**Rationale:**
- **opentelemetry-api**: Core API for metrics instrumentation
- **opentelemetry-sdk**: Reference implementation with exporters
- **opentelemetry-exporter-otlp-proto-http**: HTTP-based OTLP exporter (preferred over gRPC for simplicity)

**Version Strategy:**
- Pin to `^1.28.0` (latest stable as of early 2025)
- All three packages should use the same version to avoid compatibility issues

### 4.2 Optional Dependencies

For users who don't need Grafana export:

```toml
[tool.poetry.extras]
grafana = [
    "opentelemetry-api",
    "opentelemetry-sdk",
    "opentelemetry-exporter-otlp-proto-http",
]
```

Usage: `pip install gc-monitor[grafana]`

---

## 5. Implementation Plan

### 5.1 Phase 1: Core Implementation (TDD Approach)

**Task 1.1: Test-First Development**
- [ ] Create `tests/test_otel_metrics.py`
  - Test metric name conventions
  - Test field-to-metric mapping
  - Test metric type assignments (Counter vs Gauge vs Histogram)
- [ ] Create `tests/test_grafana_exporter.py`
  - Test exporter initialization
  - Test `add_event()` with mock GCMonitorStatsItem
  - Test `close()` flushes pending metrics
  - Test thread safety
  - Test error handling (endpoint unavailable)

**Task 1.2: OpenTelemetry Metric Definitions**
- [ ] Create `src/gc_monitor/otel_metrics.py`
  - Define `GCMetricNames` class with metric name constants
  - Define `create_meter_provider()` factory function
  - Document semantic conventions used

**Task 1.3: GrafanaExporter Implementation**
- [ ] Create `src/gc_monitor/grafana_exporter.py`
  - Implement `GCMonitorExporter` interface
  - Initialize MeterProvider in `__init__`
  - Implement `add_event()` to record metrics
  - Implement `close()` to shutdown MeterProvider
  - Add thread safety (lock for metric recording)
  - Add error handling and logging

### 5.2 Phase 2: CLI Integration

**Task 2.1: CLI Updates**
- [ ] Update `src/gc_monitor/cli.py`
  - Add `--exporter` argument: `trace` (default) | `grafana`
  - Add `--grafana-endpoint` argument
  - Add `--grafana-export-interval` argument
  - Add environment variable support documentation
- [ ] Update argument validation
  - Validate endpoint URL format
  - Warn if Grafana exporter selected without optional dependencies

**Task 2.2: Package Exports**
- [ ] Update `src/gc_monitor/__init__.py`
  - Export `GrafanaExporter`
  - Update `__all__` list

### 5.3 Phase 3: Documentation & Examples

**Task 3.1: Example Code**
- [ ] Create `examples/grafana_export_example.py`
  - Basic usage with localhost
  - Usage with Grafana Cloud
  - Usage with custom headers (authentication)

**Task 3.2: Grafana Dashboard**
- [ ] Create `dashboards/gc-monitor-dashboard.json`
  - GC pause duration histogram
  - Heap size over time
  - Objects collected per generation
  - GC frequency metrics
  - Import instructions in README

**Task 3.3: Documentation Updates**
- [ ] Update `README.md`
  - Grafana export section
  - Configuration options
  - Grafana Cloud integration guide
  - Dashboard import instructions
- [ ] Add `GRAFANA_EXPORT.md` (detailed guide)
  - Architecture overview
  - Quickstart with Docker Compose (Grafana + Mimir + Alloy)
  - Troubleshooting guide

### 5.4 Phase 4: Testing & Validation

**Task 4.1: Integration Testing**
- [ ] Create Docker Compose setup for local testing
  - Grafana Mimir (or Prometheus with OTLP)
  - Grafana Alloy (optional)
  - Grafana (visualization)
- [ ] Manual validation script
  - Run gc-monitor with Grafana exporter
  - Verify metrics appear in Grafana
  - Test dashboard queries

**Task 4.2: CI/CD Updates**
- [ ] Add Grafana exporter tests to CI pipeline
- [ ] Add type checking for new modules
- [ ] Update mypy configuration if needed

---

## 6. CLI Integration

### 6.1 New Command-Line Options

```bash
# Basic usage with Grafana exporter
gc-monitor 12345 --exporter grafana

# With custom endpoint
gc-monitor 12345 --exporter grafana \
  --grafana-endpoint http://mimir.local:4318/v1/metrics

# With export interval
gc-monitor 12345 --exporter grafana \
  --grafana-export-interval 5.0

# With verbose output
gc-monitor 12345 --exporter grafana -v

# Using environment variables
export OTEL_EXPORTER_OTLP_ENDPOINT=http://mimir.local:4318
export OTEL_SERVICE_NAME=my-python-app
gc-monitor 12345 --exporter grafana
```

### 6.2 Updated CLI Signature

```python
parser.add_argument(
    "-e",
    "--exporter",
    type=str,
    choices=["trace", "grafana"],
    default="trace",
    help="Exporter type: trace (Chrome JSON) or grafana (OTLP metrics)",
)
parser.add_argument(
    "--grafana-endpoint",
    type=str,
    default=None,
    help="OTLP endpoint for Grafana export (default: from env or http://localhost:4318/v1/metrics)",
)
parser.add_argument(
    "--grafana-export-interval",
    type=float,
    default=10.0,
    help="Export interval in seconds (default: 10.0)",
)
```

### 6.3 Example Usage Patterns

**Pattern 1: Local Development with Grafana Stack**
```bash
# Start Grafana stack (Docker Compose)
docker-compose up -d

# Run gc-monitor
gc-monitor $$ --exporter grafana -v
```

**Pattern 2: Grafana Cloud**
```bash
export OTEL_EXPORTER_OTLP_ENDPOINT=https://otlp-gateway-prod-us-east-0.grafana.net/otlp
export OTEL_EXPORTER_OTLP_HEADERS="Authorization=Basic base64encodedcreds"
export OTEL_SERVICE_NAME=my-production-app

gc-monitor $$ --exporter grafana -v
```

**Pattern 3: Dual Export (Trace + Grafana)**
```python
# Custom script for dual export
from gc_monitor import GCMonitor, TraceExporter, GrafanaExporter, connect
from pathlib import Path

pid = 12345
trace_exporter = TraceExporter(pid, Path("gc_trace.json"))
grafana_exporter = GrafanaExporter(pid, endpoint="http://localhost:4318/v1/metrics")

# Note: This requires modifying core.py to support multiple exporters
# See "Future Enhancements" section
monitor = connect(pid, exporter=grafana_exporter, rate=0.1)
```

---

## 7. Data Model Mapping

### 7.1 GCMonitorStatsItem → OpenTelemetry Metrics

```python
# Conceptual mapping implementation

from opentelemetry import metrics

# Get meter from provider
meter = metrics.get_meter("gc-monitor")

# Create instruments (done once in GrafanaExporter.__init__)
collections_counter = meter.create_counter(
    name="python.gc.collections.total",
    description="Total number of GC collections",
    unit="{collections}",
)

heap_size_gauge = meter.create_gauge(
    name="python.gc.heap.size",
    description="Current heap size",
    unit="By",
)

pause_duration_histogram = meter.create_histogram(
    name="python.gc.pause.duration",
    description="GC pause duration",
    unit="s",
)

# In add_event(), record metrics from GCMonitorStatsItem
def add_event(self, stats_item: GCMonitorStatsItem) -> None:
    # Common attributes for all metrics
    common_attrs = {
        "gc.generation": stats_item.gen,
        "pid": self._pid,
    }

    # Record counter (monotonically increasing)
    self._collections_counter.add(
        amount=stats_item.collections,
        attributes=common_attrs,
    )

    # Record gauges (point-in-time values)
    self._heap_size_gauge.set(
        amount=stats_item.heap_size,
        attributes=common_attrs,
    )

    # Record histogram (for percentile analysis)
    self._pause_duration_histogram.record(
        amount=stats_item.duration,
        attributes=common_attrs,
    )

    # ... record other metrics
```

### 7.2 Resource Attributes

OpenTelemetry Resource attributes provide context for all metrics:

```python
from opentelemetry.sdk.resources import Resource

resource = Resource.create(
    attributes={
        "service.name": "gc-monitor",
        "service.version": "0.1.0",
        "process.pid": pid,
        "telemetry.sdk.name": "gc-monitor",
        "telemetry.sdk.language": "python",
    }
)
```

---

## 8. Scalability Considerations

### 8.1 Performance Impact

| Concern | Mitigation |
|---------|------------|
| **Metric recording overhead** | OpenTelemetry SDK is optimized; recording is O(1) per metric |
| **Network calls** | Batching (default 10s interval) reduces frequency |
| **Memory usage** | SDK buffers metrics; configurable via `export_interval` |
| **Thread contention** | OpenTelemetry SDK is thread-safe; minimal lock contention |

### 8.2 Scaling Strategies

**Small Scale (Single Process):**
- Direct export to Grafana Mimir/Prometheus
- Default 10s export interval

**Medium Scale (Multiple Processes):**
- Use Grafana Alloy as aggregator
- Each process exports to Alloy; Alloy batches and forwards
- Reduces connections to backend

**Large Scale (Distributed Systems):**
- Deploy OpenTelemetry Collector cluster
- Use OTLP/gRPC for efficiency
- Implement metric sampling if needed

### 8.3 Cardinality Management

**Risk:** High cardinality from `pid` attribute if monitoring many processes

**Mitigation:**
- Use `service.instance.id` instead of raw PID
- Aggregate metrics by service name, not individual process
- Document cardinality best practices in README

---

## 9. Potential Challenges & Mitigations

### 9.1 Challenge: Optional Dependency Management

**Problem:** Not all users need Grafana export; OpenTelemetry adds ~5MB to package size

**Mitigation:**
- Use `poetry.extras` for optional dependencies
- Graceful degradation: fail with clear error message if Grafana exporter used without dependencies
- Lazy import: only import OpenTelemetry when GrafanaExporter is instantiated

```python
# In grafana_exporter.py
def __init__(self, ...) -> None:
    try:
        from opentelemetry import metrics
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
        # ... rest of imports
    except ImportError as e:
        raise ImportError(
            "GrafanaExporter requires opentelemetry packages. "
            "Install with: pip install gc-monitor[grafana]"
        ) from e
```

### 9.2 Challenge: Endpoint Configuration Complexity

**Problem:** Users may struggle with OTLP endpoint configuration

**Mitigation:**
- Sensible defaults (localhost:4318)
- Environment variable support (OpenTelemetry standard)
- Clear error messages on connection failure
- Documentation with common endpoint examples

### 9.3 Challenge: Metric Temporality Confusion

**Problem:** Users may not understand delta vs cumulative temporality

**Mitigation:**
- Default to `delta` (better for most use cases)
- Document the difference clearly
- Provide examples of queries for each temporality
- Allow configuration via constructor and environment variable

### 9.4 Challenge: Grafana Dashboard Setup

**Problem:** Users need pre-built dashboards to get value quickly

**Mitigation:**
- Include pre-built dashboard JSON in repository
- Document import process
- Provide dashboard for both Mimir and Prometheus backends
- Include common queries in README

### 9.5 Challenge: Authentication & Security

**Problem:** Grafana Cloud and enterprise deployments require authentication

**Mitigation:**
- Support `OTEL_EXPORTER_OTLP_HEADERS` environment variable
- Document Bearer token and Basic auth patterns
- Warn against hardcoding credentials in code
- Recommend secrets management for production

---

## 10. Implementation Code Skeletons

### 10.1 GrafanaExporter Class Skeleton

```python
"""Grafana OTLP exporter for GC monitoring data."""

import threading
from typing import Any, Dict, Optional

from opentelemetry import metrics
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource

from ._gc_monitor import GCMonitorStatsItem
from .exporter import GCMonitorExporter


class GrafanaExporter(GCMonitorExporter):
    """
    Thread-safe exporter for Grafana via OpenTelemetry Protocol (OTLP).

    Sends GC monitoring metrics to Grafana Mimir or compatible backends.
    """

    def __init__(
        self,
        pid: int,
        endpoint: str = "http://localhost:4318/v1/metrics",
        service_name: str = "gc-monitor",
        export_interval: float = 10.0,
        temporality: str = "delta",
        headers: Optional[Dict[str, str]] = None,
        timeout: float = 10.0,
        thread_name: str = "GC Monitor",
    ) -> None:
        """
        Initialize the Grafana exporter.

        Args:
            pid: Process ID being monitored
            endpoint: OTLP metrics endpoint URL
            service_name: Service name for OpenTelemetry resource
            export_interval: Interval between exports in seconds
            temporality: Metric temporality preference ("delta" or "cumulative")
            headers: Optional HTTP headers for authentication
            timeout: Request timeout in seconds
            thread_name: Name for the GC monitor thread
        """
        super().__init__(pid, thread_name)
        self._endpoint = endpoint
        self._export_interval = export_interval
        self._headers = headers
        self._timeout = timeout
        self._lock = threading.Lock()
        self._closed = False

        # Create resource
        resource = Resource.create(
            attributes={
                "service.name": service_name,
                "process.pid": pid,
            }
        )

        # Create exporter with temporality preference
        exporter = OTLPMetricExporter(
            endpoint=endpoint,
            headers=headers,
            timeout=timeout,
        )

        # Create reader with export interval
        reader = PeriodicExportingMetricReader(
            exporter=exporter,
            export_interval_millis=int(export_interval * 1000),
        )

        # Create meter provider
        self._meter_provider = MeterProvider(
            resource=resource,
            metric_readers=[reader],
        )

        # Set global meter provider (optional, for compatibility)
        metrics.set_meter_provider(self._meter_provider)

        # Get meter and create instruments
        self._meter = self._meter_provider.get_meter("gc-monitor")
        self._create_instruments()

    def _create_instruments(self) -> None:
        """Create OpenTelemetry metric instruments."""
        # Counter for cumulative counts
        self._collections_counter = self._meter.create_counter(
            name="python.gc.collections.total",
            description="Total number of GC collections",
            unit="{collections}",
        )

        # Gauges for point-in-time values
        self._heap_size_gauge = self._meter.create_gauge(
            name="python.gc.heap.size",
            description="Current heap size",
            unit="By",
        )

        self._uncollectable_gauge = self._meter.create_gauge(
            name="python.gc.objects.uncollectable",
            description="Objects that couldn't be collected",
            unit="{objects}",
        )

        # Histogram for pause durations
        self._pause_duration_histogram = self._meter.create_histogram(
            name="python.gc.pause.duration",
            description="GC pause duration",
            unit="s",
        )

        # ... create other instruments

    @override
    def add_event(self, stats_item: GCMonitorStatsItem) -> None:
        """
        Record GC monitoring metrics.

        Args:
            stats_item: GCMonitorStatsItem instance from callback
        """
        if self._closed:
            return

        with self._lock:
            # Common attributes
            attrs = {
                "gc.generation": stats_item.gen,
                "pid": str(self._pid),
            }

            # Record metrics
            self._collections_counter.add(stats_item.collections, attrs)
            self._heap_size_gauge.set(stats_item.heap_size, attrs)
            self._uncollectable_gauge.set(stats_item.uncollectable, attrs)
            self._pause_duration_histogram.record(stats_item.duration, attrs)

            # ... record other metrics

    @override
    def close(self) -> None:
        """
        Close the exporter and flush pending metrics.

        Safe to call multiple times.
        """
        with self._lock:
            if self._closed:
                return
            self._closed = True

        # Shutdown meter provider (flushes pending metrics)
        self._meter_provider.shutdown()
```

### 10.2 Docker Compose for Local Testing

```yaml
# docker-compose.test.yml
version: "3.8"

services:
  grafana:
    image: grafana/grafana:10.4.0
    ports:
      - "3000:3000"
    environment:
      - GF_AUTH_ANONYMOUS_ENABLED=true
      - GF_AUTH_ANONYMOUS_ORG_ROLE=Admin
    volumes:
      - ./dashboards:/etc/grafana/provisioning/dashboards
      - ./datasources:/etc/grafana/provisioning/datasources

  mimir:
    image: grafana/mimir:2.14.0
    ports:
      - "8080:8080"
    command:
      - -target=play
      - -config.file=/etc/mimir/config.yaml

  alloy:
    image: grafana/alloy:1.0.0
    ports:
      - "4318:4318"
    volumes:
      - ./alloy-config.alloy:/etc/alloy/config.alloy
    command:
      - run
      - --server.http.listen-addr=0.0.0.0:4318
      - --storage.path=/var/lib/alloy
      - /etc/alloy/config.alloy
```

---

## 11. Testing Strategy

### 11.1 Unit Tests

**test_grafana_exporter.py:**
```python
def test_exporter_initialization():
    """Test that exporter initializes correctly."""
    exporter = GrafanaExporter(pid=12345, endpoint="http://localhost:4318/v1/metrics")
    assert exporter is not None
    exporter.close()

def test_add_event_records_metrics():
    """Test that add_event records metrics correctly."""
    exporter = GrafanaExporter(pid=12345)
    stats_item = GCMonitorStatsItem(
        gen=0, ts=1000, collections=1, collected=10, uncollectable=0,
        candidates=5, object_visits=100, objects_transitively_reachable=50,
        objects_not_transitively_reachable=50, heap_size=10000,
        work_to_do=10, duration=0.5, total_duration=5.0
    )
    exporter.add_event(stats_item)
    # Verify metrics were recorded (may require mocking)
    exporter.close()

def test_close_flushes_metrics():
    """Test that close() flushes pending metrics."""
    exporter = GrafanaExporter(pid=12345)
    exporter.add_event(create_test_stats_item())
    exporter.close()
    # Verify shutdown was called on meter provider

def test_thread_safety():
    """Test that exporter is thread-safe."""
    exporter = GrafanaExporter(pid=12345)

    def add_events():
        for _ in range(100):
            exporter.add_event(create_test_stats_item())

    threads = [threading.Thread(target=add_events) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    exporter.close()
```

### 11.2 Integration Tests

**test_grafana_integration.py:**
```python
@pytest.mark.integration
def test_export_to_mimir():
    """Test export to local Mimir instance."""
    # Requires Docker Compose running
    exporter = GrafanaExporter(
        pid=12345,
        endpoint="http://localhost:4318/v1/metrics",
        export_interval=1.0,  # Fast export for testing
    )

    exporter.add_event(create_test_stats_item())
    time.sleep(2.0)  # Wait for export

    # Query Mimir to verify metrics received
    response = requests.get(
        "http://localhost:8080/prometheus/api/v1/query",
        params={"query": "python_gc_heap_size"},
    )
    assert response.status_code == 200

    exporter.close()
```

---

## 12. Future Enhancements

### 12.1 Multiple Exporters

**Current Limitation:** `connect()` accepts single exporter

**Future Enhancement:** Support multiple exporters simultaneously

```python
# Proposed API
class MultiExporter(GCMonitorExporter):
    def __init__(self, exporters: list[GCMonitorExporter]) -> None:
        self._exporters = exporters

    def add_event(self, stats_item: GCMonitorStatsItem) -> None:
        for exporter in self._exporters:
            exporter.add_event(stats_item)

    def close(self) -> None:
        for exporter in self._exporters:
            exporter.close()

# Usage
multi_exporter = MultiExporter([
    TraceExporter(pid, Path("gc_trace.json")),
    GrafanaExporter(pid, endpoint="http://localhost:4318/v1/metrics"),
])
monitor = connect(pid, exporter=multi_exporter, rate=0.1)
```

### 12.2 Custom Metric Aggregation

**Future Enhancement:** Allow users to configure which metrics to export

```python
GrafanaExporter(
    pid=12345,
    metrics_config={
        "python.gc.collections.total": {"enabled": True},
        "python.gc.heap.size": {"enabled": True},
        "python.gc.pause.duration": {"enabled": True, "histogram_boundaries": [0.01, 0.1, 0.5, 1.0, 5.0]},
        # ... disable other metrics
    },
)
```

### 12.3 OpenTelemetry Span Events

**Future Enhancement:** Export GC events as trace spans for correlation

```python
# Could be useful for correlating GC pauses with request latency
tracer = tracer_provider.get_tracer("gc-monitor")
with tracer.start_as_current_span("gc.pause") as span:
    span.set_attribute("gc.generation", stats_item.gen)
    span.set_attribute("gc.duration", stats_item.duration)
    # ... other attributes
```

---

## 13. Migration Guide (If Refactoring Existing Code)

### 13.1 For Existing Users

**No Breaking Changes:**
- Existing `TraceExporter` continues to work unchanged
- Default CLI behavior unchanged (`--exporter trace`)
- No changes to `GCMonitorStatsItem` or `GCMonitorHandler`

**Opt-In Migration:**
```bash
# Old usage (still works)
gc-monitor 12345 -o gc_trace.json

# New Grafana export
gc-monitor 12345 --exporter grafana

# Dual export (requires custom script)
# See examples/grafana_export_example.py
```

### 13.2 Code Changes Required

**Minimal Changes:**
```python
# Before
from gc_monitor import TraceExporter, connect

exporter = TraceExporter(pid, Path("gc_trace.json"))
monitor = connect(pid, exporter=exporter, rate=0.1)

# After (Grafana export)
from gc_monitor import GrafanaExporter, connect

exporter = GrafanaExporter(pid, endpoint="http://localhost:4318/v1/metrics")
monitor = connect(pid, exporter=exporter, rate=0.1)
```

---

## 14. Success Criteria

### 14.1 Functional Requirements

- [ ] `GrafanaExporter` implements `GCMonitorExporter` interface
- [ ] Metrics are exported to OTLP endpoint successfully
- [ ] All `GCMonitorStatsItem` fields are mapped to metrics
- [ ] CLI supports `--exporter grafana` option
- [ ] Environment variables are respected
- [ ] Graceful shutdown flushes pending metrics

### 14.2 Quality Requirements

- [ ] All new code has unit tests (TDD approach)
- [ ] Type hints are complete and pass mypy/pyright
- [ ] Documentation includes usage examples
- [ ] Pre-built Grafana dashboard is included
- [ ] Integration tests pass with local Grafana stack

### 14.3 Performance Requirements

- [ ] Metric recording overhead < 1ms per event
- [ ] Memory overhead < 10MB for OpenTelemetry SDK
- [ ] Network calls batched (default 10s interval)
- [ ] No blocking I/O in `add_event()` (async export)

---

## 15. References

1. **OpenTelemetry Python Documentation**: https://opentelemetry.io/docs/languages/python/
2. **OTLP Specification**: https://github.com/open-telemetry/opentelemetry-specification/blob/main/specification/protocol/otlp.md
3. **Grafana Mimir Documentation**: https://grafana.com/docs/mimir/latest/
4. **OpenTelemetry Semantic Conventions**: https://github.com/open-telemetry/semantic-conventions
5. **Grafana Alloy**: https://grafana.com/docs/alloy/latest/

---

## Appendix A: Complete Metric Reference

| Metric Name | Type | Unit | Description | Field |
|-------------|------|------|-------------|-------|
| `python.gc.generation` | Gauge | {generation} | GC generation | `gen` |
| `python.gc.collections.total` | Counter | {collections} | Total collections | `collections` |
| `python.gc.objects.collected` | Counter | {objects} | Objects collected | `collected` |
| `python.gc.objects.uncollectable` | Gauge | {objects} | Uncollectable objects | `uncollectable` |
| `python.gc.objects.candidates` | Gauge | {objects} | Collection candidates | `candidates` |
| `python.gc.object_visits` | Gauge | {visits} | Objects visited | `object_visits` |
| `python.gc.objects.transitively_reachable` | Gauge | {objects} | Transitively reachable | `objects_transitively_reachable` |
| `python.gc.objects.not_transitively_reachable` | Gauge | {objects} | Not transitively reachable | `objects_not_transitively_reachable` |
| `python.gc.heap.size` | Gauge | By | Heap size | `heap_size` |
| `python.gc.work.pending` | Gauge | {work_units} | Pending work | `work_to_do` |
| `python.gc.pause.duration` | Histogram | s | Pause duration | `duration` |
| `python.gc.total.duration` | Gauge | s | Total duration | `total_duration` |

---

*Document Version: 1.0*
*Last Updated: March 20, 2026*
*Author: Qwen Code Architecture Assistant*
