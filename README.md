# gc-monitor

A modern Python package for monitoring Python's garbage collector (GC) and exporting statistics in various formats.

![Python Version](https://img.shields.io/badge/python-3.12+-blue.svg)
![License](https://img.shields.io/badge/license-MIT-green.svg)

## Features

- **Real-time GC monitoring** - Track garbage collection events in running Python processes
- **Multiple export formats** - Chrome Trace Event, Pyperf JSON, JSONL file, and JSONL (stdout) support
- **CLI and API** - Use via command-line or integrate into your own tools
- **Pyperf hook integration** - Seamlessly integrate with pyperf benchmarks
- **Lightweight** - Minimal overhead background thread polling
- **External process architecture** - Monitor processes without in-process overhead
- **Auto-flush support** - Stream large traces to disk incrementally
- **Graceful shutdown** - Cross-platform process termination (Unix: SIGINT → SIGTERM → SIGKILL, Windows: CTRL_BREAK_EVENT → kill())

## Installation

```bash
pip install -e .
```

## CLI Usage

The `gc-monitor` command provides a convenient way to monitor GC activity from the command line.

### Basic Usage

```bash
# Monitor a process until interrupted (Chrome format)
gc-monitor 12345

# Monitor with custom output file
gc-monitor 12345 -o gc_trace.json

# Monitor for a specific duration with verbose output
gc-monitor 12345 -d 30 -v

# Export in pyperf format (for pyperf hook integration)
gc-monitor 12345 --format pyperf -o gc_stats.json
```

### Command-Line Options

| Option | Description | Default |
|--------|-------------|---------|
| `pid` (required) | Process ID to monitor | - |
| `-o, --output` | Output file path for trace data | `gc_trace.json` (chrome), `gc_monitor.jsonl` (jsonl) |
| `-r, --rate` | Polling rate in seconds | `0.1` |
| `-d, --duration` | Monitoring duration in seconds | Until interrupted |
| `-v, --verbose` | Enable verbose output | `False` |
| `--format` | Output format: `chrome`, `pyperf`, `jsonl`, or `stdout` | `chrome` |
| `--flush-threshold` | Number of events to buffer before flushing (jsonl format) | `100` |

### Example Commands

**Monitor a long-running process:**
```bash
gc-monitor 12345 --output server_gc.json --verbose
```

**Quick 10-second sampling:**
```bash
gc-monitor 12345 --duration 10 --rate 0.05
```

**High-frequency monitoring:**
```bash
gc-monitor 12345 --output detailed_trace.json --rate 0.01
```

**Export for pyperf hook:**
```bash
gc-monitor 12345 --format pyperf --output gc_stats.json
```

**Export to JSONL format:**
```bash
# Export to JSONL file (one JSON object per line)
gc-monitor 12345 --format jsonl --output gc_events.jsonl

# Use default output file (gc_monitor.jsonl)
gc-monitor 12345 --format jsonl

# Custom flush threshold for high-frequency monitoring
gc-monitor 12345 --format jsonl --flush-threshold 50 --rate 0.01
```

**Stream JSONL to stdout:**
```bash
# Stream events to stdout (one JSON object per line)
gc-monitor 12345 --format stdout
```

## Pyperf Hook Integration

The gc-monitor package provides a pyperf hook for automatic GC metrics collection during benchmarks.

### Installation

First, install pyperf:

```bash
pip install pyperf
```

### Usage

```bash
# Run benchmark with GC monitoring
python my_benchmark.py --hook=gc_monitor

# Or using pyperf directly
pyperf timeit --hook=gc_monitor my_benchmark.py

# Save results with GC metrics
python my_benchmark.py --hook=gc_monitor -o benchmark_results.json

# Analyze GC metrics from results
python examples/analyze_gc_metrics.py benchmark_results.json
```

### GC Metrics Collected

The hook collects and reports the following GC metrics in pyperf metadata:

- `gc_collections_total` - Total number of GC collections
- `gc_objects_collected_total` - Total objects collected
- `gc_objects_uncollectable_total` - Total uncollectable objects
- `gc_avg_pause_duration_sec` - Average GC pause duration
- `gc_max_pause_duration_sec` - Maximum GC pause duration
- `gc_min_pause_duration_sec` - Minimum GC pause duration
- `gc_avg_heap_size` - Average heap size
- `gc_max_heap_size` - Maximum heap size
- `gc_collections_by_gen_0`, `gc_collections_by_gen_1`, `gc_collections_by_gen_2` - Collections by generation

### Example Benchmarks

See the `examples/` directory for benchmark examples:

- `pyperf_benchmark_example.py` - Simple benchmark demonstrating hook usage
- `pyperf_advanced_example.py` - Advanced benchmark with GC enabled/disabled comparison
- `analyze_gc_metrics.py` - Script to analyze GC metrics from benchmark results

## API Usage

### Basic Monitoring

```python
from gc_monitor import TraceExporter, connect
import time

# Create exporter
exporter = TraceExporter(pid=12345, output_path=Path("trace.json"))

# Connect and start monitoring
monitor = connect(pid, exporter=exporter, rate=0.1)

# Monitor for some time
time.sleep(5)

# Stop and save
monitor.stop()
```

### Pyperf Exporter

```python
from gc_monitor import PyperfExporter, connect
import time

# Create pyperf exporter
exporter = PyperfExporter(pid=12345)

# Connect and start monitoring
monitor = connect(pid, exporter=exporter, rate=0.1)

# Monitor for some time
time.sleep(5)

# Stop and save
monitor.stop()
exporter.write(Path("gc_stats.json"))
```

## Getting Started

1. **Install dependencies:**
   ```bash
   poetry install
   # Or using pip
   pip install -e .
   ```

2. **Run tests:**
   ```bash
   pytest
   ```

3. **Build the distribution:**
   ```bash
   python -m build
   ```

## Project Structure

- `src/gc_monitor/` - Package source
  - `_gc_monitor.py` - Low-level GC monitoring API (or mock implementation)
  - `_process_terminator.py` - Cross-platform process termination utilities
  - `core.py` - High-level monitoring (GCMonitor class)
  - `exporter.py` - Base exporter interface
  - `chrome_trace_exporter.py` - Chrome Trace format exporter
  - `pyperf_exporter.py` - Pyperf JSON format exporter
  - `pyperf_hook.py` - Pyperf hook integration
  - `jsonl_exporter.py` - JSONL file format exporter
  - `stdout_exporter.py` - JSONL stdout exporter
  - `cli.py` - Command-line interface
- `tests/` - Test suite (160 tests, ~92% coverage)
- `examples/` - Usage examples and benchmarks
- `pyproject.toml` - Packaging configuration
- `Makefile` - Build/test commands
- `README.md` - This file
- `LICENSE` - License

## Architecture

### External Process Model (Pyperf Hook)

The pyperf hook uses an external process architecture to minimize overhead:

1. Hook spawns `gc-monitor` CLI as a separate process
2. External process reads target process memory directly
3. Results written to temp JSON file
4. Hook reads JSON and injects metrics into pyperf metadata

This approach provides:
- Zero in-process overhead during benchmarks
- Crash isolation (gc-monitor crashes don't affect benchmark)
- Clean separation of concerns

### CPython Integration

When running on CPython with the experimental `_gc_monitor` module available, the package uses the native implementation. Otherwise, it falls back to a mock implementation for testing and development.

## Interaction Schema

This section illustrates how gc-monitor interacts with monitored processes across different usage modes.

### 1. Direct Monitoring Mode (CLI/API)

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                           MONITORED PYTHON PROCESS                              │
│                              (PID: 12345)                                       │
│                                                                                 │
│  ┌─────────────────────────────────────────────────────────────────────────┐   │
│  │                      Python Runtime + GC                                │   │
│  │                                                                         │   │
│  │    ┌──────────────┐    ┌──────────────┐    ┌──────────────┐            │   │
│  │    │  GC Gen 0    │    │  GC Gen 1    │    │  GC Gen 2    │            │   │
│  │    │  Collection  │    │  Collection  │    │  Collection  │            │   │
│  │    └──────┬───────┘    └──────┬───────┘    └──────┬───────┘            │   │
│  │           │                   │                   │                     │   │
│  │           └───────────────────┼───────────────────┘                     │   │
│  │                               │                                         │   │
│  │                    ┌──────────▼──────────┐                             │   │
│  │                    │   _gc_monitor API   │ ◄─── Native C extension     │   │
│  │                    │   (C-level hooks)   │       (CPython only)        │   │
│  │                    └──────────┬──────────┘                             │   │
│  │                               │                                         │   │
│  │                    ┌──────────▼──────────┐                             │   │
│  │                    │   GC Statistics     │                             │   │
│  │                    │   (shared memory)   │                             │   │
│  │                    └──────────┬──────────┘                             │   │
│  │                               │                                         │   │
│  └───────────────────────────────┼─────────────────────────────────────────┘   │
│                                  │                                              │
│                                  │ Read via process memory                      │
│                                  ▼                                              │
└─────────────────────────────────────────────────────────────────────────────────┘
                                  │
                                  │ (External process reads monitored PID memory)
                                  ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│                           GC-MONITOR EXTERNAL PROCESS                           │
│                           (CLI or API usage)                                    │
│                                                                                 │
│  ┌─────────────────────────────────────────────────────────────────────────┐   │
│  │                         GCMonitor (core.py)                             │   │
│  │                                                                         │   │
│  │    ┌──────────────────────────────────────────────────────────────┐    │   │
│  │    │              Background Polling Thread                       │    │   │
│  │    │                                                              │    │   │
│  │    │    ┌────────────┐      ┌────────────┐      ┌────────────┐   │    │   │
│  │    │    │   Read GC  │─────►│  Process   │─────►│   Event    │   │    │   │
│  │    │    │   Stats    │      │  Changes   │      │  Queue     │   │    │   │
│  │    │    └────────────┘      └────────────┘      └─────┬──────┘   │    │   │
│  │    │                                                  │          │    │   │
│  │    └──────────────────────────────────────────────────┼──────────┘    │   │
│  │                                                       │               │   │
│  │    ┌──────────────────────────────────────────────────▼──────────┐    │   │
│  │    │                    Event Dispatcher                         │    │   │
│  │    │                         │                                   │    │   │
│  │    │                         ▼                                   │    │   │
│  │    │              ┌─────────────────────┐                        │    │   │
│  │    │              │  GCMonitorExporter  │ (Abstract Base)        │    │   │
│  │    │              └──────────┬──────────┘                        │    │   │
│  │    │                         │                                   │    │   │
│  │    └─────────────────────────┼───────────────────────────────────┘    │   │
│  │                              │                                        │   │
│  └──────────────────────────────┼────────────────────────────────────────┘   │
│                                 │                                             │
│         ┌───────────────────────┼───────────────────────┐                     │
│         │                       │                       │                     │
│         ▼                       ▼                       ▼                     │
│  ┌─────────────┐         ┌─────────────┐         ┌─────────────┐             │
│  │ TraceExporter│        │PyperfExporter│       │JSONLExporter│             │
│  │  (Chrome)   │        │  (Pyperf)    │       │  (File)     │             │
│  └──────┬──────┘         └──────┬──────┘         └──────┬──────┘             │
│         │                       │                       │                     │
│         ▼                       ▼                       ▼                     │
│  ┌─────────────┐         ┌─────────────┐         ┌─────────────┐             │
│  │ gc_trace.   │         │ gc_stats.   │         │ gc_events.  │             │
│  │ json        │         │ json        │         │ jsonl       │             │
│  └─────────────┘         └─────────────┘         └─────────────┘             │
│                                                                                 │
│  ┌─────────────────────────────────────────────────────────────────────────┐   │
│  │                    Signal Handler (Graceful Shutdown)                   │   │
│  │                                                                         │   │
│  │    Unix: SIGINT ──► SIGTERM ──► SIGKILL (escalating)                   │   │
│  │    Windows: CTRL_BREAK_EVENT ──► kill()                                │   │
│  │                                                                         │   │
│  │    ┌──────────────┐    ┌──────────────┐    ┌──────────────┐            │   │
│  │    │   Flush      │───►│   Stop       │───►│   Cleanup    │            │   │
│  │    │   Pending    │    │   Polling    │    │   Resources  │            │   │
│  │    │   Events     │    │   Thread     │    │              │            │   │
│  │    └──────────────┘    └──────────────┘    └──────────────┘            │   │
│  └─────────────────────────────────────────────────────────────────────────┘   │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

**Data Flow:**
```
GC Event → _gc_monitor API → GCMonitor (polling) → Exporter → Output File
```

**Signal Flow (Graceful Shutdown):**
```
User Input (Ctrl+C) → Signal Handler → Flush Events → Stop Thread → Cleanup → Exit
```

---

### 2. Pyperf Hook Mode

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                           PYPERF BENCHMARK PROCESS                              │
│                              (PID: 54321)                                       │
│                                                                                 │
│  ┌─────────────────────────────────────────────────────────────────────────┐   │
│  │                      pyperf benchmark runner                            │   │
│  │                                                                         │   │
│  │    ┌──────────────────────────────────────────────────────────────┐    │   │
│  │    │                    Benchmark Loop                            │    │   │
│  │    │                                                              │    │   │
│  │    │    ┌──────────────┐    ┌──────────────┐    ┌──────────────┐ │    │   │
│  │    │    │   Setup      │───►│   Run        │───►│   Teardown   │ │    │   │
│  │    │    │              │    │   (timed)    │    │              │ │    │   │
│  │    │    └──────────────┘    └──────┬───────┘    └──────────────┘ │    │   │
│  │    │                              │                               │    │   │
│  │    │                    ┌─────────▼─────────┐                     │    │   │
│  │    │                    │  gc_monitor_hook  │                     │    │   │
│  │    │                    │  (entry/exit)     │                     │    │   │
│  │    │                    └─────────┬─────────┘                     │    │   │
│  │    │                              │                               │    │   │
│  │    └──────────────────────────────┼───────────────────────────────┘    │   │
│  │                                   │                                    │   │
│  └───────────────────────────────────┼────────────────────────────────────┘   │
│                                      │                                        │
│         ┌────────────────────────────┼────────────────────────────┐           │
│         │                            │                            │           │
│         │              ┌─────────────▼──────────────┐             │           │
│         │              │   gc_monitor_hook entry    │             │           │
│         │              │                            │             │           │
│         │              │  1. Get temp file path     │             │           │
│         │              │  2. Spawn gc-monitor CLI   │             │           │
│         │              │     (subprocess)           │             │           │
│         │              │  3. Wait for benchmark     │             │           │
│         │              │  4. Read temp JSON         │             │           │
│         │              │  5. Inject into metadata   │             │           │
│         │              │  6. Cleanup temp file      │             │           │
│         │              │                            │             │           │
│         │              └─────────────┬──────────────┘             │           │
│         │                            │                            │           │
│         │              ┌─────────────▼──────────────┐             │           │
│         │              │   gc_monitor_hook exit     │             │           │
│         │              │                            │             │           │
│         │              │  1. Signal gc-monitor stop │             │           │
│         │              │  2. Wait for flush         │             │           │
│         │              │  3. Parse results          │             │           │
│         │              │  4. Add to pyperf metadata │             │           │
│         │              │                            │             │           │
│         │              └────────────────────────────┘             │           │
│         │                                                         │           │
│         └─────────────────────────────────────────────────────────┘           │
│                                                                               │
└───────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      │ (spawns subprocess)
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│                         GC-MONITOR CLI SUBPROCESS                               │
│                         (spawned by hook)                                       │
│                                                                                 │
│  ┌─────────────────────────────────────────────────────────────────────────┐   │
│  │                         CLI Entry Point                                 │   │
│  │                                                                         │   │
│  │    ┌──────────────┐    ┌──────────────┐    ┌──────────────────────┐   │   │
│  │    │   Parse      │───►│   Create     │───►│   Start Monitoring   │   │   │
│  │    │   Args       │    │   Exporter   │    │   (background)       │   │   │
│  │    └──────────────┘    └──────────────┘    └──────────┬───────────┘   │   │
│  │                                                       │               │   │
│  │    ┌──────────────┐    ┌──────────────┐    ┌──────────▼───────────┐   │   │
│  │    │   Cleanup    │◄───│   Write      │◄───│   Wait for Signal    │   │   │
│  │    │   (temp)     │    │   to Temp    │    │   (from hook)        │   │   │
│  │    └──────────────┘    │   JSON File  │    └──────────────────────┘   │   │
│  │                        └──────┬───────┘                                │   │
│  │                               │                                        │   │
│  └───────────────────────────────┼────────────────────────────────────────┘   │
│                                  │                                             │
│                                  ▼                                             │
│                        ┌──────────────────┐                                    │
│                        │  Temp JSON File  │                                    │
│                        │  (temp_xxxx.json)│                                    │
│                        └──────────────────┘                                    │
│                                  │                                             │
│                                  │ (read by hook)                              │
│                                  ▼                                             │
└─────────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      │ (metrics injection)
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│                           PYPERF METADATA OUTPUT                                │
│                                                                                 │
│  ┌─────────────────────────────────────────────────────────────────────────┐   │
│  │                    Benchmark Results JSON                               │   │
│  │                                                                         │   │
│  │    {                                                                    │   │
│  │      "benchmarks": [                                                    │   │
│  │        {                                                                │   │
│  │          "name": "my_benchmark",                                        │   │
│  │          "runs": [...],                                                 │   │
│  │          "metadata": {                                                  │   │
│  │            "gc_collections_total": 42,                                  │   │
│  │            "gc_objects_collected_total": 1250,                          │   │
│  │            "gc_avg_pause_duration_sec": 0.0023,                         │   │
│  │            "gc_max_pause_duration_sec": 0.0156,                         │   │
│  │            "gc_min_pause_duration_sec": 0.0008,                         │   │
│  │            "gc_avg_heap_size": 52428800,                                │   │
│  │            "gc_max_heap_size": 104857600                                │   │
│  │          }                                                              │   │
│  │        }                                                                │   │
│  │      ]                                                                  │   │
│  │    }                                                                    │   │
│  └─────────────────────────────────────────────────────────────────────────┘   │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

**Communication Flow:**
```
Hook Entry → Spawn gc-monitor CLI → Benchmark Runs → Hook Exit → 
Signal Stop → Read Temp JSON → Inject Metadata → Cleanup
```

---

### 3. Export Formats and Output Paths

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                              GC-MONITOR EXPORTERS                               │
│                                                                                 │
│                           ┌─────────────────────┐                               │
│                           │  GCMonitorExporter  │                               │
│                           │  (Abstract Base)    │                               │
│                           └──────────┬──────────┘                               │
│                                      │                                          │
│         ┌────────────────────────────┼────────────────────────────┐             │
│         │                            │                            │             │
│         │                            │                            │             │
│         ▼                            ▼                            ▼             │
│  ┌─────────────────┐         ┌─────────────────┐         ┌─────────────────┐   │
│  │  TraceExporter  │         │  PyperfExporter │         │  JSONLExporter  │   │
│  │  (Chrome Trace) │         │  (Pyperf JSON)  │         │  (JSONL File)   │   │
│  └────────┬────────┘         └────────┬────────┘         └────────┬────────┘   │
│           │                           │                           │             │
│           ▼                           ▼                           ▼             │
│  ┌─────────────────┐         ┌─────────────────┐         ┌─────────────────┐   │
│  │  Chrome Trace   │         │  Pyperf Metadata│         │  JSONL File     │   │
│  │  Event Format   │         │  Format         │         │  (one per line) │   │
│  └────────┬────────┘         └────────┬────────┘         └────────┬────────┘   │
│           │                           │                           │             │
│           ▼                           ▼                           ▼             │
│  ┌─────────────────┐         ┌─────────────────┐         ┌─────────────────┐   │
│  │  gc_trace.json  │         │  gc_stats.json  │         │ gc_events.jsonl │   │
│  │                 │         │                 │         │                 │   │
│  │  {              │         │  {              │         │  {"ts":...}     │   │
│  │    "traceEvents"|         │    "metadata":  │         │  {"ts":...}     │   │
│  │    [...]        │         │    {            │         │  {"ts":...}     │   │
│  │  }              │         │      "gc_..."   │         │  ...            │   │
│  │                 │         │    }            │         │                 │   │
│  └─────────────────┘         │  }              │         └─────────────────┘   │
│                              └─────────────────┘                               │
│                                                                                 │
│         ┌─────────────────────────────────────────────────────────────┐        │
│         │                                                             │        │
│         ▼                                                             │        │
│  ┌─────────────────┐                                                  │        │
│  │ StdoutExporter  │                                                  │        │
│  │ (JSONL stdout)  │                                                  │        │
│  └────────┬────────┘                                                  │        │
│           │                                                           │        │
│           ▼                                                           │        │
│  ┌─────────────────┐                                                  │        │
│  │  Terminal/Pipe  │                                                  │        │
│  │                 │                                                  │        │
│  │  {"ts":...}     │                                                  │        │
│  │  {"ts":...}     │◄─────────────────────────────────────────────────┘        │
│  │  ...            │           (all exporters inherit from base)               │
│  └─────────────────┘                                                            │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

**Output Format Summary:**

| Format | Exporter Class | Default Output | Use Case |
|--------|---------------|----------------|----------|
| Chrome Trace | `TraceExporter` | `gc_trace.json` | Visualize in Chrome DevTools, Perfetto |
| Pyperf JSON | `PyperfExporter` | `gc_stats.json` | Pyperf hook integration, benchmark metadata |
| JSONL File | `JSONLExporter` | `gc_events.jsonl` | Streaming, log aggregation, post-processing |
| JSONL Stdout | `StdoutExporter` | stdout | Piping to other tools, real-time monitoring |

---

### 4. Complete System Overview

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                              COMPLETE SYSTEM VIEW                               │
│                                                                                 │
│                                                                                 │
│  ┌─────────────────────────────────────────────────────────────────────────┐   │
│  │                         MODE 1: DIRECT MONITORING                       │   │
│  │                                                                         │   │
│  │    User runs: gc-monitor 12345 --format chrome --output trace.json     │   │
│  │                                                                         │   │
│  │    ┌──────────────┐         ┌──────────────┐         ┌──────────────┐  │   │
│  │    │  Monitored   │────────►│  gc-monitor  │────────►│  trace.json  │  │   │
│  │    │  Process     │         │  CLI Process │         │  (Chrome)    │  │   │
│  │    │  (PID 12345) │         │  (external)  │         │              │  │   │
│  │    └──────────────┘         └──────────────┘         └──────────────┘  │   │
│  └─────────────────────────────────────────────────────────────────────────┘   │
│                                                                                 │
│  ┌─────────────────────────────────────────────────────────────────────────┐   │
│  │                         MODE 2: PYPERF HOOK                             │   │
│  │                                                                         │   │
│  │    User runs: python benchmark.py --hook=gc_monitor                    │   │
│  │                                                                         │   │
│  │    ┌──────────────┐         ┌──────────────┐         ┌──────────────┐  │   │
│  │    │  Pyperf      │────────►│  gc-monitor  │────────►│  Temp JSON   │  │   │
│  │    │  Benchmark   │  spawn  │  Subprocess  │  write  │  File        │  │   │
│  │    │  Process     │         │  (monitor)   │         │              │  │   │
│  │    └──────────────┘         └──────────────┘         └──────┬───────┘  │   │
│  │                                                             │           │   │
│  │         ┌──────────────┐         ┌──────────────┐          │           │   │
│  │         │  Pyperf      │◄────────│  gc_monitor  │◄─────────┘           │   │
│  │         │  Metadata    │ inject │  Hook        │  read                 │   │
│  │         │  (gc_stats)  │         │  (cleanup)   │                      │   │
│  │         └──────────────┘         └──────────────┘                      │   │
│  └─────────────────────────────────────────────────────────────────────────┘   │
│                                                                                 │
│  ┌─────────────────────────────────────────────────────────────────────────┐   │
│  │                         EXPORT FORMAT CHAIN                             │   │
│  │                                                                         │   │
│  │    GC Events → Exporter Base → Format-Specific → File/Stream           │   │
│  │                  │                                                      │   │
│  │                  ├─► TraceExporter ──► Chrome Trace JSON               │   │
│  │                  ├─► PyperfExporter ──► Pyperf Metadata JSON           │   │
│  │                  ├─► JSONLExporter ──► JSONL File                      │   │
│  │                  └─► StdoutExporter ──► JSONL to stdout                │   │
│  └─────────────────────────────────────────────────────────────────────────┘   │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

## License

MIT License - see [LICENSE](LICENSE) for details.
