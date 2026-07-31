# Pyperf Hook Integration

The gcmon package provides a pyperf hook for automatic GC metrics collection during benchmarks. The hook uses the same [external-process model](../README.md#how-it-works) as the CLI.

> **Prerequisite:** install [pyperf](https://pypi.org/project/pyperf/) first (`pip install pyperf`). pyperf auto-discovers the hook once `gcmon` is installed; pass `--hook=gcmon` to enable it for a benchmark.

## Usage

```bash
# Run benchmark with GC monitoring
python my_benchmark.py --hook=gcmon

# Or using pyperf directly
pyperf timeit --hook=gcmon my_benchmark.py

# Save results with GC metrics
python my_benchmark.py --hook=gcmon -o benchmark_results.json
```

## GC Metrics Collected

The hook collects and reports the following GC metrics in pyperf metadata:

- `gc_pause_gen_0_p99`, `gc_pause_gen_1_p99`, `gc_pause_gen_2_p99` - P99 GC pause duration by generation (milliseconds)
- `gc_pause_gen_0_sum`, `gc_pause_gen_1_sum`, `gc_pause_gen_2_sum` - Total GC pause time by generation (milliseconds)
- `gc_pause_gen_0_count`, `gc_pause_gen_1_count`, `gc_pause_gen_2_count` - Number of GC pauses by generation
- `gc_pause_count` - Total number of recorded GC pauses across all generations and monitored processes
- `gc_heap_size_p99` - P99 across the per-process peak live object counts

## Example: Perfetto Trace Viewer for Pyperf Benchmarks

When you run a pyperf benchmark with the gcmon hook, you can visualize the GC activity alongside the benchmark execution in Perfetto:

<img src="https://raw.githubusercontent.com/sergey-miryanov/gcmon/main/docs/images/perfetto-pyperf-example.png" alt="Perfetto Pyperf Example" width="800">

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

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `GCMON_PYPERF_HOOK_OUTPUT` | Output path for the combined GC trace file (JSONL). Supports `{bench_name}` and `{pid}` substitution. | `gcmon_{bench_name}_combined_{pid}.jsonl` |
| `GCMON_PYPERF_HOOK_TEMP_DIR` | Directory for temporary JSONL files written during monitoring. | System temp directory |
| `GCMON_PYPERF_HOOK_VERBOSE` | Enable verbose logging from the hook. Accepts `1`, `yes`, `on`, or `true` (case-insensitive). | Disabled |
| `GCMON_PYPERF_HOOK_CONTROL_TIMEOUT` | Timeout (seconds) for the hook to connect to the control plane. | `10.0` |
