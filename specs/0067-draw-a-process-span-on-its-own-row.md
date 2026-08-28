# 0067: Draw a process's span on its own row, untruncated

- **Status:** Not started
- **Kind:** feature (enhancement)
- **Effort:** M
- **Origin:** the clipping loss
  [ADR-0011](../docs/adr/0011-process-lifetime-and-ordering.md) measures and
  `docs/formats.md` warns readers about, met with the observation that the row
  which could draw the interval is occupied by a marker that draws nothing
- **Respects:**
  [ADR-0002](../docs/adr/0002-perfetto-track-uuid-and-hierarchy.md) (uuid
  allocation and parenting),
  [ADR-0010](../docs/adr/0010-process-identity-cmdline-and-start-marker.md)
  (the cmdline written twice; this spec supersedes its `Start Process`
  marker),
  [ADR-0011](../docs/adr/0011-process-lifetime-and-ordering.md) (the
  `Processes` track, its clipping and its ordering; this spec adds a second
  drawing and changes none of it),
  [ADR-0012](../docs/adr/0012-trace-output-formats.md) (a Perfetto-only
  feature is allowed to be Perfetto-only),
  [ADR-0014](../docs/adr/0014-perfetto-integration-test-strategy.md) (no new
  suite behind a marker),
  [ADR-0024](../docs/adr/0024-an-event-names-the-track-it-is-drawn-on.md) (an
  event names its `Track`; this span is encoder-built and adds no `Track`)

## 1. Problem statement

An operator opens a trace of a fan-out and finds `Process 4821` on the
`Processes` track drawn as a hairline. The process ran for seconds and the
slice is nanoseconds wide, and nothing on screen says why.

The reason is in
[ADR-0011](../docs/adr/0011-process-lifetime-and-ordering.md): slices on one
Perfetto track are a stack, so a shared track has to be laminar, and the sweep
pulls a crossed span's end back to one nanosecond before the span that crosses
it. Siblings fanning out from a fork loop start microseconds apart, so they
cross rather than nest. That ADR measures what survives: 1000 children with
nanosecond start jitter and varying lifetimes retain **0.37%** of their total
observed duration.

The interval is not lost. It is on the slice as `real_start_ts` and
`real_end_ts`, and `docs/perfetto-sql.md` carries the join that recovers it.
`docs/formats.md` tells the operator outright to read the annotations instead
of the drawing:

> **Read a process's span from the `real_start_ts` and `real_end_ts`
> annotations, not from the slice width**, which overlapping processes cut
> short and sometimes to nothing.

A trace that has to warn a reader off what it drew is one row short, not one
annotation short.

The row that could draw it is occupied. Each process has a track of its own,
and the only thing on it is a zero-duration instant named `Start Process`,
which
[ADR-0010](../docs/adr/0010-process-identity-cmdline-and-start-marker.md)
added so the Perfetto UI would render the row's `description`, the joined
command line. It marks nothing an operator asked about, carries no
annotations, and costs a name filter in every query that enumerates slices.

## 2. Solution

A process's own row draws its span, from the first thing gcmon observed of
that process to the last, with nothing clipped. The row belongs to one
process, so no sibling can cross it and there is nothing to clip against.

The bar is named `Process Span` and carries the four annotations the
`Processes` slice carries — `cmdline`, `real_start_ts`, `real_end_ts` and
`pid_epoch` — so a reader who clicks either bar reads the same fields.

The `Start Process` marker goes. Keeping the row rendering was its only job,
and a slice does that.

A span is therefore drawn twice: clipped on the `Processes` track, which is
what keeps the cross-process overview one row tall, and tight on the process's
own row. Where the two disagree the process's own row is right, and the
`real_*` annotations on both drawings say the same thing.

`--format jsonl`, `--format stdout` and Chrome output are unchanged.

## 3. User stories

1. As someone reading a trace of a fan-out, I want a process's observed
   duration drawn somewhere, so that reading it is opening a row rather than
   writing a join.
