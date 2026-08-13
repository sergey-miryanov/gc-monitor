# ADR-0015: Draw reconstructed GC loss on a per-interpreter track, one span per poll interval

- **Status:** Accepted
- **Date:** 2026-08-05

## Context

CPython 3.15 exports GC records through a fixed ring buffer, a few slots per generation and one
per generation under `Py_GIL_DISABLED`. A poll reads the whole ring. A target collecting faster
than gcmon polls overwrites records before any poll reads them, and the read gives no sign of
it.

Two cumulative fields make the loss measurable. `collections` counts the runs that finished,
`duration` totals their pause seconds, and the gap between one poll's last counter and the next
poll's first carries an exact count and an exact pause sum.

Where inside that gap the missing runs happened, nothing the target exports says. Every
question below comes back to that split: the counts are exact, their placement is guesswork.

## Decision

**Loss spans go on a `GC Loss` track of their own, one per `(pid, iid)`, at `tid = -2 - iid`.**
The `tid = -1` sentinel from [ADR-0013](0013-rss-sampling.md) is the precedent, extended to a
range. The track is a custom slice track parented to the process track, sorting under the
interpreter's own row. gcmon names no negative tid with a `thread_name`, which stops Perfetto
from drawing a track as an OS thread that does not exist.

**A span is one poll interval**, from the read before the gap to the read that found it. That
bound is the tightest available: a record the previous poll did not return was either lost
already, which that poll reported itself, or not yet written. Every run a span names finished
between those two reads.

**Both edges come from the monitor's clock**, `time.monotonic_ns()` taken at the start of the
read. Anchoring both on the same point makes consecutive intervals tile the timeline; a left
edge from one poll's start against a right edge from another's finish would overlap by the
width of a read. ADR-0013's RSS samples already land on this timeline beside the target's own
timestamps.

**One span, named for the generations that lost records**, `GC Loss(0,2)`. A record goes
missing between two reads, so the generations go blind over the same interval and differ only
in their counts. Perfetto hashes the name, so each combination keeps a stable colour, and a
reader sees which generations went blind before clicking anything.

**A span's width is uncertainty, not GC time.** One short lost run can draw a bar as wide as
the poll interval. Beside the `GC Pause` slices such a bar reads as a very long pause, which is
why loss gets a row of its own, and a row holding nothing else is a row you can find.

The bar also covers the runs gcmon did see. Runs inside an interpreter serialize, so no lost
run happened during an observed one. Trimming the bar around them would narrow the claim to
somewhere the records might not be, on evidence that says nothing about where they ran. The
args report how much of the interval survived instead, and every number on a span stays the
target's own counter over the span's own bounds.

**The row is a sequence.** Consecutive intervals meet at a poll instant, so spans touch without
crossing and the track needs no clipping sweep of the kind
[ADR-0011](0011-process-lifetime-and-ordering.md) built for process lifetimes. Touching puts
one span's END on the same timestamp as the next one's BEGIN, which makes their emission order
load-bearing.

**The args are the interval's totals, then one group per generation.** `observed_count`,
`missing_count`, `seen`, `missing_pause_total` and `missing_pause_total_ns` sit at the top
level. Each generation that collected or lost something gets a `gen0` / `gen1` / `gen2` group
under them. A generation that came through whole still gets its group, which makes `seen`
checkable, since the groups add up to the totals. A generation that neither collected nor lost
anything is left out.

`missing_pause_total_ns` sums what the lost records cost, so it says `total`: a reader who
takes it for the bar's width has read the one number on the slice that is not a duration. It
appears twice, as a number for SQL and as text for reading, and `seen` carries its counts for
the same reason. A group reaches Perfetto as a `DebugAnnotation` holding `dict_entries` and no
value of its own, which the trace processor flattens back under the group's name.

**A span names the records it is missing**, as `missing_collections` in each group, both ends
included and written as one field. A pair of numbers meets at the same counter whenever a ring
loses one record, and a range of nothing reads as a mistake to anyone who does not already know
the ends are inclusive. Only the near fence is stored, as `lost_from`; the far end follows from
it and `lost_count`, since a stored pair could drift from the count `--stats` sums. The range
makes the reconstruction checkable: between the first and last record gcmon read on a ring,
every run is either drawn as a `GC Pause` slice or inside exactly one span's range for that
generation, none twice and none unaccounted for.

**The reconstruction answers to the target's own totals.** Per ring, the pause time gcmon
sampled plus the pause time it attributed to the gaps equals the delta of `duration` across the
whole observed span. The suite asserts it, which catches a fencepost error, a wrong gap, and a
`duration` that does not share a clock with the timestamps.

**The statistics record each gap as it is found**, before anything is drawn, so coverage, the
scale factor and every aggregate stay independent of what the trace shows.

**Loss records leave the monitor a poll at a time.** Nothing is retained, no flush is needed
when a session stops or a pid goes away, and no buffer grows over a long session. Emitting
there keeps loss inside the shared converter, so Chrome, Perfetto and JSONL take it from one
place and [ADR-0007](0007-shared-trace-converter-pipeline.md) holds. `combine` can then rebuild
the spans from a JSONL capture.

## What gcmon trusts the target for

