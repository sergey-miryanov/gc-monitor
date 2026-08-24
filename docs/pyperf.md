# Pyperf Hook Integration

gcmon ships a pyperf hook that marks where each benchmark ran in a trace you
are already recording, over the same
[external-process model](../README.md#how-it-works) as the CLI.

> **Prerequisite:** `pip install pyperf`. It finds the hook once `gcmon` is
> installed; pass `--hook=gcmon` to turn it on.

## Usage

Start one monitor over the whole suite and let the hook annotate its trace:

```bash
gcmon run -o suite.pftrace -- \
    python my_benchmark.py --hook=gcmon \
    --inherit-environ=GCMON_CONTROL_ADDRESS
```

`gcmon run` puts the control address in the environment, and pyperf isolates
its workers from it, so `--inherit-environ=GCMON_CONTROL_ADDRESS` is what
carries it through to the process the hook runs in. Without it the hook has
nothing to talk to.

pyperf's own runners take the same flags:

```bash
gcmon run -o suite.pftrace -- \
    pyperf timeit --hook=gcmon --inherit-environ=GCMON_CONTROL_ADDRESS \
    my_benchmark.py
```

## When No Monitor Is Listening

The hook refuses the run on the first worker rather than finishing a suite
that recorded nothing. pyperf prints the message and exits 1:

```
ERROR setting up hook 'gcmon':
gcmon: no monitor is listening on GCMON_CONTROL_ADDRESS. Start one over the
whole run, `gcmon run -o suite.pftrace -- <your benchmark> --hook=gcmon
--inherit-environ=GCMON_CONTROL_ADDRESS`, and pyperf will carry the address
through to its workers.
```

Two things produce it: running without `gcmon run`, and running under it
without `--inherit-environ=GCMON_CONTROL_ADDRESS`. The second is the one that
catches people, because the runner process can reach the monitor and its
workers cannot. `GCMON_PYPERF_HOOK_CONTROL_TIMEOUT` bounds how long the hook
waits before it gives up.

## What the Hook Writes

Two instants per measured region, on the worker's own process:

```
gcmon:<benchmark>:<n>:begin
gcmon:<benchmark>:<n>:end
```

`<benchmark>` is pyperf's name for it, with anything outside `[A-Za-z0-9_-]`
replaced by `_`. `<n>` counts regions within one worker process, so a worker
that runs warmups and then values numbers them in the order they ran: under
`--warmups=1 --values=3`, region 1 is the warmup and regions 2 through 4 are
the values.

The `gcmon:` prefix is reserved, so `name LIKE 'gcmon:%'` selects marks and
nothing else. See [Perfetto SQL](perfetto-sql.md) for querying a trace.

The hook adds nothing to pyperf's metadata. It spawns no process, writes no
file, and computes no statistics.

## Why the Hook Marks Instead of Monitoring

It used to start a `gcmon monitor` of its own around each benchmark and
publish `gc_*` keys into pyperf's metadata. Four things are better this way.

**A suite costs one monitor.** pyperf builds a hook inside each measurement
phase, and a phase runs twice per process, once for warmups and once for
values. A sixty-benchmark suite at `-p 5` was on the order of six hundred
monitor processes, each attaching and writing a file of its own.

**Nothing of the hook's runs while the benchmark does.** It reads a clock at
each end of the region and sends both instants afterwards, when pyperf hands
it the benchmark name. No I/O happens between those two reads.

**The old numbers covered more than the benchmark.** Stopping a monitor stops
the reading, not the collecting: the target kept collecting through every gap,
and the cumulative counters those numbers were reconstructed from span the
gaps whole. `gc_pause_gen_N_sum` was the pause over a window wider than the
benchmark by every gap in it.

**A marked region can be narrowed afterwards.** A gated one is fixed when the
run ends, and a benchmark cannot be re-run to change your mind about where its
boundaries were. Everything outside the marks is still in the trace, so what
to count is a decision you make later.

## Perfetto Traces from a Pyperf Run

<img src="https://raw.githubusercontent.com/sergey-miryanov/gcmon/main/docs/images/perfetto-pyperf-example.png" alt="Perfetto Pyperf Example" width="800">

*A pyperf run in Perfetto: several benchmark workers in parallel, the gcmon
process beside them, and each worker's GC activity on its own tracks.*

```bash
gcmon run -o suite.pftrace -- \
    python my_benchmark.py --hook=gcmon -p 5 \
    --inherit-environ=GCMON_CONTROL_ADDRESS
```

Open `suite.pftrace` in [Perfetto UI](https://ui.perfetto.dev).

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `GCMON_PYPERF_HOOK_VERBOSE` | Enable verbose logging from the hook. Accepts `1`, `yes`, `on`, or `true` (case-insensitive). | Disabled |
| `GCMON_PYPERF_HOOK_CONTROL_TIMEOUT` | Timeout (seconds) for the hook to connect to the control plane. | `10.0` |

`GCMON_CONTROL_ADDRESS` is set by `gcmon run` and `gcmon monitor`, not by you.
Pass it through `--inherit-environ` so the workers see it.
