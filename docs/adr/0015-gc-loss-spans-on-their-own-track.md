# ADR-0015: Draw reconstructed GC loss on a per-interpreter track, merged per poll

- **Status:** Accepted
- **Date:** 2026-08-05

## Context

CPython 3.15 exports GC records through a fixed ring buffer: `GC_YOUNG_STATS_SIZE = 11` slots
for generation 0, `GC_OLD_STATS_SIZE = 3` for the older two, and 1 each under
`Py_GIL_DISABLED`. A poll reads the whole ring. A target collecting faster than gcmon polls
overwrites records before anyone reads them, and the read itself gives no sign of it. On the
capture this work was built against, gen 0 collected about 87 times per 100 ms tick against
11 slots.

Two cumulative fields make the loss measurable. `collections` counts what was missed, and
`duration`, a running total of pause seconds, gives the pause time nobody saw. The gap between
one poll's last counter and the next poll's first is an interval in which records were lost,
with an exact count and an exact pause sum attached to it.

Drawing that interval is where the decisions are. Nothing in the ring says where inside the
interval the missing collections ran, and two generations of one interpreter can lose records
across stretches that overlap.

## The arithmetic

Per key, `KeyAccumulator` carries `first`, `first_pause_ns` and `first_duration` from the first
record gcmon observed, `last`, `last_duration` and `last_ts_stop` from the most recent one, and
the running `sampled_count` and `sampled_pause_ns`. On the first record `r` of a poll's run,
with `r.collections = c`, the previous cursor at `p`, and `confirmed` the newest record the
previous poll saw finish anywhere in the interpreter:

```
gap        = c - p - 1
window     = (max(last_ts_stop, confirmed), r.ts_start)
lost_pause = round((r.duration - last_duration) * 1e9) - (r.ts_stop - r.ts_start)
```

`Δduration` spans records `p+1 .. c` inclusive. gcmon read record `c`, so taking its own pause
back out leaves the pause sum of the `gap` records nobody saw. `observe_batch` tests the run's
first record alone, since a ring holds consecutive records and nothing inside a run can be
missing.

At close, per key:

```
exact_count    = last - first + 1
exact_pause_ns = round((last_duration - first_duration) * 1e9) + first_pause_ns
```

`first_duration` is already cumulative through the first record, so the delta over the span
misses that record's own pause. Adding `first_pause_ns` back is the fencepost rule, and it makes
`exact_count` and `exact_pause_ns` describe the same set of collections. One invariant ties them
together, and the suite asserts it:

```
exact_pause_ns == sampled_pause_ns + Σ lost_pause over all windows on the key
```

That one assertion catches a fencepost error, a wrong window, and a `duration` that does not
share a clock with the timestamps.

Lifetime totals need no arithmetic. `collections` and `duration` are cumulative from interpreter
start, and the chain survives the ring wrapping, because `gc_get_prev_stats` reads the immediate
predecessor rather than the slot about to be overwritten: the record being destroyed has already
handed its totals forward. So `lifetime_count = last` and
`lifetime_pause_ns = round(last_duration * 1e9)`, both exact.

## Decision

**Loss spans go on a `GC Loss` track of their own, one per `(pid, iid)`, at
`tid = -2 - iid`.** The `RSS_TID = -1` sentinel from
[ADR-0013](0013-rss-sampling.md) is the precedent, and `LOSS_TID_BASE = -2` extends it to a
range. The track is a plain custom slice track parented to the process track, which is
[ADR-0011](0011-process-lifetime-and-ordering.md)'s `Processes` pattern seen from the other
side, and it carries `sibling_order_rank = 1` so it sorts under the interpreter's own row.
`_build_meta` suppresses `ThreadMeta` for negative tids, which stops Perfetto from drawing the
track as an OS thread that does not exist.

**The slice spans the whole unobserved interval**, abutting the observed collection before it
and the one after. That interval is what gcmon knows. A bar sized to the reconstructed pause
would be narrower than the uncertainty, and would put all of it at the window's left edge
where the data does not support it. The pause sum rides in the args as `lost_pause_gen_N` and
`lost_pause_total`, reading as a magnitude rather than as a placement.

The width is therefore not GC time. One lost 5 ms collection can draw a 130 ms bar, which is
the main reason these spans are not inline. Beside 5 ms `GC Pause` slices a window-width bar
reads as a very long pause, and what is drawn here is reconstructed rather than measured.
A row holding nothing but loss is also a row you can find; inline, a loss span is one more bar
among thousands with only its name to distinguish it.

