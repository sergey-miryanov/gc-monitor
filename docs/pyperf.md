# Pyperf Hook Integration

gcmon ships a pyperf hook that marks where each benchmark ran, over the same
[external-process model](../README.md#how-it-works) as the CLI.

> **Prerequisite:** `pip install pyperf`. It finds the hook once `gcmon` is
> installed; pass `--hook=gcmon` to turn it on.

## Usage

```bash
python my_benchmark.py --hook=gcmon

# pyperf's own runners take the flag too
pyperf timeit --hook=gcmon my_benchmark.py
```

The hook publishes no GC metrics into pyperf's metadata. It writes a begin and
an end instant per measured region into the trace instead, and the numbers for
a region are read off that trace.

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
