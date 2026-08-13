# ADR-0015: Draw reconstructed GC loss on a per-interpreter track, one span per poll interval

- **Status:** Accepted
- **Date:** 2026-08-05 (rewritten 2026-08-12, when the span became the poll interval and the
  per-generation counts moved into grouped args; earlier captures do not read back)

## Context

CPython 3.15 exports GC records through a fixed ring buffer: `GC_YOUNG_STATS_SIZE = 11` slots
for generation 0, `GC_OLD_STATS_SIZE = 3` for the older two, 1 each under `Py_GIL_DISABLED`. A
poll reads the whole ring. A target collecting faster than gcmon polls loses records before any
poll reads them, and the read gives no sign of it. On the capture this was built against, gen 0
collected about 87 times per 100 ms tick against 11 slots.

Two cumulative fields make the loss measurable. `collections` counts what was missed;
`duration`, a running total of pause seconds, gives the pause time nobody saw. The gap between
one poll's last counter and the next poll's first carries an exact count and an exact pause
sum.

Where inside that gap the missing runs happened, nothing the target exports says. Every
question below comes back to that split. The counts are exact; their placement is guesswork.

## The arithmetic

Per ring, gcmon carries `first`, `first_pause_ns` and `first_duration` from the first record it
observed, `last` and `last_duration` from the most recent one, and the running `sampled_count`
and `sampled_pause_ns`. On the first record `r` a poll returned for that ring, with
`r.collections = c` and the previous cursor at `p`:

```
lost_from  = p + 1
gap        = c - lost_from
lost_pause = round((r.duration - last_duration) * 1e9) - (r.ts_stop - r.ts_start)
```

No interval appears here. The per-generation entry the exporters read holds counters and
nothing else; the two polls that bracket it answer the placement question.

`Δduration` spans records `p+1 .. c` inclusive. gcmon read record `c`, so taking its own pause
back out leaves the pause sum of the `gap` records nobody saw. Only the first record a poll
returned for a ring is tested, since a ring holds consecutive records and nothing between them
can be missing.

At close, per ring:

```
exact_count    = last - first + 1
exact_pause_ns = round((last_duration - first_duration) * 1e9) + first_pause_ns
```

`first_duration` is already cumulative through the first record, so the delta over the span
misses that record's own pause. Adding `first_pause_ns` back makes `exact_count` and
`exact_pause_ns` describe the same runs. One invariant ties them together, and the suite
asserts it:

```
exact_pause_ns == sampled_pause_ns + Σ lost_pause over all gaps on the ring
```

That assertion catches a fencepost error, a wrong gap, and a `duration` that does not share a
clock with the timestamps.

Lifetime totals need no arithmetic. `collections` and `duration` are cumulative from
interpreter start, and the chain survives the ring wrapping: `gc_get_prev_stats` reads the
immediate predecessor rather than the slot about to be overwritten, so the record being
destroyed has already handed its totals forward. `lifetime_count = last` and `lifetime_pause_ns
= round(last_duration * 1e9)`, both exact.

## Decision

**Loss spans go on a `GC Loss` track of their own, one per `(pid, iid)`, at `tid = -2 - iid`.**
The `tid = -1` sentinel from [ADR-0013](0013-rss-sampling.md) is the precedent, and the loss
tids extend it to a range. The track is a plain custom slice track parented to the process
track, carrying `sibling_order_rank = 1` so it sorts under the interpreter's own row. gcmon
names no negative tid with a `thread_name` metadata event, which stops Perfetto from drawing
the track as an OS thread that does not exist.

**A span is one poll interval**, from the read before the gap to the read that found it. That
bound is the tightest available: a record the previous poll did not return was either lost
already, which that poll reported itself, or not yet written. So every run a span names
finished between those two reads.

**Both edges come from the monitor's clock**, `time.monotonic_ns()` taken at the start of the
read and carried through the fold. Anchoring both on the same point of the read makes
consecutive intervals tile the timeline; taking the left edge from one poll's start and the
right from another's finish would overlap them by the width of a read. Reading the monitor's
clock against the target's timestamps is an assumption gcmon already makes, since ADR-0013's
RSS samples are stamped from it and land on the same timeline as the GC records.

**One span, named for the generations that lost records**, `GC Loss(0,2)`. A record can only go
missing between two reads, so the generations go blind over the same interval and differ only
in their counts. The name gives each combination a stable colour, since Perfetto hashes it, and
says which generations went blind before a reader clicks anything.

**A span's width is uncertainty rather than GC time.** One lost 5 ms run can draw a 130 ms bar.
Beside 5 ms `GC Pause` slices such a bar reads as a very long pause, which is why loss gets a
row of its own, and a row holding nothing else is a row you can find.