2. As someone who clicks the bar, I want the command line and the epoch on it,
   so that identifying the process is one click rather than a hunt through the
   track descriptor.
3. As someone querying a trace, I want one slice per process carrying the
   tight interval, so that a duration aggregate is a `GROUP BY upid` over one
   slice name instead of a comparison between a drawing and its annotations.
4. As someone who followed `docs/formats.md` and filtered `Start Process` out
   of a query, I want to be told the name is gone, so that my filter is not
   silently matching nothing while a new synthetic slice arrives to take its
   place.
5. As someone reading the `Processes` track, I want it unchanged, so that
   every query and habit built on it still works.
6. As a user of `--format jsonl`, I want my output byte-identical, so that a
   Perfetto-only feature stays Perfetto-only.
7. As an operator on a tree of short-lived children, I want no row invented
   for a process gcmon only ever saw alive, so that the UI does not fill with
   groups holding nothing.

## 4. Implementation decisions

**The second drawing is tight, and the two drawings may disagree on screen.**
A process track holds one span and never crosses anything, so
`_clip_spans_to_laminar` has no work to do for it and is not consulted. On a
crossing fan-out an operator therefore sees one process with two visibly
different durations, a hairline on `Processes` and a full bar in its own
group. That is
[ADR-0011](../docs/adr/0011-process-lifetime-and-ordering.md)'s existing
distortion becoming visible rather than a new one: "the drawn duration is a
lower bound, never an upper one" stays true, and the process row is where the
bound is tight. The cost is accepted deliberately: a reader who has read
neither ADR sees two numbers and no on-screen cue which to believe, and prose
in `docs/formats.md` and ADR-0025 is what answers them.

Rejected: **clipping the process-track span the same way**, so the two rows
always agree. It costs the whole point, since the fan-out children would be
hairlines in both places and the trace would still draw the real interval
nowhere.

Rejected: **packing the `Processes` track into lanes** so nothing ever
diverges.
[ADR-0011](../docs/adr/0011-process-lifetime-and-ordering.md) rejected it
already: N children alive at once are N mutually crossing intervals and need N
lanes, so the timeline is as unreadable as a track per pid.

**Both ends are emitted at close, from `finalize_perfetto_packets`.** It
already walks every recorded span to emit the `Processes` slices; the
process-track span for the same process comes off the same accumulator entry,
taking `real_start` / `real_end` where the `Processes` slice takes the clipped
pair. Emitting the BEGIN early instead, where the marker sits today, would put
the two drawings' starts under two owners: `PerfettoTrackState` folds
**liveness observations** into a span, and those never pass through
`convert_trace_events_to_perfetto`, so a process whose first liveness tick
predates its first GC event would get a BEGIN at the event and a
`real_start_ts` of the tick. The rows would then disagree about the start as
well as the end, and that disagreement would be a defect rather than the
deliberate clip above. One tuple read twice cannot drift.

The packets join the block `finalize_perfetto_packets` already returns. Their
position in it is free: the trace processor sorts by timestamp and breaks ties
by position only within one track, and these sit on a track that carries
nothing else with a colliding timestamp except the marks below.

**The span is named `Process Span`, one constant string for every process.** A
per-process name would extend
[0066](0066-give-each-process-on-a-reused-pid-its-own-track.md)'s
matching-strings principle to a third place, but no constant could then filter
it: every consumer that today writes `s.name != 'Start Process'` would have to
filter by track type instead, and the name would collide with the `Processes`
slice of the same process, so a bare `GROUP BY s.name` would double-count.
Which of the two drawings a slice is stays readable from the track it sits on.

`Observed` was the first candidate and is **rejected on the glossary**:
`CONTEXT.md` already defines an **observed span** as the interval from the
first record gcmon read on a *ring* to the last, and a slice of that name on a
process row invites the two to be read as one thing. `Lifetime` is on the
`_Avoid_` list and reserved for the `Processes` span; `Alive` would claim what
[ADR-0011](../docs/adr/0011-process-lifetime-and-ordering.md) refused to claim
when it rejected `psutil.Process(pid).create_time()`, since the span describes
what gcmon observed and not when the process existed.

