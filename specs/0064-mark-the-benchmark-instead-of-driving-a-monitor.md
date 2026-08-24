# 0064: Mark the benchmark in the trace instead of driving a monitor

- **Status:** Not started
- **Kind:** feature (enhancement)
- **Effort:** M
- **Origin:** design session 2026-08-23 on the pyperf hook, from two
  complaints about it: that it runs a monitor process of its own, and that it
  starts and stops one
- **Respects:** [ADR-0006](../docs/adr/0006-begin-end-slice-pairs.md) (a
  duration is a begin/end pair in both backends; a mark is two instants
  instead, and section 4 says why that shape is deferred rather than
  rejected), [ADR-0009](../docs/adr/0009-nanoseconds-canonical-time-unit.md)
  (nanoseconds inside gcmon),
  [ADR-0011](../docs/adr/0011-process-lifetime-and-ordering.md) (ordering by
  timestamp, under which a mark landing seconds late is already normal)

## 1. Problem statement

Someone benchmarking a change to the collector runs a pyperformance suite with
`--hook=gcmon` and wants to know what GC cost each benchmark. Three things go
wrong, and the third is the one they notice.

**gcmon starts a monitor process for every measurement phase of every
worker.** pyperf instantiates its hooks inside `Runner._compute_values`, and
`compute_warmups_values` calls that twice, once for warmups and once for
values (pyperf 2.10.0). Each instantiation spawns `gcmon monitor`, attaches to
the worker, opens a control pipe and writes its own temporary JSONL. A
sixty-benchmark suite at `-p 5` is on the order of six hundred monitors, and
`GCMonitorHook.teardown` opens the same output path with `"wb"` for each of
them, so the second silently overwrites the first.

**Starting and stopping the monitor does not scope anything.** The hook stops
it between values. `ControlServer._handle_msg` records the pid as disabled and
`EventsMonitor.tick` skips polling it, but the target keeps collecting: the
records made during the gap are still in the ring when polling resumes, and
`_replay` folds them in with the rest. Worse,
`StreamingStats.observe_cumulative` reads a cumulative counter whose span
covers the gap whole, so the exact figures include every collection between
two values. `docs/pyperf.md` says `gc_pause_gen_N_sum` counts "every run in
the monitored window", and the monitored window is wider than the benchmark by
every gap in it. The control plane offers the same gate to every other caller
under the same false claim. That fault belongs to the control plane and gets a
spec of its own; here the hook stops relying on it.

**Nothing says where a benchmark ran.** The trace of a suite holds every
worker's GC activity and no way to tell a benchmark's own region from the
interpreter starting up, importing its dependencies, calibrating, or pyperf's
bookkeeping between values. That is the question the operator came with, and
it is the one thing the hook does not answer.

## 2. Solution

`--hook=gcmon` marks the trace and stops monitoring it.

The operator starts one monitor over the whole suite, the way they would over
any other process tree:

```
gcmon run -o suite.pftrace -m pyperformance run --hook=gcmon ...
```

The hook, inside each worker, records when the benchmark function started and
stopped and writes those instants into that one trace. Opening `suite.pftrace`
then shows each worker's GC activity with the benchmark's own region marked on
it, and a run costs one monitor instead of six hundred.

The hook does nothing else. It spawns no process, starts and stops nothing,
writes no file, computes no statistics, and adds nothing to pyperf's metadata.
`gc_pause_gen_N_*`, `gc_pause_count` and `gc_heap_size_p99` are gone.

Because it only annotates, it refuses to run where there is nothing to
annotate: without a monitor it fails on the first worker with a message naming
the `gcmon run` that was missed, rather than completing a suite and producing
nothing.

## 3. User stories

1. As someone benchmarking a collector change, I want one monitor over the
   suite rather than one per measurement phase, so that a sixty-benchmark run
   does not spawn six hundred processes to watch itself.
2. As someone opening the trace afterwards, I want to see where each benchmark
   ran, so that I do not attribute interpreter startup and imports to it.
3. As someone reading a warmup value that looks wrong, I want the marks
   numbered, so that I can tell the first region from the fourth.
4. As someone who forgot `gcmon run`, I want the run to fail on the first
   worker, so that I do not spend an hour producing a trace with nothing in
   it.
5. As someone measuring a benchmark, I want the hook to do no I/O while the
   benchmark is running, so that the thing observing it is not also disturbing
   it.
6. As someone deciding where a benchmark's boundaries were, I want the region
   marked rather than cut out while the run is happening, so that changing my
   mind about it does not mean running the suite again.