The bar also covers the runs gcmon did see, its own interpreter's included. Runs inside an
interpreter are serialized, so no lost one happened during one that was seen. Trimming the bar
around them would narrow the claim to somewhere the records might not be, on evidence that says
nothing about where they ran; the args report how much of the interval survived instead. Every
number on a span is then the target's own counter over the span's own bounds, and no bar
reports more pause than it covers.

**The row is a sequence.** Slices on one Perfetto track are a stack: an END force-closes
everything above the slice it closes, and two spans that cross cannot be expressed.
[ADR-0011](0011-process-lifetime-and-ordering.md) hit this with process lifetimes and answered
it with a clipping sweep. One span per poll needs no sweep, no merge and no nesting order,
since consecutive intervals meet at a poll instant and never overlap. Two edges taken from two
reads also cannot arrive reversed, so no span is ever held back for describing no interval.

Order still matters between neighbours. A span's END shares its timestamp with the next one's
BEGIN, and a processor sorting by timestamp leaves those two in the order they were emitted;
reversed, they read as nested. The monitor emits a poll at a time and gets this right by
construction. A capture read back from JSONL carries the order only in its lines, so the
converter sorts loss records on `ts_start` before drawing them.

**The args are the interval's totals, then one group per generation.** `observed_count`,
`missing_count`, `seen`, `missing_pause_total` and `missing_pause_total_ns` sit at the top
level, where a reader finds them without opening anything. Each generation that collected or
lost something gets a `gen0` / `gen1` / `gen2` group under them, carrying its own counts and
`missing_collections`. A generation that came through whole still gets a group with what it
observed, which makes `seen` checkable: the groups add up to the totals. A generation that
neither collected nor lost anything is left out, since an entry saying zero twice is noise on a
slice meant to be read at a glance.

`missing_pause_total_ns` sums what the lost records cost, so it says `total`: a reader who
takes it for the bar's own width has read the one number on the slice that is not a duration.
It appears twice, exactly for SQL and as text for reading, since a bare `3316458100` beside a
duration the UI has already formatted invites being read as a smaller number than it is. `seen`
carries its counts for the same reason.

A group reaches Perfetto as a `DebugAnnotation` carrying `dict_entries` and no value of its
own. The trace processor flattens the entries back under the group's name, so gen 1's count
answers to `args.debug.gen1.missing_count` in SQL.

**A span names the records it is missing**, as `missing_collections` in each group, both ends
included and written as one field. A pair of numbers meets at the same counter whenever a ring
loses a single record, and `413..413` reads as a range of nothing to anyone who does not
already know the ends are inclusive. Subtracting two of the ring's cumulative counters puts
both fences in hand, and only the near one is stored, as `lost_from`; the far end follows from
it and `lost_count`, since a stored pair could drift from the count `--stats` sums. The range
makes the reconstruction checkable: between the first and last record gcmon read on a ring,
every run is either drawn as a `GC Pause` slice or inside exactly one span's range for that
generation, none twice and none unaccounted for.

**The statistics record each gap as it is found**, before anything is drawn, so coverage, the
scale factor and every aggregate stay independent of what the trace shows.

**Loss records leave the monitor a poll at a time.** Nothing is retained, no flush is needed
when a session stops or a pid goes away, and no buffer grows over a long session. Emitting
there also keeps loss inside the shared converter, so Chrome, Perfetto and JSONL take it from
one place and [ADR-0007](0007-shared-trace-converter-pipeline.md) holds. `combine` can then
rebuild the spans from a JSONL capture.

## What gcmon trusts the target for

Three properties hold the reconstruction up. CPython guarantees the first two, which gcmon
assumes rather than checks; the invariant above tests the third. `src/gcmon/loss.py` cites this
section rather than restating it.

**1. The publish-last contract survives to the reader.** `add_stats` (`Python/gc.c:1399-1418`)
copies the previous record forward, overwrites `ts_start`, increments `collections`, and
publishes `ts_stop` last, with a comment saying the ordering exists so remote readers do not
select a partially updated record. Both of gcmon's filters rest on it. Between the memcpy and
the `ts_start` store the slot is a byte-identical twin carrying its original's counter, which
the ring drops on the counter; from there to the `ts_stop` store it holds a new start against a
stale stop, which the completeness filter drops.

Whether that ordering reaches the reader is unsettled. The stores are plain, with no barrier
and no atomic, so nothing forbids the compiler from sinking the `ts_start` store past the other
two, and on a weakly-ordered target such as AArch64 store-store order is not architecturally
guaranteed to any other observer, including the kernel serving gcmon's read. A read landing
inside a reordered window can return a record assembled from two runs: the previous start
against this stop, under a fresh counter. It passes both filters and goes out as genuine, with
a pause too long by the interval between the two. Each reordering leaves a different
fingerprint, and no client-side check catches them all without discarding real records too, so
gcmon does not try. The fix belongs upstream.