**The annotation set is identical on both drawings**: `cmdline`,
`real_start_ts`, `real_end_ts`, `pid_epoch`. On the process-track span the
`real_*` pair is tautological, exactly `ts` and `ts + dur`, and that is the
point.
[ADR-0011](../docs/adr/0011-process-lifetime-and-ordering.md) already put that
pair on *every* `Processes` slice rather than only clipped ones "so a consumer
never has to check whether a clip happened"; the same rule one scope wider
means a consumer never has to check which track it is on either. It also makes
"where the drawn interval and the annotations disagree, the annotations are
the truth" hold everywhere instead of holding on one track and being vacuous
on the other. The cost is two varints per process.

Rejected: **annotating the span with `sibling_order_rank`.**
[ADR-0011](../docs/adr/0011-process-lifetime-and-ordering.md) records that it
is a UI hint with no SQL column and that the UI may rearrange tracks anyway.
An annotation would turn it into data a reader could analyse by and be wrong,
and it says nothing about the interval this span draws.

**Emission is gated on the process having a descriptor**, that is on
`PerfettoTrackState.has_pid(pid, pid_epoch)`; the `Processes` slice stays
ungated. `get_process_track_uuid` allocates on demand, so calling it for a
process without a descriptor would mint a uuid nothing describes and emit a
slice naming a track the trace processor never heard of. The set is exact:
`convert_trace_events_to_perfetto` emits a descriptor only from an event,
every event is folded into the accumulator by the pre-pass, so every process
with a descriptor has a span and no other process does. The invariant that
follows is queryable: **the `Process Span` slices are a subset of the
`Processes` slices, and the difference is exactly the set of processes gcmon
only ever saw alive.**

Rejected: **giving every process with a span a descriptor**, so the two sets
coincide. It draws an empty group — no cmdline, no thread row, no counters —
for every transient child that never collected, which on the 1862-process run
[0059](0059-say-which-process-held-a-pid-in-the-trace.md) measured is most of
them.
[ADR-0011](../docs/adr/0011-process-lifetime-and-ordering.md) scoped it out
already.

**The workload's marks nest inside the span, except one.** A `gcmon:` mark is
an `Instant` on the process track and every event widens the span, so the span
contains every mark by construction and marks land at `depth = 1`. One case
ties: the mark that *is* the process's first observation shares a timestamp
with the BEGIN, and the trace processor breaks ties by position in the
sequence. The mark is written during convert and the BEGIN at close, so the
mark sorts first and lands at `depth = 0`, beside the bar rather than inside
it. The other end is safe, since a mark at `real_end` still precedes the END
in file order. Both are pinned by a test rather than left to be discovered.

Rejected: **widening the span by a nanosecond at each end** so no tie is
possible. It makes `ts` / `dur` disagree with `real_start_ts` / `real_end_ts`,
which destroys both the tight-bound claim and the annotation rule above, to
move one mark.

**The `Start Process` marker is deleted**, with `_emit_start_process_marker`,
`_maybe_emit_start_process_marker`, `_START_PROCESS_INSTANT_NAME` and
`PerfettoTrackState`'s `has_start_process_marker` /
`mark_start_process_marker`. Both emissions gate on having had an event, so
the sets coincide and no process loses its rendering in a run that closes.

**A killed run loses the command line from the UI**, and that is accepted. The
marker rode out in-batch, so it reached the file with whatever batch was
already written; the span goes out at close. On SIGKILL `close()` never runs,
so a process track whose process emitted no workload mark has no event, and
the Perfetto UI hides a track's `description` when the track is empty — the
failure
[ADR-0010](../docs/adr/0010-process-identity-cmdline-and-start-marker.md)
invented the marker for, returning on the crash path. Such a run already loses
the whole `Processes` track, every span and the batch in flight, so this is
consistent with what is already gone rather than a new class of loss.

Rejected: **keeping the marker as crash insurance** beside the span. It taxes
every correct trace with a second synthetic event per process, forever, to
improve a trace that is already truncated.