7. As someone whose worker was killed mid-benchmark, I want a trace that says
   nothing about that benchmark rather than half of it, so that a partial
   region never reads as a complete one.
8. As a gcmon maintainer, I want the hook to hold no statistics code, so that
   there is one path from records to a table and not two.
9. As a gcmon maintainer, I want the mark's grammar written and read in one
   module, so that the writer and the reader cannot drift.

## 4. Implementation decisions

**The hook accumulates and lands.** `__enter__` reads `time.monotonic_ns()`.
`__exit__` reads it again and appends the pair. `teardown` sends them. Nothing
crosses a process boundary while the benchmark is running. The current
`__enter__` calls `ControlClient.start_monitoring`, whose first call blocks on
`connect_with_retry` for up to `GCMON_PYPERF_HOOK_CONTROL_TIMEOUT`. That block
does not disappear; it moves: the refusal below connects in `__init__`, before
a benchmark is running and outside anything pyperf times.

pyperf forces this shape. `instantiate_selected_hooks` calls the entry point
with no arguments and `HookBase.__enter__` takes none, so the hook does not
know which benchmark it is running until `teardown(metadata)`, where
`Runner.compute` has already set `metadata['name']`.

`teardown` reads `metadata['name']` and writes nothing back. No `gc_*` keys,
and no other key either.

**Timestamps travel with the message.** `ControlClient._send` stamps `ts` with
`time.monotonic_ns()` at send time, so `instant_msg` gains a `ts` parameter
and `_send` uses it when given. The server side already honours whatever the
client says: `ControlMsg` carries `ts` and `ControlServer._handle_msg` passes
it straight to `_add_event`. That parameter is the only change this spec makes
to the control plane, and it is additive: a caller that omits it gets what it
gets today.

That the two clocks are comparable is the assumption everything rests on.
`time.monotonic` is system-wide on both Windows and Linux, and CPython stamps
a GC record from the same clock; on 3.15.0b4, `monotonic_ns`,
`perf_counter_ns` and a record's `ts_stop` agreed. It belongs in a test,
because everything downstream fails silently if it ever stops being true.

Marks therefore reach the exporter out of order with respect to records, by
seconds rather than milliseconds. That is already a normal condition: ADR-0011
records that a freshly discovered child's first GC event can predate gcmon
ever polling it, and the trace processor sorts by timestamp, breaking ties by
position only where timestamps are equal.

**The mark is a point, not a span.** Two instants,
`gcmon:<bench>:<n>:<i>:begin` and `gcmon:<bench>:<n>:<i>:end`, carried by the
existing `InstantMsg`, which `JsonlExporter.add_instant_event` already writes
and the trace converter already draws.

ADR-0006 would make a duration a begin/end slice pair, and that is the better
shape: it pairs in the model rather than in a string, and a reader finds a
region without matching two names. It waits on a new track and a decision
about where that track sits in the hierarchy, a larger change than this spec
is for. When someone takes it, the grammar module below is the only thing that
has to know.

**The grammar is colon-delimited, with a reserved prefix, and lives in one
module with a parser beside the formatter.** `<bench>` is `metadata['name']`
through the `re.sub(r"[^a-zA-Z0-9_-]", "_", ...)` the hook already applies, so
it can hold no colon. A reserved `gcmon:` prefix makes `name LIKE 'gcmon:%'` a
usable predicate for anyone writing against `docs/perfetto-sql.md`, and leaves
room for a fifth field later.

Rejected: recovering the benchmark name by running a `bm_[A-Za-z0-9_]+` regex
over the hook's own `sys.argv`. It would put a second copy of spec 0062's
sanitizer inside the hook, in a different language, with nothing keeping the
two in step.

**`<n>` counts regions in the process, not in the hook instance.** A worker
builds one hook per `_compute_values` call and both teardowns read the same
`metadata['name']`, so an instance-scoped counter would emit
`gcmon:bm_base64:1:begin` twice in one process, meaning different things. A
The hook also counts its own regions, and `<i>` restarting is where one
measurement phase ended and the next began. That is what the process-wide
count cannot say, since it runs straight through both. Which phase is the
warmups still comes from the command line: `--warmups=1 --values=3` says the
first phase holds the warmup and the second holds the three values.

**No monitor is a refusal, not a warning.** `GCMonitorHook.__init__` connects
eagerly and raises `pyperf._hooks.HookError`, which
`instantiate_selected_hooks` catches to print one message and exit 1. This is
what `pyperf._hooks.pystats` does to refuse a build without
`--enable-pystats`. Failing on the first worker costs a second; the
alternative costs a suite, because a missing `GCMON_CONTROL_ADDRESS` makes
`ControlClient._ensure_connected` return without calling the factory at all,
so every send is a silent no-op.

