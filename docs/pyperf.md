# Pyperf Hook Integration

gcmon ships a pyperf hook that marks where each benchmark ran in the trace a
running monitor is already writing. The hook is the only gcmon code inside the
benchmark process; the monitor reads that process from outside
([How it works](../README.md#how-it-works)).

> **Prerequisite:** `pip install pyperf`. pyperf finds the hook once gcmon is
> installed; pass `--hook=gcmon` to turn it on.

## Usage

Start one monitor over the whole suite:

```bash
gcmon run -o suite.pftrace -s my_benchmark.py \
    --hook=gcmon --inherit-environ=GCMON_CONTROL_ADDRESS
```

`gcmon run` takes its target as `-s <script>` or `-m <module>` and passes
everything after it to the target untouched, pyperf's flags included:

```bash
gcmon run -o suite.pftrace -m pyperf timeit \
    --hook=gcmon --inherit-environ=GCMON_CONTROL_ADDRESS \
    "sum(range(100))"

gcmon run -o suite.pftrace -m pyperformance run \
    --hook=gcmon --inherit-environ=GCMON_CONTROL_ADDRESS
```

`gcmon run` sets `GCMON_CONTROL_ADDRESS` for the script it starts. That script
is pyperf's runner, and the hook runs a level down, inside each worker the
runner spawns:

```
gcmon run  ->  your script (pyperf runner)  ->  worker (the hook)
```

pyperf passes a worker a fixed set of environment variables, plus the ones you
name in `--inherit-environ`. Without the flag the address stops at the runner,
and the first worker cannot connect.

## When No Monitor Is Listening

One worker that cannot reach a monitor stops the whole run. pyperf prints the
message and exits 1:

```
ERROR setting up hook 'gcmon':
gcmon: no monitor is listening on GCMON_CONTROL_ADDRESS. Start one over the
whole run: `gcmon run -o suite.pftrace -s my_benchmark.py --hook=gcmon
--inherit-environ=GCMON_CONTROL_ADDRESS`, or -m for a module. pyperf carries
the address through to its workers from there.
```

Two things produce it: no `gcmon run` at all, or `gcmon run` without
`--inherit-environ=GCMON_CONTROL_ADDRESS`. The second is easy to miss, because
the runner process reaches the monitor and the workers do not. The hook waits
`GCMON_PYPERF_HOOK_CONTROL_TIMEOUT` seconds before printing this.

## What the Hook Writes

Two instants per measured region, on the worker's process track:

```
gcmon:<benchmark>:<n>:<i>:begin
gcmon:<benchmark>:<n>:<i>:end
```

`<benchmark>` is the name pyperf reports, with anything outside
`[A-Za-z0-9_-]` replaced by `_`.

`<n>` counts regions across the whole worker process, in the order they ran.
`<i>` counts them within one measurement phase and restarts at 1 when pyperf
begins the next. The restart is the boundary between warmups and values: under
`--warmups=1 --values=3` a worker writes `<n>` 1 through 4, with `<i>` going
1, then 1, 2, 3.

The `gcmon:` prefix is reserved: `name LIKE 'gcmon:%'` selects marks and
nothing else. See [Perfetto SQL](perfetto-sql.md) for querying a trace.

The hook writes nothing else: no metadata key, no file of its own.

## Why the Hook Marks Instead of Monitoring

The hook used to start a `gcmon monitor` of its own around each benchmark and
publish `gc_*` keys into pyperf's metadata.

**A suite costs one monitor.** pyperf builds a hook inside each measurement
phase, and a phase runs twice per process, once for warmups and once for
values. A sixty-benchmark suite at `-p 5` was on the order of six hundred
monitor processes, each attaching and writing a file of its own.

**The hook does nothing while the benchmark runs.** It reads a clock at each
end of the region and sends both instants afterwards, when pyperf hands it the
benchmark name. It does no I/O between them.

**The old numbers covered more than the benchmark.** Stopping a monitor stops
the reading, not the collecting: the target kept collecting through every gap,
and gcmon reconstructed those numbers from cumulative counters that span the
gaps whole. `gc_pause_gen_N_sum` was the pause over a window wider than the
benchmark by every gap in it.

**You pick the window when you read the trace.** The old hook fixed it during
the run, by stopping and starting the monitor, and a different window meant
running the suite again. A marked run leaves everything in the trace: the
benchmark, pyperf's bookkeeping between values, and the interpreter starting
up.

## Perfetto Traces from a Pyperf Run

<img src="https://raw.githubusercontent.com/sergey-miryanov/gcmon/main/docs/images/perfetto-pyperf-example.png" alt="Perfetto Pyperf Example" width="800">

*A pyperf run in Perfetto: several benchmark workers in parallel, the gcmon
process beside them, and each worker's GC activity on its own tracks.*

```bash
gcmon run -o suite.pftrace -s my_benchmark.py \
    --hook=gcmon -p 5 --inherit-environ=GCMON_CONTROL_ADDRESS
```

Open `suite.pftrace` in [Perfetto UI](https://ui.perfetto.dev).

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `GCMON_PYPERF_HOOK_VERBOSE` | Enable verbose logging from the hook. Accepts `1`, `yes`, `on`, or `true` (case-insensitive). | Disabled |
| `GCMON_PYPERF_HOOK_CONTROL_TIMEOUT` | Timeout (seconds) for the hook to connect to the control plane. | `10.0` |

`gcmon run` sets `GCMON_CONTROL_ADDRESS`, and `--inherit-environ` passes it on
to the workers ([Usage](#usage)). That is the hook's only route to the
monitor, and `gcmon monitor` cannot offer it: it attaches to a process already
running, whose environment is fixed by then.