**2. One poll's records for one ring are contiguous.** A ring holds consecutive records, so
what a poll hands over has no hole inside it and a gap can only sit ahead of it, at the seam
between two polls. gcmon folds the tail from the last record alone, without checking.

Producing such a hole takes **two or more** runs finishing inside one ~1.1 KB read, positioned
so the target's write cursor crosses the reader's. A single run finishing during the copy
leaves the records contiguous whichever side of the cursor it lands on, and under
`Py_GIL_DISABLED` every ring holds one slot, so a poll returns at most one record per ring and
a hole cannot form. Were it to happen, the counts would survive, since they read only the two
ends, but no gap would carry the hole's pause and the invariant would break in silence.
Accepted without a guard: the property belongs to the ring, and a check that never fires costs
more in code than the failure costs in practice.

A torn read costs nothing else. It moves which records a poll sees; the counters are
subtractions holding no timestamp, and the edges come from the clock rather than the records.

**3. `round(seconds * 1e9)` agrees with the nanosecond timestamps to within a nanosecond.**
`duration` is a `double`, `ts_start` and `ts_stop` are `PyTime_t`, and the arithmetic above
subtracts one from the other. The invariant tests it. A failure there means the two fields do
not share a clock, which would leave the whole reconstruction unsound.

## Consequences

- **The track reads as a near-solid bar at default settings**, since gcmon is blind for most of
  every tick. Lower `--rate` or a calmer workload thins it out, and the numbers live in the
  args either way.
- **One extra row per `(pid, iid)`**, on top of the process track, thread track and `GC
  Metrics` group each process already has. Three generations share it, so the row's shape does
  not depend on how many of them went blind.
- **Sums and counts become exact; percentiles do not.** `Count` and `Sum` come back from the
  target's own counters. Quantiles cannot: gcmon holds only the durations it sampled, and that
  sample skews long, since a long run delays the next one, so its record occupies its slot
  longer and is likelier to survive to the next poll. `P50` through `P99` read high. The scale
  factor is a ratio of two totals, so it corrects sums; applying it to a quantile would assume
  the sampled and unsampled distributions share a shape, which is the assumption the skew
  violates. [`docs/statistics.md`](../statistics.md) documents this rather than correcting it.
- **A loss row whose spans overlap corrupts in silence**, the first END closing the wrong span
  while the trace processor reports `misplaced_end_event = 0`. One span per poll cannot produce
  that shape, so the default suite pins the row's flatness rather than leaving it to the `fuzz`
  job, which is gated off `main`.
- **`combine` rebuilds loss spans from JSONL but not from Chrome.** A Chrome trace carries them
  as slices, so re-converting preserves the drawing and loses the record type.
- **The intervals either side of the observed span draw nothing.** No poll measured a
  `Δcollections` across them, and gcmon cannot tell "ran before we attached" from "lost". Both
  fall outside the span rather than counting against coverage.
- **`Δduration` inherits CPython's float accumulator.** Over 10^5 runs the relative error stays
  around 10^-11, below the nanosecond resolution of the fields it is compared against.
- **A pid reused inside one tick is measured against its predecessor's counter**, so gcmon
  reports a gap and an `exact_count` belonging to neither process. Accepted without code: reuse
  that fast needs the pid allocator to wrap, and a successor gcmon cannot read returns
  `INVALID_PROCESS`, which clears the rings.
- **A duplicated export can push `Cov` above 1.0.** After gcmon drops a pid's rings the next
  poll re-exports the whole ring, and those duplicates inflate `sampled_count`, which now
  divides into an exact count. Clamping the ratio would hide the duplication.

## Alternatives considered

- **One span per generation, each running to that generation's next observed record.** The
  shape to argue against first, since it puts each generation's blindness on a bar of its own.
  Rejected on the widths: they come from where the next record happens to sit rather than from
  when anything was lost, so three bars at three widths read as three events at three times.
  They also have to be sorted into nesting order, can arrive reversed when a read tears, and
  say nothing the grouped args leave out.
- **Narrowing a span around the records observed inside it**, by cutting it into pieces and
  sharing the counts and pause across them. Rejected: nothing in the ring says how the records
  divide, so every division is a guess, and it would be the only estimated quantity in the
  drawing. A share taken by width can also hand a piece more pause than it is wide. The
  narrowing stays available to a reader anyway, from the `GC Pause` slices on the row above and
  the `observed_count` in the args.