`HookError` is not public: `pyperf.__init__.__all__` does not carry it. The
hook therefore imports it inside the function and falls back to a plain
exception if `pyperf._hooks` ever moves, which still fails the run rather than
losing it. Keeping it lazy also lets `tests/pyperf/` construct a hook with
pyperf absent.

**The hook does not monitor its own process.** `_remote_debugging.GCMonitor`
takes a pid and accepts the caller's own, which makes an in-process hook
available rather than impossible: it would read its own ring, keep its own
statistics, and need no second process anywhere. The cost rules it out. A
free-threaded build sizes the ring at one record per generation, and keeping
up with it means a thread polling hard enough that every read takes the GIL
inside the process being benchmarked. gcmon reads from outside for that
reason, and `--rate` spends the monitor's time and not the target's.

**The hook stops driving the monitor and leaves the gate where it is.**
`__enter__` and `__exit__` stop calling `start_monitoring` and
`stop_monitoring`, and nothing else about the control plane moves: the three
gating calls stay on `ControlClient`, the `start` and `stop` arms stay in
`ControlServer._handle_msg` with `_enabled` and `is_enabled`, and
`EventsMonitor` keeps `is_pid_enabled` and the branch in `tick` that reads it.
Withdrawing a documented API is a decision about the control plane rather than
about the hook; it gets the spec named in section 6, and this one only stops
being that API's last in-tree caller.

Marking would win against a working gate. Repairing this one means polling at
the stop, keeping the counters, polling at the start, subtracting the
difference, and dropping whatever the ring holds on resume, so the observed
span becomes a set of intervals. Even then it loses to bracketing. Gating
destroys the gap's records while still counting their cost; marking keeps
everything, opens no loss window, costs the target nothing, and lets the
exclusion be decided after the run, which matters because a benchmark cannot
be re-run to change your mind about where its boundaries were. The one thing
gating offers that marking does not is saving gcmon's own polling cost, and
gcmon is external, so that cost is not the target's.

**Nothing needs to poll at a mark.** Every GC record carries the target's
cumulative counters at its own timestamp, which is what `observe_cumulative`
reads. For any interval, the first record at or after the begin mark and the
last at or before the end mark differ by the collections between them: an
exact count and an exact pause sum, with CONTEXT.md's **observed span**
narrowed from the ring's to the region's. A reader gets both a sampled and an
exact side without the monitor having polled at the boundary, so `--rate`
bounds nothing here.

**What the hook stops carrying.** `src/gcmon/pyperf/metrics.py` loses its only
caller and goes, with `tests/pyperf/test_metrics.py` and the `to_metrics`
benchmark in `tests/benchmarks/test_bench_stats.py`. The subprocess, the
temporary directory, `--control-name` and `GCMonitorHook._build_command` go,
and with them the hook's calls into `support.process_terminator`, which stays
for `ChildProcessRunner`. `GCMON_PYPERF_HOOK_OUTPUT` and
`GCMON_PYPERF_HOOK_TEMP_DIR` go; `GCMON_PYPERF_HOOK_VERBOSE` and
`GCMON_PYPERF_HOOK_CONTROL_TIMEOUT` stay. The entry point takes no arguments,
matching how pyperf calls it.

`_replay` moves out of `pyperf/hook.py` under spec 0061, which currently
justifies the move as letting the hook and the offline path share one
implementation. After this the hook has no statistics at all, so the move
needs a different reason, and 0061's section 4 gets that one-line correction
when this lands.

## 5. Seams and testing decisions

- **Seam:** `ControlServer` with a recording exporter, driven by a real
  `ControlClient`. It is the highest seam that observes a mark end to end
  without a monitor, a target or pyperf, and `tests/control/` already works
  this way. The grammar module is tested beneath it.
- **New seam needed:** the grammar module, holding the formatter and the
  parser. Nothing existing formats or reads an instant's name.
- **What makes a good test here:** the marks arriving with the timestamps
  taken at `__enter__` and `__exit__`, not the ones at send time.
  Accumulate-and-land turns on it, and a test asserting only "four instants
  arrived" passes without it.
- **Prior art:** `tests/control/test_control_server.py` for the server with a
  mock exporter, `tests/exporters/test_perfetto_exporter_integration.py` for a
  trace-processor assertion on an instant, `tests/pyperf/` for the hook.
