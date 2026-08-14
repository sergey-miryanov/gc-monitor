# Pyperf Hook Integration

gcmon ships a pyperf hook that collects GC metrics during benchmarks, over the
same [external-process model](../README.md#how-it-works) as the CLI.

> **Prerequisite:** `pip install pyperf`. It finds the hook once `gcmon` is
> installed; pass `--hook=gcmon` to turn it on.

## Usage

```bash
python my_benchmark.py --hook=gcmon

# pyperf's own runners take the flag too
pyperf timeit --hook=gcmon my_benchmark.py

# The metrics land in the saved JSON
python my_benchmark.py --hook=gcmon -o benchmark_results.json
```

## GC Metrics Collected

In pyperf metadata, with `N` the generation, 0 through 2:

- `gc_pause_gen_N_p99` - P99 pause, in milliseconds
- `gc_pause_gen_N_sum` - total pause time, in milliseconds
- `gc_pause_gen_N_count` - pauses counted
- `gc_pause_count` - the same count over every generation and process
- `gc_pause_gen_N_coverage` - the share of that generation's records gcmon read,
  in `[0, 1]`
- `gc_pause_gen_N_lifetime_count`, `gc_pause_gen_N_lifetime_sum` - runs and
  pause time since the *interpreter* started
- `gc_heap_size_p99` - P99 across the per-process peak live object counts

### `sum` and `count` are exact, `p99` is sampled

> **Breaking change.** `gc_pause_gen_N_sum`, `gc_pause_gen_N_count` and
> `gc_pause_count` counted the GC runs gcmon read. They now count every run in
> the monitored window, reconstructed from CPython's cumulative counters, so
> they read higher. Do not trend a history that spans this change.

A benchmark whose collector runs faster than gcmon polls loses records; see
[How gcmon reads a process](monitoring.md). `sum` and `count` correct for that
exactly. `p99` reads high, since a long GC run leaves its record in the ring
slot for longer, where it is likelier to survive to the next poll.

`gc_pause_gen_N_coverage` tells you how far to trust the `p99` beside it: at
`1.0` it is the real distribution, at `0.2` it is the tail of a biased sample.
See [Statistics](statistics.md#percentiles-are-sampled-and-read-high) for why no
scale factor fixes a quantile.

### The lifetime metrics are not benchmark-scoped

`gc_pause_gen_N_lifetime_count` and `_lifetime_sum` cover the interpreter's
whole history, including whatever ran before the hook attached. They answer what
this process spent in GC. What this benchmark cost is a different question.

Trend them the way you would trend `sum` and you measure how long each worker
lived. Use `gc_pause_gen_N_sum` for the benchmark's own share.

## Perfetto Traces from a Pyperf Run

<img src="https://raw.githubusercontent.com/sergey-miryanov/gcmon/main/docs/images/perfetto-pyperf-example.png" alt="Perfetto Pyperf Example" width="800">

*A pyperf run in Perfetto: several benchmark workers in parallel, the gcmon
process beside them, and each worker's GC activity on its own tracks.*

```bash
export GCMON_PYPERF_HOOK_OUTPUT="gcmon_{bench_name}.jsonl"
python my_benchmark.py --hook=gcmon --inherit-environ=GCMON_PYPERF_HOOK_OUTPUT -p 5
```

pyperf isolates worker environments, so `--inherit-environ` is how
`GCMON_PYPERF_HOOK_OUTPUT` reaches the workers. Open the result in
[Perfetto UI](https://ui.perfetto.dev).

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `GCMON_PYPERF_HOOK_OUTPUT` | Output path for the combined GC trace file (JSONL). Supports `{bench_name}` and `{pid}` substitution. | `gcmon_{bench_name}_combined_{pid}.jsonl` |
| `GCMON_PYPERF_HOOK_TEMP_DIR` | Directory for temporary JSONL files written during monitoring. | System temp directory |
| `GCMON_PYPERF_HOOK_VERBOSE` | Enable verbose logging from the hook. Accepts `1`, `yes`, `on`, or `true` (case-insensitive). | Disabled |
| `GCMON_PYPERF_HOOK_CONTROL_TIMEOUT` | Timeout (seconds) for the hook to connect to the control plane. | `10.0` |
