# gcmon

gcmon reads another Python process's garbage-collection records out of its
memory and turns them into a trace and a statistics table. Everything below is
vocabulary: what a word means here, and which spelling to use when several are
in circulation. `docs/adr/` records decisions and `specs/` records open work.

## Language

### What the target writes, what gcmon writes

**Record**:
One entry gcmon read out of the target's ring buffer, describing one finished
GC run.
_Avoid_: sample, entry, datapoint

**Event**:
One thing gcmon wrote into a trace. A record becomes one or more events.
_Avoid_: record (for the trace side), slice (that is one shape of event)

**Span**:
A slice on the `Processes` track, bounding a process's observed lifetime.
_Avoid_: lifetime (unqualified; see below), duration, extent

### The things gcmon counts

**Ring**:
One CPython ring buffer, identified by `(pid, iid, gen)`. It carries its own
`collections` counter, and it is the unit statistics are reported for. A pid
outlives the process holding it, so what a run keeps to the end carries the
**pid epoch** as well.
_Avoid_: buffer, per-generation stats, slot array

**Interpreter**:
One CPython interpreter inside a process, identified by its **iid**. Each keeps
its own collector, its own rings and its own cumulative counters, and gcmon
publishes the iid as a Perfetto `tid`.
_Avoid_: subinterpreter (an iid of 0 is an interpreter too), thread, isolate

**Generation**:
One of the collector's three age tiers, `0`, `1` or `2`, spelled **gen** in
code and `Gen0`–`Gen2` in output.
_Avoid_: tier, level, cohort

**Pid epoch**:
Which process held a pid, counting from 1 and advancing when gcmon sees one
exit. Spelled `pid_epoch`, and part of every key a run keeps to the end, so a
successor on a recycled pid never writes into its predecessor's figures.
_Avoid_: index (that is CPython's write cursor into a ring buffer, and nothing
else), generation (the collector owns it), epoch (bare, reads as a point in
time)

**Loss window**:
An interval whose records were overwritten before any poll read them, bounded
by the two poll instants either side of it. In a name, **loss** is the window
itself and **lost** is what was in it: `loss_tid` names the track it is drawn
on, `lost_count` the records it swallowed. The window has a width of its own,
so a `loss_duration` and a `lost_pause_ns` are different numbers.
_Avoid_: missing data, dropped events, gap (in output; fine in prose about the
arithmetic), `loss_count` for a count of records

### The three intervals a number can describe

Every figure gcmon prints answers to one of these, and the two-number cells in
the `--stats` table are there to say which.

**Sampled**:
The records gcmon read. Percentiles and averages are always this, and they read
high: a long run delays the next one, so its record survives in the ring more
often than a short one's.
_Avoid_: observed (means the span, below), measured, actual

**Exact**:
Every GC run between the first and last record gcmon read on a ring, whether or
not its record survived to be read. Reconstructed from the target's cumulative
counters, and exact in the arithmetic sense.
_Avoid_: true, real, total

**Lifetime totals**:
Everything one interpreter has collected since it started, monitored window
included. Always written with the qualifier: bare **lifetime** means the
`Processes`-track span above. The source names the counters underneath rather
than the interval — `CumulativeCounters`, `StreamingStats.observe_cumulative`,
`cumulative_totals_by_gen` — so the bare word is left to the span everywhere
outside this prose and the `pause_gen_N_lifetime_*` pyperf keys.
_Avoid_: lifetime (bare), cumulative total (the counters underneath are
cumulative, the interval is not), since-start count

**Observed span**:
The interval from the first record gcmon read on a ring to the last. What
happened before it counts as neither sampled nor lost, because gcmon cannot
tell "ran before we attached" from "was overwritten".
_Avoid_: monitoring window (that is wall time, and wider), capture

### How complete a capture is

**Coverage**:
Sampled count over exact count, in `[0, 1]`. Printed as the `Cov` column and
as a percentage in the footer and the advisory.
_Avoid_: completeness, hit rate, fidelity

**Scale factor**:
The multiplier taking a sampled pause sum to the exact one. Printed as the `F`
column. It corrects a sum, never a percentile.
_Avoid_: correction factor, weight, extrapolation