**The track has to stay laminar, and merging is what keeps it there.** Slices on one Perfetto
track are a stack, so an END force-closes everything above the slice it closes and two spans
that merely cross cannot be expressed. ADR-0011 hit this with process lifetimes and answered
it with a clipping sweep. Observed GC slices never need one, since CPython serializes
collections within an interpreter, but loss spans inherit none of that. They are synthetic
intervals, and a poll that loses gen-0 and gen-1 records over overlapping stretches produces
two windows that cross.

So `merge_windows` collapses one poll's windows, per `(pid, iid)`, into a disjoint set of
maximal spans before export. The union of overlapping intervals is disjoint by construction,
so the track is laminar with **no sweep and nothing shortened**. Each merged span carries the
per-generation counts and pause sums of every window inside it, and attribution stays
unambiguous: a merged span is a union of input windows, so each window lies inside exactly one.

**One poll is the whole scope of the merge.** A single bulk
`_Py_RemoteDebug_ReadRemoteMemory` returns every generation of an interpreter at once, so all
its keys share one confirmation point, and a key whose counter came back unchanged proves it
lost nothing up to that read. Every window opened at poll N therefore starts at or after poll
N-1's confirmation and ends at one of poll N's own fresh records. Windows from different polls
cannot overlap, and nothing later exists to merge with.

That buys a short list of absences: no window is retained, there is no flush at `stop()`,
`forget()` or `retain()`, no unbounded buffer on a long run, and loss spans reach the exporter
in time order as gcmon finds them. Emitting from `_ingest` also keeps loss records flowing
through the shared converter, so Chrome, Perfetto and JSONL get them from one place and
[ADR-0007](0007-shared-trace-converter-pipeline.md) holds. `combine` can then reproduce loss
spans from a JSONL capture, which a Perfetto-only finalize path could not.

**A merged span is drawn whole, over the collections observed inside it.** The span claims
the missing records are somewhere in it, and where the poll recovered a collection inside
that stretch the claim is too strong: collections in an interpreter are serialized, so no
lost record ran during one that was seen.

This was originally answered by cutting the span around those intervals and sharing the
counts and pause across the pieces in proportion to width. That is reversed. Neither pass
asked whether a piece had room for what it was handed, and a piece taking a zero share by
width was dropped, which put its neighbour's whole pause on a bar too narrow to hold it. On a
GC-bound target the split drew bars reporting more lost pause than they were wide, which is
not uncertainty but arithmetic that cannot be true. Nothing in the ring says how the records
divide across the pieces, so every division was a guess, and the guess was the only estimated
quantity in the drawing.

Drawn whole, **every number on a span is the target's own counter over the span's own
bounds**, and the invariant that no bar reports more pause than it covers holds by
construction. The narrowing the split performed is still available to a reader: the observed
collection is drawn on the interpreter's thread row directly above, from evidence already on
screen, and it costs nothing to see.

Drawing touches nothing but the picture. `StreamingStats` records each window as it opens,
before anything is drawn, so coverage, the scale factor and every aggregate are unaffected.

## What gcmon trusts the target for

Two properties are assumed rather than checked. Both belong to CPython. `observe_batch` and
`_is_complete` cite this section rather than restating it.

**1. The publish-last contract survives to the reader.** `add_stats`
(`Python/gc.c:1399-1418`) copies the previous record forward, overwrites `ts_start`,
increments `collections`, and publishes `ts_stop` last, with a comment stating that the
ordering exists so remote readers do not select a partially updated record. Both filters in
`_ingest` are built on that ordering. Between the memcpy and the `ts_start` store the slot is
a byte-identical twin carrying its original's counter, which the counter dedup drops; from
there to the `ts_stop` store it holds a new start against a stale stop, which `_is_complete`
drops.

Whether the ordering reaches the reader is unsettled. The stores are plain, with no barrier
and no atomic. Nothing forbids the compiler from sinking the `ts_start` store past the other
two, and on a weakly-ordered target such as AArch64 store-store order is not architecturally
guaranteed to any other observer, including the kernel serving the read on gcmon's behalf. A
read landing inside a reordered window can return a record assembled from two collections: the
previous start against this stop, under a fresh counter. It passes both filters and is emitted
as genuine, with a span and a pause too long by one collection interval. Each reordering
leaves a different fingerprint, and no client-side check catches them all without also
discarding real records. gcmon detects none of them and does not try. The fix belongs upstream.