- **Clipping a span's far end to the poll's earliest observation anywhere in the interpreter.**
  The same guess in a subtler form. Oldest-first eviction orders a ring's lost records against
  that ring's kept records and says nothing about another generation's, so a lost gen-0 run can
  have happened after an observed gen-2 one. Measured on a real capture, clipping produced
  spans narrower than the pause they reported.
- **Bounding a span by how many runs it could hold**, taking the shortest interval ever
  measured between two consecutive records of a ring as a floor on its period. Rejected: one
  fast burst weakens a lifetime minimum for the whole session. It also errs in the dangerous
  direction, since a stretch narrower than the floor that did hold a record loses it, and loss
  happens precisely when runs come fast.
- **Inline on the interpreter's thread track (`tid = iid`)**, either with an ADR-0011-style
  clipping sweep or snapped to the adjacent observed records. Rejected: an interval-width bar
  beside 5 ms pauses invites the misreading the separate track prevents, clipping would shorten
  spans whose width is the claim being made, and snapping draws the loss as a pause of known
  extent.
- **One track per `(pid, iid, gen)`.** Three rows say what the args say, at three times the
  vertical cost, and a process with several interpreters would carry nine.
- **A flat `-2` for every interpreter.** Rejected: interpreters collect concurrently and one
  poll bounds all of them, so two interpreters' spans would land on one row over the same
  interval and Perfetto would read the pair as nested.
- **Retaining intervals and emitting them at shutdown.** Correct, and rejected: it buffers
  without bound on a long session and emits every loss span in a lump after every GC record. A
  poll knows both its edges.
- **Emitting the track from a Perfetto-only finalization hook**, as ADR-0011 does for process
  lifetimes. Rejected: it would make loss Perfetto-only, breaking ADR-0007's single converter
  and leaving `combine` unable to rebuild spans from a JSONL capture.
- **A flag to disable loss detection.** Rejected: a reader who does not know what fraction of
  the records a capture holds cannot interpret any other number in it.

## Implementation

- `src/gcmon/loss.py` holds the arithmetic, one accumulator per `(pid, iid, gen)` carrying the
  fencepost fields. Pure structs, no I/O. It picks out what the ring has not handed over,
  cursor and duplicate slots both, folds that, and returns the generation's entry for the poll,
  so nothing downstream assembles one out of a loss and a count kept apart.
- `src/gcmon/data.py` holds the loss record, one entry per generation, and derives the far
  fence from `lost_from` and `lost_count`. It also produces the args written for reading rather
  than for summing.
- `src/gcmon/monitor.py` sorts each poll's complete records into counter order per ring, hands
  each ring its own, and emits one record per interpreter that lost anything, bounded by the
  pid's previous poll instant and this one. That instant sits beside the rings, so dropping a
  pid drops both together and a reused pid inherits no interval. A record caught part-written
  is dropped and comes back complete a poll later, opening no gap and bounding none.
- `src/gcmon/trace_event.py` fixes the loss track at `tid = -2 - iid` and widens a slice's args
  to one level of nesting for the generation groups.
- `src/gcmon/exporters/trace_converter.py` takes loss through the shared pipeline as its third
  record type. `src/gcmon/exporters/perfetto_format.py` emits the track descriptor from both
  the BEGIN and the END branch, since it hangs off the slices rather than off a meta event, and
  `src/gcmon/exporters/perfetto_builders.py` writes a generation's group as `dict_entries` on
  an annotation carrying no value of its own.
- `src/gcmon/stats.py` records every gap.
- `tests/test_loss.py` checks the arithmetic against synthetic sessions with known ground truth
  and against a verbatim two-poll capture in `tests/test_monitor_cursor.py`, driving the
  monitor itself rather than a mirror of it. `tests/test_loss_replay.py` replays
  `tests/captures.py`, every GC run one target made with its counters unbroken, behind a
  simulated ring and poll clock, so the counts, the pause sums and the intervals answer to the
  target rather than to an expectation. Its ring model follows §1 and holds itself to the
  verbatim capture, and it reaches both hazards above by construction.
- `tests/exporters/loss_row.py` polls a monitor on a fixed clock and resolves the loss row as a
  stack, failing if any span opens inside another.
  `tests/exporters/test_combine_loss_round_trip.py` runs that walk over the live row and a
  second one over `combine`'s Chrome output, compares the two, and feeds both an overlapping
  pair as a negative control. `tests/exporters/test_perfetto_loss_track.py`, marked `fuzz`,
  settles the track-layout and flatness claims against the real trace processor per
  [ADR-0014](0014-perfetto-integration-test-strategy.md).
