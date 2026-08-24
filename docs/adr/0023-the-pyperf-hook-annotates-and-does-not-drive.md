# ADR-0023: Mark the benchmark from the pyperf hook, and drive nothing

- **Status:** Accepted
- **Date:** 2026-08-24

## Context

`--hook=gcmon` used to run gcmon for you. pyperf builds a hook inside each
measurement phase and a phase runs twice per worker process, once for warmups
and once for values, so each build spawned a `gcmon monitor`, attached it to
the worker, opened a control pipe and wrote a temporary capture of its own. A
sixty-benchmark suite at `-p 5` is on that order of six hundred monitors, and
each one reopened the same output path for writing, so all but the last were
overwritten.

Around the benchmark the hook started and stopped the monitor through the
control plane. Stopping suppresses gcmon's polling of that pid and nothing
else: the target keeps collecting, the records made during the gap are still
in the ring when polling resumes, and the cumulative counters a capture is
reconstructed from span the gap whole. The numbers the hook published were
therefore the pause over a window wider than the benchmark by every gap in it,
under a documented claim that they covered the monitored window.

What the operator wanted from all this was one thing the hook did not produce:
a way to tell a benchmark's own activity from the interpreter starting up,
importing, calibrating, and pyperf's bookkeeping between values.

## Decision

- **The hook annotates a trace somebody else is recording.** It spawns no
  process, writes no file, computes no statistics, and adds no key to pyperf's
  metadata. The operator runs the suite under `gcmon run`, and the hook
  reaches that monitor through `GCMON_CONTROL_ADDRESS`.
- **A benchmark's extent is two instants, not a gate.** The hook writes a
  begin and an end mark per measured region, under a grammar reserved to
  gcmon: `gcmon:<benchmark>:<n>:begin` and `:end`. Everything outside a region
  stays in the trace.
- **Marks are captured in the region and sent after it.** The hook reads a
  clock at each end and holds the pair; `ControlClient.instant_msg` takes the
  captured timestamp, so the send can happen at teardown, which is the first
  moment pyperf names the benchmark. No I/O of gcmon's runs between the two
  reads.
- **`<n>` counts regions in the worker process.** Two hook instances in one
  worker are handed the same benchmark name, so a counter scoped to the
  instance would write one mark name twice meaning two different things.
- **No monitor is a refusal.** Building a hook connects, and raises pyperf's
  own `HookError` where nothing answers, which pyperf catches to print one
  message and exit. Failing on the first worker costs a second; the
  alternative costs a suite, because a control client with nowhere to send
  makes every send a silent no-op.
- **The grammar is written and read in one module,**
  `src/gcmon/model/marks.py`, below every layer that would want either half.

## Consequences

A run costs one monitor whatever the suite's shape, and one trace holds every
worker's marks alongside its GC activity.

The region is decided after the run rather than during it. A benchmark cannot
be re-run to change your mind about where its boundaries were, and with the
marks in the trace it does not have to be.

Marks reach the exporter out of order with respect to records, by seconds
rather than milliseconds, which ADR-0011 already covers: the trace processor
sorts by timestamp and a freshly discovered child's first event can already
predate gcmon polling it.

Anyone wanting per-benchmark numbers reads them off the trace. There is one
path from records to a table instead of two, and nothing in pyperf's metadata
to trend across this change.

The whole arrangement rests on `time.monotonic` being the clock CPython stamps
a GC record from, on both Windows and Linux. If that stops being true the
marks land in the wrong place and nothing says so, which is why a test asserts
the two clocks agree rather than a comment claiming it.

Two ways to get nothing are now one refusal, and both are the operator's to
fix: no `gcmon run`, or `gcmon run` without
`--inherit-environ=GCMON_CONTROL_ADDRESS`, since pyperf isolates its workers
from the environment.

## Alternatives considered

**Repair the gate rather than replace it.** Making start/stop scope anything
means polling at the stop, keeping the counters, polling again at the start,
subtracting the difference, and discarding whatever the ring holds on resume,
which turns the observed span into a set of intervals. Even repaired it loses:
gating destroys the gap's records while still counting their cost, marking
keeps everything and opens no loss window. The one thing gating saves is
gcmon's own polling cost, and gcmon is external, so that cost is not the
benchmark's.

**Monitor from inside the worker.** The remote debugging interface accepts the
caller's own pid, so an in-process hook is available rather than impossible:
it would read its own ring and need no second process. A free-threaded build
sizes the ring at one record per generation, so keeping up means a thread
polling hard enough that every read takes the GIL inside the process being
benchmarked. gcmon reads from outside for that reason, and `--rate` spends the
monitor's time rather than the target's.

**Carry the fields as annotations rather than in the name.** An instant could
take a payload of arbitrary keys, which `build_track_event` already supports
through `debug_annotations` and the GC pause slices already use, and a reader
would join the `args` table instead of parsing a string. That is the better
shape, for the same reason ADR-0006's slice pair is: the fields are structured
rather than encoded. It is also not a change to the hook. `InstantEvent` has
no args where `BeginEvent` and `CounterEvent` do, so a payload has to be
carried by every exporter's `add_instant_event` and survive the JSONL round
trip that `combine` reads. It gets a spec of its own, together with the
decision about what the name is then for, and it is the same spec that settles
the slice shape below.

**Draw a region as a slice.** ADR-0006 makes a duration a begin/end pair in
both backends, and that is the better shape here too: it pairs in the model
rather than in a string, and a reader finds a region without matching two
names. It needs a track of its own and a decision about where that track sits
in the hierarchy, which is a larger change; the grammar module is the only
thing that has to know when someone takes it.

**Recover the benchmark name from the worker's command line.** A regex over
`sys.argv` would name the region without waiting for teardown, at the price of
a second copy of the sanitizer, in another language, with nothing keeping the
two in step.

## Implementation

`src/gcmon/pyperf/hook.py` holds the hook and the refusal;
`src/gcmon/model/marks.py` holds the grammar, formatter and parser together.
`ControlClient.instant_msg` carries the captured timestamp, and the control
server passes it through to the exporter unchanged.

`tests/pyperf/test_pyperf_marks.py` drives a real client into a real control
server: that the marks carry the timestamps taken at the region's ends rather
than at send time, that regions keep counting across hook instances, that an
unfinished region lands nothing, and that the marks reach a Perfetto trace on
the worker's process, read back through the trace processor.
`tests/test_marks.py` pins the literal `gcmon:bm_base64:1:begin`, so a change
to the grammar is a visible diff rather than a round trip that still passes.
