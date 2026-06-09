# gcmon - zero-overhead GC monitoring for Python

[![PyPI](https://img.shields.io/pypi/v/gcmon.svg)](https://pypi.org/project/gcmon/)
[![CI](https://github.com/sergey-miryanov/gcmon/actions/workflows/ci.yml/badge.svg)](https://github.com/sergey-miryanov/gcmon/actions/workflows/ci.yml)
[![Python Version](https://img.shields.io/badge/python-3.15+-blue.svg)](https://pypi.org/project/gcmon/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

A package for monitoring Python garbage collection events and exporting
statistics in various formats.

## Why gcmon?

Python's garbage collector can introduce unpredictable pauses in
applications. The standard library provides `gc.get_stats()` for
aggregate collection counters and `gc.callbacks` for per-event hooks,
but both run inside the target process: callbacks add execution
overhead that distorts timing, while `gc.get_stats()` only exposes
cumulative counters with no per-pause resolution. Neither can monitor
a process without modifying its code.

gcmon reads GC statistics directly from a target process's memory via
the `_remote_debugging` CPython extension, with zero in-process
overhead and without pausing the target process — no code changes or
runtime modification required.

Use it to profile GC pause times in production services, debug memory
leaks, or integrate GC metrics into benchmarks.

## Features

- **Real-time GC monitoring** - Track garbage collection events in running Python
processes without in-process overhead
- **Multiple export formats** - Chrome Trace Event, JSONL file, and JSONL to stdout
- **CLI** - Monitor processes or run scripts with GC monitoring
- **Pyperf hook integration** - Seamlessly integrate with pyperf benchmarks

## Alternatives Comparison

| Approach | In-process? | Per-pause resolution | Zero code change | Overhead |
|---|---|---|---|---|
| `gc.callbacks` | Yes | Yes | No | No — distorts timing |
| `gc.get_stats()` | Yes | No — cumulative only | No | Minimal |
| `tracemalloc` | Yes | N/A — allocations, not GC | No | High |
| [`memray`](https://github.com/bloomberg/memray) | Yes (partial¹) | N/A — allocations, not GC | Partial (attach mode) | Moderate |
| [`py-spy`](https://github.com/benfred/py-spy) | No | N/A — CPU profiling only | Yes | Low |
| [`austin`](https://github.com/P403n1x87/austin) | No | Partial² | Yes | Minimal |
| **gcmon** | **No** | **Yes** | **Yes** | **Yes — zero in-process cost** |

¹ memray's `attach` mode avoids modifying code but still injects an allocator into the target process.
² austin's `-g` flag tags frames during GC activity but provides no per-pause timing or heap data.

## How It Works

gcmon runs **outside** the target process. It reads GC statistics directly
from the process's memory via the `_remote_debugging` CPython C extension
(available in CPython 3.15+), which uses platform-specific memory access APIs.

For the pyperf hook integration, gcmon uses an **external process model**:

1. The hook spawns the `gcmon` CLI as a separate process
2. The external process reads the target process memory directly
3. Results are written to a temporary JSON file
4. The hook reads the JSON and injects metrics into pyperf metadata

This provides zero in-process overhead during benchmarks, crash isolation
(gcmon crashes don't affect the target), and clean separation of concerns.

## Limitations

The monitoring and monitored processes must use the **exact same Python version
and build**. `gcmon` reads GC statistics directly from the target process's
in-memory data structures. The layout of these structures varies between Python
versions (fields, offsets, sizes), so mismatched binaries are rejected by
`_remote_debugging` to prevent undefined behavior or crashes.

## Installation

```bash
pip install gcmon

# With stats support (see Statistics below)
pip install gcmon[stats]
```

## Quick Start

```bash
# Monitor a running process by PID (default Chrome Trace format)
gcmon 12345

# Run a Python script with GC monitoring
gcmon run -s my_script.py

# Monitor with custom output and statistics output
gcmon 12345 -o trace.json --stats
```

### Example: Chrome Trace Output

<img src="docs/images/chrome-trace-example.png" alt="Chrome Trace Example" width="800">

*Example: GC monitoring data visualized in Chrome Trace viewer showing:*
- *GC Pause events (top row with markers)*
- *Heap Size over time (green area chart)*
- *Memory Counters*

This visualization helps you:
- **Identify GC pause patterns** - See when and how long GC pauses occur
- **Track memory growth** - Monitor heap size changes over time
- **Analyze collection efficiency** - Compare GC-related counters
- **Debug memory issues** - Spot memory leaks or inefficient collection patterns

### Example: JSONL Output

With `--format jsonl` (writes to file) or `--format stdout` (writes to terminal),
each line is a JSON object representing one GC event:

```jsonl
{"pid": 12345, "tid": 0, "gen": 0, "iid": 1, "ts_start": 1700000000000000, "ts_stop": 1700000000001500, "heap_size": 1048576, "collections": 42, "collected": 120, "uncollectable": 0, "candidates": 300, "duration": 1.5}
{"pid": 12345, "tid": 0, "gen": 1, "iid": 2, "ts_start": 1700000000200000, "ts_stop": 1700000000235000, "heap_size": 2097152, "collections": 3, "collected": 85, "uncollectable": 1, "candidates": 150, "duration": 3.5}
```

| Field | Description |
|-------|-------------|
| `pid` | Process ID of the monitored target |
| `gen` | GC generation (0, 1, or 2) |
| `iid` | Interpreter ID (`0` for the main interpreter) |
| `ts_start`, `ts_stop` | Event timestamps (nanoseconds) |
| `heap_size` | Heap size at event time (bytes) |
| `collections` | Cumulative collection count for this generation |
| `collected` | Objects collected in this event |
| `uncollectable` | Objects that could not be collected |
| `candidates` | Candidate objects for collection |
| `duration` | Pause duration (milliseconds) |

For incremental GC events, additional fields (`increment_size`, `alive_size`,
`ts_mark_alive_*`, `ts_fill_increment_*`, `ts_deduce_unreachable_*`) are included.

## When to Use

**Use gcmon when you want to:**

- Profile GC pause times in production or staging without modifying application code
- Measure GC impact on latency-sensitive services (APIs, real-time systems)
- Correlate GC activity with benchmark results via the pyperf hook
- Track heap size trends over time across running processes
- Debug intermittent latency spikes suspected to be GC-related

**Use something else when you need to:**

- Per-object allocation tracking — use [`tracemalloc`](https://docs.python.org/3/library/tracemalloc.html)
- Object reference graph inspection — use [`objgraph`](https://pypi.org/project/objgraph/)
- Allocation profiling — use [`memray`](https://github.com/bloomberg/memray)
- CPU profiling and flame graphs — use [`py-spy`](https://github.com/benfred/py-spy) or [`austin`](https://github.com/P403n1x87/austin)
- Coarse GC activity tagging in CPU profiles — use [`austin`](https://github.com/P403n1x87/austin) with `-g` (no per-pause timing or heap data)
- In-process GC callbacks (e.g., triggering actions on collection) — use [`gc.callbacks`](https://docs.python.org/3/library/gc.html#gc.callbacks)
- Cumulative collection counters without per-pause detail — use [`gc.get_stats()`](https://docs.python.org/3/library/gc.html#gc.get_stats)
- Monitor across different Python builds — gcmon requires the exact same binary (see [Limitations](#limitations))

## CLI Usage

The `gcmon` command uses subcommands (`monitor`, `run`). If no subcommand
is given, `monitor` is used by default.

### monitor

Monitor a running process by PID.

```bash
# Monitor a process until interrupted (Chrome format)
gcmon 12345
# or:
gcmon monitor 12345

# Monitor with custom output file
gcmon 12345 -o gc_trace.json

# Monitor for a specific duration with verbose output
gcmon 12345 -d 30 -v

# High-frequency monitoring
gcmon 12345 --output trace.json --rate 0.01
```

### run

Run a Python script or module with GC monitoring enabled.

**Important:** All options and arguments after `-s`/`--script` or `-m`/`--module` are passed verbatim to the target — they are **not** interpreted by gcmon. Place gcmon options before the target.

```bash
# Run a script
gcmon run -s my_script.py

# Run a module (like python -m)
gcmon run --stats --table-format md -m test test_gc -v

# Pass arguments to the script; everything after -s goes to the target
gcmon run -s benchmark.py --iterations 1000 --verbose

# Run a module with GC monitoring options
gcmon run --format jsonl -o trace.jsonl --stats -m http.server 8000
```

You must specify exactly one of `-s`/`--script` or `-m`/`--module`.

### Options

| Option | Applies to | Description | Default |
|--------|------------|-------------|---------|
| `pid` (required) | `monitor` | Process ID to monitor | - |
| `-s, --script <path>` | `run` | Python script path to run | - |
| `-m, --module <name>` | `run` | Module name to run (like `python -m`) | - |
| `-o, --output` | both | Output file path for trace data | `gcmon.json` (chrome), `gcmon.jsonl` (JSONL) |
| `-r, --rate` | both | Polling rate in seconds | `0.1` |
| `-d, --duration` | both | Monitoring duration in seconds | Until interrupted / script exits |
| `-v, --verbose` | both | Enable verbose output (`-v` for INFO, `-vv` for DEBUG) | `0` |
| `--format` | both | Output format: `chrome` (Chrome Trace Event), `jsonl` (JSONL to file), or `stdout` (JSONL to stdout) | `chrome` |
| `--flush-threshold` | both | Number of events to buffer before flushing (JSONL format) | `100` |
| `--stats` | both | Show statistics table at end of monitoring (see [Statistics](#statistics)) | `False` |
| `--table-format` | both | Table format: `plain` or `markdown`/`md` | `plain` |

### Environment Variables

All CLI options can be overridden via environment variables. CLI flags take precedence.

| Variable | Equivalent flag | Description | Default |
|----------|----------------|-------------|---------|
| `GCMON_OUTPUT` | `-o, --output` | Output file path for trace data | `gcmon.json` (chrome), `gcmon.jsonl` (JSONL) |
| `GCMON_RATE` | `-r, --rate` | Polling rate in seconds | `0.1` |
| `GCMON_DURATION` | `-d, --duration` | Monitoring duration in seconds | Until interrupted / script exits |
| `GCMON_VERBOSE` | `-v, --verbose` | Verbose level (integer or truthy value) | `0` |
| `GCMON_FORMAT` | `--format` | Output format: `chrome`, `jsonl`, or `stdout` | `chrome` |
| `GCMON_FLUSH_THRESHOLD` | `--flush-threshold` | Number of events to buffer before flushing (JSONL format) | `100` |
| `GCMON_STATS` | `--stats` | Enable statistics table (`1`, `true`, `yes`, `on`) | `False` |
| `GCMON_TABLE_FORMAT` | `--table-format` | Table format: `plain`, `md`, or `markdown` | `plain` |


## Statistics

Use `--stats` to display a statistics table at the end of monitoring. The table reports GC pause durations (p50, p90, p95, p99) and counts per generation, broken down by process.

### Example Output

```bash
$ gcmon 12345 --stats --table-format md

| PID   | Metric           | Count |     Sum |     Avg |     P50 |     P90 |     P95 |     P99 |
|-------|------------------|-------|---------|---------|---------|---------|---------|---------|
| Total | GC Pause(0)      |    42 |  35.200 |   0.838 |   0.720 |   1.500 |   1.800 |   2.400 |
|       | GC Pause(1)      |    18 |  72.000 |   4.000 |   3.500 |   6.800 |   7.500 |  10.200 |
|       | GC Pause(2)      |     5 | 125.000 |  25.000 |  22.000 |  38.000 |  42.000 |  50.000 |
|       |                  |       |         |         |         |         |         |         |
| 12345 | GC Pause(0)      |    42 |  35.200 |   0.838 |   0.720 |   1.500 |   1.800 |   2.400 |
|       | GC Pause(1)      |    18 |  72.000 |   4.000 |   3.500 |   6.800 |   7.500 |  10.200 |
|       | GC Pause(2)      |     5 | 125.000 |  25.000 |  22.000 |  38.000 |  42.000 |  50.000 |
```

*Values shown in milliseconds. Metrics are reported per GC generation (0, 1, 2).*

### Without `[stats]` extra

By default, statistics are computed from an in-memory buffer of up to 1024 samples. Percentiles are calculated exactly by sorting the buffered values. Once the buffer is full, older samples are discarded. This is sufficient for short monitoring sessions but may lose data during long runs.

### With `[stats]` extra

Install the optional `ddsketch` dependency for high-accuracy, memory-efficient statistics:

```bash
pip install gcmon[stats]
```

This installs [DDSketch](https://github.com/DataDog/sketches-py), which:
- Tracks **all** samples without a fixed buffer limit
- Computes approximate quantiles with 0.1% relative accuracy
- Uses constant memory regardless of monitoring duration

For long-running processes or high-frequency polling, the `[stats]` extra is recommended.


## Pyperf Hook Integration

The gcmon package provides a pyperf hook for automatic GC metrics collection during benchmarks.

### Usage

```bash
# Run benchmark with GC monitoring
python my_benchmark.py --hook=gcmon

# Or using pyperf directly
pyperf timeit --hook=gcmon my_benchmark.py

# Save results with GC metrics
python my_benchmark.py --hook=gcmon -o benchmark_results.json

```

### GC Metrics Collected

The hook collects and reports the following GC metrics in pyperf metadata:

- `gc_pause_gen_0_p99`, `gc_pause_gen_1_p99`, `gc_pause_gen_2_p99` - P99 GC pause duration by generation (microseconds)
- `gc_pause_gen_0_sum`, `gc_pause_gen_1_sum`, `gc_pause_gen_2_sum` - Total GC pause time by generation (microseconds)
- `gc_pause_gen_0_count`, `gc_pause_gen_1_count`, `gc_pause_gen_2_count` - Number of GC pauses by generation
- `gc_heap_size_p99` - P99 heap size across all samples (bytes)

## How It Works

For the pyperf hook integration, gcmon uses an **external process model**:

1. The hook spawns the `gcmon` CLI as a separate process
2. The external process reads the target process memory directly
3. Results are written to a temporary JSONL file
4. The hook reads the JSONL and injects metrics into pyperf metadata

This provides zero in-process overhead during benchmarks, crash isolation
(gcmon crashes don't affect the target), and clean separation of concerns.

### Example: Perfetto Trace Viewer for Pyperf Benchmarks

When you run a pyperf benchmark with the gcmon hook and export the results in Chrome Trace format, you can visualize the GC activity alongside the benchmark execution in Perfetto:

<img src="docs/images/perfetto-pyperf-example.png" alt="Perfetto Pyperf Example" width="800">

*Example: Pyperf benchmark trace visualized in Perfetto showing:*
- *Multiple benchmark worker processes running in parallel*
- *GC Monitor process tracking memory events*
- *Timeline view of benchmark execution with GC activity*

This visualization helps you:
- **Correlate GC activity with benchmark performance** - See how GC pauses affect benchmark timing
- **Identify performance outliers** - Spot runs affected by GC pauses
- **Analyze parallel benchmark execution** - Monitor multiple worker processes simultaneously
- **Debug benchmark variability** - Understand sources of timing variation between runs

To generate traces for Perfetto:
```bash
export GCMON_PYPERF_HOOK_OUTPUT="gcmon_{bench_name}.jsonl"
# Run benchmark with GC monitoring and JSONL output
python my_benchmark.py --hook=gcmon --inherit-environ=GCMON_PYPERF_HOOK_OUTPUT -p 5

# Open in Perfetto UI (https://ui.perfetto.dev)
```

`--inherit-environ` is needed because pyperf isolates worker environments by default;
it tells pyperf to pass `GCMON_PYPERF_HOOK_OUTPUT` from the parent shell to
worker subprocesses so the hook writes to the intended file.

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `GCMON_PYPERF_HOOK_OUTPUT` | Output path for the combined GC trace file (JSONL). Supports `{bench_name}` and `{pid}` substitution. | `gcmon_{bench_name}_combined_{pid}.jsonl` |
| `GCMON_PYPERF_HOOK_TEMP_DIR` | Directory for temporary JSONL files written during monitoring. | System temp directory |
| `GCMON_PYPERF_HOOK_VERBOSE` | Enable verbose logging from the hook. Accepts `1`, `yes`, `on`, or `true` (case-insensitive). | Disabled |

## License

MIT License - see [LICENSE](LICENSE) for details.

## Contributing

Bug reports and pull requests are welcome at [GitHub](https://github.com/sergey-miryanov/gcmon/issues).