**2. One poll's records for one key are contiguous.** A ring holds consecutive records, so the
run handed to `observe_batch` has no hole inside it. A gap can only sit ahead of the run, at
the seam between two polls. `observe_batch` folds the run's tail from its last record alone,
without checking.

Producing such a hole requires the single ~1.1 KB read to be torn by **two or more** collections
completing inside it, positioned so the target's write cursor crosses the reader's. One
collection during the copy always yields a contiguous window whichever side of the cursor it
lands on, and under `Py_GIL_DISABLED` both ring sizes are 1, so a run is a single record and a
hole is impossible. If it ever happened, the counts would stay correct, since they read only
the run's two ends, but no window would carry the hole's pause and the invariant that exact
pause equals sampled plus lost would break without a sound. Accepted without a guard: the
property belongs to the ring, and a check that never fires costs more in code than the failure
costs in practice.

**3. `round(seconds * 1e9)` agrees with the nanosecond timestamps to within a nanosecond.**
`duration` is a `double`, `ts_start` and `ts_stop` are `PyTime_t`, and the arithmetic above
subtracts one from the other. The invariant tests it. A failure there means the two fields do
not share a clock, which would leave the whole reconstruction unsound.

## Consequences

- **The track reads as a near-solid bar at default settings**, because gcmon is blind for most
  of every tick. Lower `--rate` or a calmer workload thins it out, and the numbers live in the
  args either way.
- **One extra row per `(pid, iid)`**, on top of the process track, thread track and
  `GC Metrics` group each process already has.
- **Every `lost_gen_N` and `lost_pause_gen_N` on a bar is a measurement.** Nothing in the
  drawing is estimated, and no bar reports more lost pause than its own duration. The cost is
  paid in width: a span reaches over collections gcmon did observe, and is wider than the
  stretch the missing records can actually be in.
- **Sums and counts become exact; percentiles do not.** `Count` and `Sum` are recoverable from
  the target's own counters. Quantiles are not: gcmon holds only the durations it sampled, and
  that sample is biased in a knowable direction, since a long collection delays its successors,
  occupies its slot longer, and is likelier to survive to the next poll. `P50` through `P99`
  read high. The scale factor cancels the bias for sums but not for quantiles, being a ratio of
  two totals: applying it to a quantile assumes the sampled and unsampled distributions have
  the same shape, which is the assumption the bias violates. Documented rather than corrected,
  in [`docs/statistics.md`](../statistics.md).
- **Crossing loss spans corrupt silently if the merge is ever skipped.** The trace processor
  reports `misplaced_end_event = 0` and reads the crossing span as nested, so nothing in the
  output flags it. The fuzz suite asserts that corruption directly, so the positive test cannot
  pass in a world where merging did nothing.
- **A window can span an observed collection of another generation**, which is why the spans
  cannot share the interpreter's thread track. There they would cross real `GC Pause` slices.
- **`combine` reproduces loss spans from JSONL but not from Chrome.** A Chrome trace carries
  them as slices, so re-converting preserves the drawing and loses the record type.
- **The intervals either side of the observed span draw nothing.** No poll measured a
  `Δcollections` across them, so no evidence of loss exists, and gcmon cannot tell "ran before
  we attached" from "lost". Both fall outside the span rather than counting against coverage.
- **`Δduration` inherits CPython's float accumulator.** `duration` is a `double` taking one
  addition per collection. Over 10^5 collections the relative error runs around 10^-11, below
  the nanosecond resolution of the fields it is compared against, so the reconstruction is exact
  to that accumulator's precision.
- **A pid reused inside one tick is measured against its predecessor's counter.** If a child
  dies and the OS hands its pid to a new CPython process between two polls, the pid never leaves
  `children` and `retain` never fires, so the successor stays invisible until its own counter
  passes the dead one's. gcmon then reports a gap and an `exact_count` belonging to neither
  process. Accepted without code: reuse that fast needs the pid allocator to wrap, and a
  successor gcmon cannot read returns `INVALID_PROCESS`, which clears the cursor through
  `forget`.
- **A duplicated export can push `Cov` above 1.0.** When a wait policy gives up on a pid that
  later answers again, `forget` has dropped its cursors and the next poll re-exports the whole
  ring. Those duplicates inflate `sampled_count`, which now divides into an exact count.
  Clamping the ratio would hide the duplication, so this decision leaves it alone.