**The publish-last contract survives to the reader.** `add_stats` in `Python/gc.c` copies the
previous record forward, overwrites `ts_start`, increments `collections`, and publishes
`ts_stop` last, so that a remote reader does not select a partially updated record. Both of
gcmon's filters rest on that ordering. Between the copy and the `ts_start` store the slot is a
byte-identical twin carrying its original's counter, which gcmon drops on the counter; from
there to the `ts_stop` store it holds a new start against a stale stop, which gcmon drops as
incomplete. The stores are plain, with no barrier and no atomic, so nothing forbids a compiler
or a weakly-ordered target from reordering them, and a read landing inside a reordered window
returns a record assembled from two runs that passes both filters and goes out as genuine. No
client-side check catches every fingerprint without discarding real records too, so gcmon does
not try. The fix belongs upstream.

**One poll's records for one ring are contiguous.** A ring holds consecutive records, so what a
poll hands over has no hole inside it and a gap can only sit at the seam between two polls.
gcmon folds the tail from the last record alone, without checking. Producing a hole takes two
or more runs finishing inside one read, positioned so the target's write cursor crosses the
reader's; under `Py_GIL_DISABLED` a poll returns at most one record per ring and the shape
cannot form. The counts would survive it, since they read only the two ends, but no gap would
carry the hole's pause and the invariant would break in silence. A check that never fires costs
more in code than the failure costs in practice.

**`duration` and the timestamps share a clock.** `duration` is a `double` and the timestamps
are `PyTime_t`, and the arithmetic subtracts one from the other. The invariant tests it, and a
failure there means the whole reconstruction is unsound.

Lifetime totals need none of this. `collections` and `duration` run cumulative from interpreter
start, and the chain survives the ring wrapping, since `gc_get_prev_stats` reads the immediate
predecessor rather than the slot about to be overwritten.

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
  `collections` delta across them, and gcmon cannot tell "ran before we attached" from "lost".
  Both fall outside the span rather than counting against coverage.
- **A pid reused inside one tick is measured against its predecessor's counter**, so gcmon
  reports a gap belonging to neither process. Accepted without code: reuse that fast needs the
  pid allocator to wrap, and a successor gcmon cannot read returns `INVALID_PROCESS`, which
  clears the rings.
- **A duplicated export can push `Cov` above 1.0.** After gcmon drops a pid's rings the next
  poll re-exports the whole ring, and those duplicates inflate the sampled count, which now
  divides into an exact count. Clamping the ratio would hide the duplication.

## Alternatives considered

- **One span per generation, each running to that generation's next observed record.** The
  shape to argue against first, since it puts each generation's blindness on a bar of its own.
  Rejected on the widths: they come from where the next record happens to sit rather than from
  when anything was lost, so three bars at three widths read as three events at three times.
  They also need sorting into nesting order, can arrive reversed when a read tears, and say
  nothing the grouped args leave out.
- **Narrowing a span around the records observed inside it**, by cutting it into pieces and
  sharing the counts and pause across them. Rejected: nothing in the ring says how the records
  divide, so every division is a guess, and it would be the only estimated quantity in the
  drawing. A share taken by width can hand a piece more pause than it is wide. A reader can
  still narrow it by hand, from the `GC Pause` slices on the row above and `observed_count` in
  the args.
- **Clipping a span's far end to the poll's earliest observation anywhere in the interpreter.**
  The same guess in a subtler form. Oldest-first eviction orders a ring's lost records against
  that ring's kept records and says nothing about another generation's, so a lost gen-0 run can
  have happened after an observed gen-2 one. Clipping can also leave a span narrower than the
  pause it reports.
- **Bounding a span by how many runs it could hold**, taking the shortest interval ever
  measured between two consecutive records of a ring as a floor on its period. Rejected: one
  fast burst weakens a lifetime minimum for the whole session. It errs in the dangerous
  direction too, since a stretch narrower than the floor that did hold a record loses it, and
  loss happens when runs come fast.
- **Inline on the interpreter's thread track (`tid = iid`)**, either with an ADR-0011-style
  clipping sweep or snapped to the adjacent observed records. Rejected: an interval-width bar
  beside the pause slices invites the misreading the separate track prevents, clipping would
  shorten spans whose width is the claim being made, and snapping draws the loss as a pause of
  known extent.
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

- `src/gcmon/loss.py` holds the arithmetic, one accumulator per `(pid, iid, gen)`. Pure
  structs, no I/O.
- `src/gcmon/data.py` holds the loss record and derives the far fence from `lost_from` and
  `lost_count`.
- `src/gcmon/monitor.py` bounds each interval by the pid's previous poll instant and this one.
  That instant sits beside the rings, so dropping a pid drops both and a reused pid inherits no
  interval.
- `src/gcmon/exporters/trace_converter.py` takes loss through the shared pipeline as its third
  record type, and restores span order when a capture comes back from JSONL, where the lines
  carry it and nothing else does. `src/gcmon/exporters/perfetto_format.py` and
  `src/gcmon/exporters/perfetto_builders.py` write the track and the generation groups.
- `src/gcmon/stats.py` records every gap.
- `tests/test_loss.py` and `tests/test_loss_replay.py` check the arithmetic against synthetic
  sessions and against a real capture replayed behind a simulated ring and poll clock.
  `tests/exporters/test_combine_loss_round_trip.py` resolves the loss row as a stack, live and
  through `combine`, with an overlapping pair as a negative control.
  `tests/exporters/test_perfetto_loss_track.py`, marked `fuzz`, settles the track layout
  against the real trace processor per [ADR-0014](0014-perfetto-integration-test-strategy.md).