- **Cases:**
  1. Two regions accumulated across `__enter__` and `__exit__` land as four
     instants at `teardown`, carrying the enter and exit timestamps.
  2. The counter does not restart: a second hook instance in one process
     numbers its first region after the last one the first instance produced,
     rather than starting at `1` again.
  3. `teardown` adds no key to the metadata dict it is handed, and the dict
     compares equal to the one passed in.
  4. No `GCMON_CONTROL_ADDRESS` raises the refusal, and so does an address
     nobody is listening on.
  5. The grammar round-trips, and a separate case pins the literal string
     `gcmon:bm_base64:1:begin`. The round trip alone reads a value back
     through the constant it wrote with, so it passes on a changed separator;
     the pinned string is what makes a grammar change a visible diff.
  6. A worker whose `__exit__` never runs lands nothing for that region.
  7. The marks reach a Perfetto trace as instants on the right process,
     asserted through the trace processor rather than through the encoder.
  8. Regression guard: with no hook in play, `gcmon run` and `gcmon monitor`
     write what they wrote before; a control client sending a plain
     `instant_msg` with no `ts` still stamps it at send time; and the gating
     tests in `tests/monitoring/test_monitor.py` are untouched and still pass.

## 6. Out of scope

- **Reading the marks.** Clipping a table to a region, a region level beside
  spec 0062's workload level, and comparing regions across two files all wait
  for a spec of their own, after 0061 exists. Until then the marks land in
  traces that nothing but the Perfetto UI reads.
- **Removing the start/stop gate.** `start_monitoring`, `stop_monitoring` and
  `pause_monitoring`, the `start` and `stop` arms with `_enabled` and
  `is_enabled`, and `EventsMonitor`'s `is_pid_enabled` all survive this spec.
  After it the hook calls none of them and nothing in tree does. The removal
  earns a spec: it breaks a documented API, it takes `docs/control-plane.md`
  and `examples/ctrl.py` with it, and it needs an amendment to ADR-0011, which
  reasons about a pid the control server suppresses mid-run getting one
  continuous span across the gap.
- **Drawing a region as a slice.** ADR-0006's shape is the right one and it
  needs a track of its own; see section 4.
- **Restoring the pyperf metadata.** It is dropped, and the reader spec
  replaces it. Adding a reduced version here would ship a second statistics
  path for the life of one spec.
- **A general accumulate-and-land in `ControlClient`.** The pattern stays in
  the hook. Moving it down is worth doing once a second caller wants it, and
  `docs/control-plane.md` describes the pattern in the meantime.
- **Polling on a mark.** Impossible under accumulate-and-land, and unnecessary
  for the reason in section 4.
- **Monitoring from inside the worker.** An in-process hook is available and
  costs the benchmark the GIL; see section 4.
- **Making the hook work outside `gcmon run`.** Spawning a monitor when none
  is listening is the design being removed.
- **Labelling a mark a warmup or a value.** `is_warmup` is a local in
  `_compute_values` and never reaches a hook, so no mark can carry the word.
  `<i>` says where the phases divide and the command line says which is which;
  see section 4.

## 7. Further notes

This is a breaking change and earns a `CHANGELOG.md` entry under
`Breaking changes`: `gc_pause_gen_N_*`, `gc_pause_count` and
`gc_heap_size_p99` leave pyperf's metadata, and the hook stops working
anywhere there is no monitor already running.

`docs/pyperf.md` loses both metrics sections and gains the `gcmon run`
invocation and the mark grammar. It also gains a section on why the hook marks
a region instead of monitoring one. An operator upgrading finds their `gc_*`
keys gone and a hook that fails without `gcmon run`, and this page is where
they look. Four facts answer them: a suite costs one monitor rather than one
per measurement phase; the hook does no I/O while the benchmark is running;
the numbers the hook used to publish covered a window wider than the benchmark
by every gap in it, because stopping the monitor stopped the reading and not
the collecting; and a region bounded by marks can be narrowed after the run,
where one bounded by a gate is fixed when the run ends.

Landing this earns an ADR: why a hook that annotates beats one that drives,
why a repaired gate still loses to bracketing, and why the monitor stays
outside a process that could read itself. The page keeps the four facts and
names no ADR; both stand alone.

`docs/control-plane.md` gains the `ts` parameter on `instant_msg` and loses
nothing; its "Focus on specific phases" use case is now better served by
marking and filtering than by gating, which is where the spec named in section
6 starts.

`CONTEXT.md` gains **mark**: one instant a workload wrote into a trace to say
where it was, as against an **event**, which is what gcmon wrote from a
record. The word for the interval between two marks waits for the spec that
teaches something to read them, since naming it before then would put a term
in the glossary that nothing implements.

Nothing here depends on another spec, and 0061 needs the one-line correction
named in section 4.
