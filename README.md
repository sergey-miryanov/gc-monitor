# gc-monitor

A modern Python package for monitoring Python's garbage collector (GC) and exporting statistics in various formats.

![Python Version](https://img.shields.io/badge/python-3.12+-blue.svg)
![License](https://img.shields.io/badge/license-MIT-green.svg)

## Features

- **Real-time GC monitoring** - Track garbage collection events in running Python processes
- **Multiple export formats** - Chrome Trace Event, Pyperf JSON, and JSONL (stdout) support
- **CLI and API** - Use via command-line or integrate into your own tools
- **Pyperf hook integration** - Seamlessly integrate with pyperf benchmarks
- **Lightweight** - Minimal overhead background thread polling
- **External process architecture** - Monitor processes without in-process overhead
- **Auto-flush support** - Stream large traces to disk incrementally
- **Graceful shutdown** - Signal handling for clean termination

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
| `-o, --output` | Output file path for trace data | `gc_trace.json` |
| `-r, --rate` | Polling rate in seconds | `0.1` |
| `-d, --duration` | Monitoring duration in seconds | Until interrupted |
| `-v, --verbose` | Enable verbose output | `False` |
| `--format` | Output format: `chrome` or `pyperf` | `chrome` |

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
  - `core.py` - High-level monitoring (GCMonitor class)
  - `exporter.py` - Base exporter interface
  - `chrome_trace_exporter.py` - Chrome Trace format exporter
  - `pyperf_exporter.py` - Pyperf JSON format exporter
  - `pyperf_hook.py` - Pyperf hook integration
  - `cli.py` - Command-line interface
- `tests/` - Test suite
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

## License

MIT License - see [LICENSE](LICENSE) for details.