## Alternatives considered

- **Inline on the interpreter's thread track (`tid = iid`), with an ADR-0011-style clipping
  sweep.** One row cheaper. Rejected twice over: a window-width bar beside 5 ms pauses invites
  the misreading the separate track exists to prevent, and clipping would shorten spans whose
  width is the claim being made. Merging reaches laminarity with no clipping at all, because
  loss windows may be unioned where process lifetimes may not.
- **Inline, snapped to the adjacent observed records.** Rejected: it draws the loss as a pause
  of known extent, which is the one thing the data does not support.
- **One track per `(pid, iid, gen)`.** Rejected: a poll's windows for two generations start at
  the same confirmation point and overlap, so three rows would draw the same stretch three
  times where one merged span says it once.
- **A flat `-2` for every interpreter.** Rejected: interpreters collect concurrently and
  nothing serializes their windows, so two interpreters' spans can cross for real. One row
  would need a clipping sweep to hold them.
- **Retaining windows and merging at `stop()`.** Correct, and rejected: it buffers without
  bound on a long run and emits every loss span in a lump after every GC event. The per-poll
  confirmation point makes it unnecessary.
- **Emitting the track from a `finalize_perfetto_packets`-style hook**, as ADR-0011 does for
  process lifetimes. Rejected: it would make loss Perfetto-only, breaking ADR-0007's single
  converter and leaving `combine` unable to reproduce spans from a JSONL capture.
- **Clipping a window's far end to the poll's earliest observation anywhere in the
  interpreter.** Tried and abandoned. Oldest-first eviction orders a key's lost records against
  that key's kept records and says nothing about another generation's, so a lost gen-0
  collection can have run after an observed gen-2 one. On a real capture, clipping produced 5
  windows of 98 narrower than the pause they reported. One was 37 ms carrying 53 ms of lost
  collections, while the key's own next record sat 537 ms out.
- **Bounding a split piece by how many collections it could hold**, while the split still
  existed, taking the shortest interval ever measured between two consecutive records of that
  key as a floor on the period. On the trace it removed the pieces that look wrong. It is what
  a narrowing rule would have to be built on if one is ever wanted again. Rejected: the floor is a lifetime
  minimum, so one fast burst weakens it for the whole run, and a target whose pace shifts
  between phases inherits the fastest phase's floor. It also errs in the dangerous direction,
  since a piece narrower than the floor that did hold a record loses it to a neighbour, and
  loss happens precisely when collections come fast.
- **A flag to disable loss detection.** Rejected: a reader who does not know what fraction of
  collections a capture contains cannot interpret any other number in it.

## Implementation

- `src/gcmon/loss.py` holds the arithmetic and the geometry: `KeyAccumulator` (one per
  `(pid, iid, gen)`, carrying the fencepost fields), `LossWindow`, `MergedLoss`,
  `confirmed_by_interpreter`, `merge_windows` and `to_loss_msg`. Pure functions and structs,
  no I/O.
- `src/gcmon/monitor.py`, `_ingest`: sorts each poll's complete records into counter order per
  key, folds them, then merges that poll's windows and emits them. The confirmation point comes
  from `confirmed_by_interpreter` alone, so it is one bound per interpreter rather than one per
  ring, and every window a poll opens for an interpreter shares a left edge. A record a read
  catches part-written is dropped by `_is_complete` and returns complete a poll later; it
  neither opens a window nor bounds one, which costs width rather than accuracy.
- `src/gcmon/trace_event.py`, `LOSS_TID_BASE = -2` with `loss_tid` and `loss_iid`.
- `src/gcmon/exporters/trace_converter.py`, `convert_loss_to_trace_format`, the third record
  type through the shared pipeline. `src/gcmon/exporters/perfetto_format.py`,
  `_emit_loss_descriptor`, called from both the BEGIN and END branches, since the descriptor
  hangs off the slices rather than off a meta event.
- `tests/test_loss.py` checks the arithmetic against synthetic runs with known ground truth
  and against a verbatim two-poll capture in `tests/test_monitor_cursor.py`.
  `tests/exporters/test_perfetto_loss_track.py`, marked `fuzz`, settles the track-layout and
  laminarity claims against the real trace processor per
  [ADR-0014](0014-perfetto-integration-test-strategy.md).