Rejected: **emitting the BEGIN in-batch and the END at close**, so a killed
run keeps at least the BEGIN. The trace processor reads an unclosed BEGIN as
`dur = -1`, which trades an absent fact for a false one.

**`combine` is affected identically, not unaffected.** An offline conversion
builds descriptors and records spans like any other run, and nothing clips on
a per-process track, so the code path is the same and every process in a
combined trace gets a `Process Span`. Its spans stay narrower than a live
run's because nothing reports liveness, which
[ADR-0011](../docs/adr/0011-process-lifetime-and-ordering.md) already records.

**A measurement to take first**, in the manner of
[0066](0066-give-each-process-on-a-reused-pid-its-own-track.md) section 4 and
against the `trace_processor` the suite pins in `tests/perfetto_prebuilt.py`
(v58.2): confirm that a `dur > 0` slice on an OS-scoped process track is
routed to that process's `upid`, that instants inside it read at `depth = 1`,
and that no non-info stat is raised. The trace has never carried this shape —
the process track has held instants only — and if the processor treats it
differently the rest of this spec is moot.

## 5. Seams and testing decisions

- **Seam:** a trace-processor SQL assertion over `slice`, `process_track`,
  `process` and `args`. It is the highest seam that can observe the change,
  and per CONVENTIONS rule 6 it asserts what the trace means rather than that
  the bytes round-tripped. A byte assertion cannot tell a slice the processor
  routed to the right `upid` from one it merged or dropped.
- **New seam needed:** none.
  `tests/exporters/test_perfetto_exporter_integration.py` already drives the
  real `trace_processor` and already carries both fixtures the interesting
  cases need: a deliberately **crossing** trace, the one asserting
  `misplaced_end_event == 0`, which is the only shape where clipping bites,
  and `reused_pid_trace_processor`. Its `TestStartProcessMarker` is the class
  this change repurposes.
- **What makes a good test here:** assert the tight duration against a
  *crossing* fixture, where the two drawings differ. The same assertion on a
  non-crossing trace passes whether or not anything was implemented, because
  clipped and unclipped agree there.
- **Prior art:** `TestStartProcessMarker` and `TestReusedPidSpans` in
  `tests/exporters/test_perfetto_exporter_integration.py`;
  `tests/exporters/test_perfetto_process_lifetime.py` for finalization.
- **Not the fuzz suite.**
  `tests/exporters/test_perfetto_emission_order_fuzz.py` exists because
  laminar clipping is combinatorial, many spans on one track interacting. A
  `Process Span` is alone on its track and crosses nothing, and its one
  ordering interaction is the deterministic tie above, which one test pins
  exactly.
- **Cases:**
  1. On the crossing fixture, a process whose `Processes` slice was clipped
     has a `Process Span` whose `dur` is the unclipped interval, and the two
     differ.
  2. All four annotations are present on it and read back through `args` with
     the right values.
  3. On the reused pid: two `upid`s, one `Process Span` each, each carrying
     its own `cmdline` and its own `pid_epoch`.
  4. A process gcmon only saw alive has a `Processes` slice, no `Process Span`
     and no process track.
  5. Marks read at `depth = 1`, and the mark that is the process's first
     observation at `depth = 0`.
  6. `misplaced_end_event == 0` and no other non-info stat, on a shape the
     trace has never carried.
  7. `--format jsonl` and Chrome output stay byte-identical, still asserted on
     the bytes.
  8. `Start Process` appears nowhere in the trace.
- **Repairs the change forces**, none of them optional:
  `tests/exporters/test_perfetto_loss_track.py` filters real slices with
  `WHERE s.depth = 0 AND s.name != 'Start Process'` and both halves break, and
  `test_the_process_marker_is_untouched_by_loss_spans` selects on the marker
  name to prove the `GC Loss` track did not reparent;
  `tests/exporters/test_exporter_thread_safety.py`,
  `tests/exporters/test_perfetto_format.py` and
  `tests/exporters/test_perfetto_slice_expansion.py` count or name the marker;
  `tests/fixtures/monitored_run_perfetto_trace.txt` is regenerated, and the
  SQL assertions above carry the weight the byte fixture used to.

## 6. Out of scope

- **Moving the workload's marks onto a track of their own**, so nothing nests.
  A better UI and a real idea, but it redefines what
  `Instant(ProcessTrack(pid))` means and what `CONTEXT.md` says a **Track**
  is. Its own spec.
- **A `Process Span` for a process gcmon only saw alive.** It needs a process
  descriptor, which
  [ADR-0011](../docs/adr/0011-process-lifetime-and-ordering.md) scoped out,
  and the group it would draw holds nothing.
- **Un-clipping the `Processes` track, or packing it into lanes.** Rejected in
  section 4 and in
  [ADR-0011](../docs/adr/0011-process-lifetime-and-ordering.md) before it.
- **Restoring the command line rendering on a killed run.** Accepted loss,
  recorded as a consequence.
- **Chrome and JSONL.** Perfetto-only, per
  [ADR-0012](../docs/adr/0012-trace-output-formats.md).
- **Bounding nesting depth.**
  [ADR-0011](../docs/adr/0011-process-lifetime-and-ordering.md)'s 512-slice
  limit sits in the reader; a process track reaches depth 2.
- **Any UI cue reconciling the two durations.** Perfetto offers no mechanism,
  so ADR-0025 and `docs/formats.md` carry it in prose.
- **Correcting
  [ADR-0011](../docs/adr/0011-process-lifetime-and-ordering.md)'s
  `ProcessMeta` consequence**, which says a descriptor can go out before a
  rank is known because a pid's `ProcessMeta` landed in an earlier batch.
  `convert_trace_events_to_perfetto` emits a descriptor only from an event and
  `_ensure_cmdline` hangs off the same path, so the sentence appears to
  describe code that no longer exists. Confirmed and corrected by whoever owns
  it; this spec neither relies on it nor changes it.

## 7. Further notes

**Landing this writes ADR-0025**, which owns the decision that a span is drawn
twice and that the second drawing is tight, and the `Process Span` that
carries it.
[ADR-0011](../docs/adr/0011-process-lifetime-and-ordering.md) keeps its title
and its scope — it still decides everything about the shared track — and gains
a dated pointer.
[ADR-0010](../docs/adr/0010-process-identity-cmdline-and-start-marker.md)
gains one too, with its render-forcing mechanism amended from marker to span
and the killed-run consequence recorded. A new record rather than an amendment
because ADR-0011 is titled "Show process lifetimes on **one shared track**",
which an amendment would falsify.

**`CONTEXT.md` gains one sentence** on the **Span** entry: a span is drawn on
the `Processes` track clipped and on the process's own row tight, and where
they differ the second is right. It stays one term; the two slices are one
interval drawn to different ends, and a second noun would tell every reader
they mean different things. The neighbouring **Observed span** entry, which is
about a ring, is left alone and is the reason `Observed` was not spent on this
slice.

**The CHANGELOG takes two entries.** A **Breaking changes** entry: the
`Start Process` marker is gone, a PerfettoSQL query matching that name matches
nothing, and every workload mark sits one level deeper. And a **Features**
entry for the span. The WIP section already carries the precedent for the
first, in the `heap_size` counter track rename.

**Docs.** `docs/formats.md` replaces its `Start Process` bullet and softens
the warning that sends a reader to the annotations, which now has a drawn
answer. `docs/perfetto-sql.md` gains a query reading the tight interval
directly, beside the join it keeps for the `Processes` track.

**Order: after
[0066](0066-give-each-process-on-a-reused-pid-its-own-track.md) has landed**,
and not before its fixture is stable. This edits `_emit_start_process_marker`
and `finalize_perfetto_packets`, both of which 0066 is mid-rewrite on, and it
breaks 0066's byte-identical guarantee deliberately. Regenerating one golden
file against two in-flight changes is how a defect gets certified as
intentional.
