# ADR-0011: Show process lifetimes on one shared track, ordered by first event

- **Status:** Accepted
- **Date:** 2026-06-27 (ordering added 2026-06-28; laminar clipping added 2026-07-31)

## Context

[ADR-0010](0010-process-identity-cmdline-and-start-marker.md) gives each pid a
`Process <pid>` track and keeps it visible. Two gaps remained.

**Monitoring duration.** A process track in isolation says nothing about span, and nothing
groups the processes for cross-process comparison.

**Track order.** Process tracks came out in dict-insertion order, so the same input in a
different arrival order produced a differently-ordered trace. Perfetto's mechanism here is
`sibling_order_rank` on each process track descriptor, but it is consulted for process
tracks only when the special root descriptor at `uuid = 0` carries
`process_ordering = PROCESS_ORDERING_EXPLICIT`. This is the same OS-scoped-parent rule
[ADR-0003](0003-gc-metrics-group-track.md) ran into, seen from the other side: for process
and thread tracks, ordering is configured on the root rather than on the parent.

**Crossing spans.** Putting every pid on one track has a constraint the original design
missed: slices on a single Perfetto track are a *stack*. A `TYPE_SLICE_END` closes whatever
is open, so a pair that merely crosses (A starts first, B starts inside A, B ends after A)
cannot be expressed. Given pid 1111 `[100ms, 400ms]` and pid 2222 `[200ms, 600ms]`, the
trace processor returns pid 2222 with a 200ms duration and reports `misplaced_end_event: 1`.
The failure is quiet: the slice table still holds one row per pid, and only the durations are
wrong. gcmon monitors a process *tree*, so any two siblings whose lifetimes overlap without
nesting cross. The repository's own integration fixture crossed, and every assertion in the
suite passed anyway.

## Decision

**A single shared top-level track named `Processes`** holds one
`TYPE_SLICE_BEGIN`/`TYPE_SLICE_END` pair per pid, named `Process <pid>`, spanning that pid's
first to last non-meta event.

- Parented to the trace root, so `parent_uuid` is **absent on the wire**, not `0`, which is
  the reserved root descriptor ([ADR-0002](0002-perfetto-track-uuid-and-hierarchy.md)).
- No `process` / `thread` / `counter` sub-message, no `child_ordering` (it is a leaf: it has
  slice events, not child tracks), no `sibling_order_rank` (it is neither an
  explicit-ordered child nor a process or thread track, so the field would be ignored).
- Perfetto-only. Chrome JSON and JSONL are unchanged.

**A root `TrackDescriptor` at `uuid = 0`** is emitted once per trace with
`process_ordering = EXPLICIT` and `thread_ordering = EXPLICIT` (fields 19 and 20) and
nothing else: no name, no parent, no sub-message.

**Process tracks are ranked by first event timestamp**, ties broken by ascending pid,
sequential from 0. Only pids with at least one non-meta event get a rank.

**The whole track is emitted at encoder close**, via `finalize_perfetto_packets`, called once
from `ProtobufEventEncoder.close()`; convert passes record spans and emit nothing. Two
reasons the BEGIN cannot go out earlier: keeping the track laminar needs every pid's span in
hand at once, and a clip discovered at close cannot correct a BEGIN already written. Nor
could the END, since `BufferedTraceExporter` flushes in chunks of `flush_threshold`
(default 1000) and Perfetto pairs a BEGIN with the **first** matching END, orphaning the rest.

**Spans are clipped to a laminar set.** Sorted by ascending start, ties broken by longer span
first and then ascending pid, a stack sweep pulls each crossed span's end back to one
nanosecond before the span that crosses it. Nesting is untouched, so a parent outliving its
children costs nothing. Spans that merely touch (`A.end == B.start`) count as crossing,
because the relative order of an END and a BEGIN sharing a timestamp is not something the
wire format lets us pin down. Sorting longer-first on equal starts is what makes the clip
safe: two spans with the same start always nest, so a clip only happens when
`A.start < B.start`, and `B.start - 1` never lands before `A.start`.

**Every slice carries `real_start_ts` and `real_end_ts` debug annotations** on its BEGIN,
holding the span as observed. They go on *every* slice, not only clipped ones, so a consumer
reads the observed span without an annotation-present check. Where `ts`/`dur` and the
annotations disagree, the annotations are the truth.

**No span is ever dropped.** A pid observed at a single instant, and a pid clipped down to
nothing, both still get a BEGIN/END pair; the trace processor accepts it and reports
`dur = 0`. A missing slice would leave no record that the process was monitored at all.

**Counter events are excluded from the end timestamp**, though not from the start. The span
means *the range over which gcmon observed GC activity*, not *the range over which the
process was alive*. RSS samples are counter events ([ADR-0013](0013-rss-sampling.md)) emitted
on their own 1 Hz schedule with no GC work behind them, so letting them extend the span would
report sampler liveness as monitoring coverage. The span is
`[first non-meta event, last Begin/End/Instant event]`.

**The carve-out is provisional, and the two ends disagree today.** The start is a minimum
over *every* non-meta event, counters included, so it already means "first evidence the
process existed"; only the end means "last GC activity". When monitor-reported lifetime
lands, liveness becomes the definition of the whole span, and an RSS sample is evidence of it
on the same footing as the monitor's own observation, so the carve-out is expected to be
**removed** rather than extended to the start, making the span
`[first observed event, last observed event]`. That puts the end within one sample interval
of a process's death rather than at its last collection, which is the intent. It also makes
a `--rss` run report a wider span than a non-`--rss` run of the same workload, honest but
leaving spans comparable only across traces captured the same way.

## Consequences

- You can see each process's lifetime at a glance and compare across processes, and the same
  events in a different input order produce the same ranks.
- **A clipped slice under-reports how long the process was observed**, by an amount that
  depends on how close together the starts are, not on how much the spans overlap: the clip
  is to `later.start - 1`. Siblings fanning out from a fork loop start microseconds apart, so
  the losses are severe in ordinary use: 1000 children with nanosecond start jitter and
  varying lifetimes retain **0.37%** of their total observed duration. `--rss` makes it
  likelier still, since `RssSampler.tick` samples every live pid in one loop and counters
  move a span's start.
- **Which sibling gets sacrificed is not meaningful.** Within an RSS round the sample order is
  `set` iteration order, so hash order decides which pid gets the earliest start and is
  therefore clipped, rather than anything about the processes.
- **The drawn duration is a lower bound, never an upper one**, so deaths are misreported as
  early rather than late. `real_end_ts - real_start_ts` recovers what was observed;
  `docs/perfetto-sql.md` carries the query.
- **Exactly one slice per pid that did GC work**, so consumers may join `Processes` slices to
  pids one-to-one. A pid seen only through counters or only through meta events has none, and
  no rank either.
- **`sibling_order_rank` is not exposed as a SQL column.** It is a UI hint, so the
  trace-processor tests act as a *schema-validity guard*: they confirm the layout is
  accepted and the `process` and `track` tables survive intact, but only the Perfetto UI can
  assert display order. Perfetto's docs call these orderings "strong hints" in any case, so
  the UI may still rearrange tracks in special contexts.
- **Ranks are not applied retroactively.** If a pid's `ProcessMeta` lands in an earlier batch
  than its first non-meta event, the descriptor goes out before the rank is known, and
  emission is idempotent, so that pid gets no rank. Within a batch the pre-pass in
  `convert_trace_events_to_perfetto` folds every non-meta event into the span state *before*
  the main loop, so same-batch `ProcessMeta` still gets its rank.
- The `Processes` block lands at the end of the file, descriptor first. The trace processor
  resolves track references across the whole trace rather than in file order.
- Consumers enumerating slices must filter `track.name == 'Processes'`, as the equivalence
  test does, since these slices are Perfetto-only.

## Alternatives considered

- **One lifetime track per pid**, representing crossing spans exactly with no clipping.
  Rejected: gcmon runs on captures with hundreds to thousands of processes, and a track per
  pid makes the timeline unreadable. A collapsible parent group does not help; the row count
  is the problem.
- **Packing spans into lanes**, colouring the interval graph so the row count is maximum
  *concurrency* rather than process *count*. Strictly better than a track per pid (8 workers
  running 1000 tasks needs 8 rows), but rejected: N children alive at once are N mutually
  crossing intervals and still need N lanes, so the timeline is as unreadable as before.
- **Dropping a slice that ends up zero-length**, the original decision here. Reversed: it
  optimised the rendering at the cost of the record, and the pids likeliest to be clipped to
  nothing are exactly the short-lived children a reader is looking for.
- **Snapping near-equal starts together before the sweep**, turning a jittered fan-out back
  into the nesting it almost is; every end survives at a cost of at most ε on each start, and
  the 0.37% above becomes 100%. Not adopted, still open: ε is a heuristic, nesting N deep
  costs N rows of vertical space inside the track, and neither the trace processor's nor the
  UI's behaviour at that depth has been measured.
- **Extending the earlier span's end instead of clipping it**, nesting the later span inside.
  Rejected: it makes a dead process look alive, and the nesting implies a parent/child
  relationship that may not exist.
- **Clipping whichever side loses fewer nanoseconds.** Rejected: it makes the direction of
  the distortion depend on the data rather than on a stated rule.
- **Leaving crossing spans alone and documenting the mismatch.** Rejected: the durations are
  silently wrong, and `misplaced_end_event` is not something a reader of the UI would think
  to check.
- **OS-level process times via `psutil.Process(pid).create_time()`.** Rejected: the span
  should describe what gcmon observed, not when the OS started the process; the difference
  would be misread as monitoring coverage.
- **Emitting the slice END at the end of each convert call.** The original implementation,
  and wrong; see above.
- **Re-emitting a process descriptor with a corrected rank in a later batch.** Rejected: it
  breaks idempotent emission for a cosmetic gain in a rare ordering.

## Implementation

- `src/gcmon/exporters/perfetto_process_lifetime.py` holds this decision:
  `_PROCESS_LIFETIME_TRACK_NAME = "Processes"`, the three `_emit_process_lifetime_*`
  functions, `_record_process_lifetime`, `_clip_spans_to_laminar` and
  `finalize_perfetto_packets`.
- `src/gcmon/exporters/perfetto_proto.py`, `TrackDescriptorField.PROCESS_ORDERING = 19` and
  `THREAD_ORDERING = 20`. Fields 6 and 7 on the same message are `chrome_process` and
  `chrome_thread`, so a wrong number writes a different message and fails silently
  ([ADR-0001](0001-hand-rolled-perfetto-protobuf-encoder.md)).
- `src/gcmon/exporters/perfetto_track_state.py`,
  `PerfettoTrackState.update_process_lifetime`, the span accumulator; its `extends_end` flag
  is where the counter carve-out lives. `pop_process_lifetimes` applies the sort order the
  sweep depends on and drains once, so `finalize_perfetto_packets` is safe to call twice.
  `get_process_track_ranks` sorts by `(start_ts, pid)`.
- `_clip_spans_to_laminar`, the stack sweep, carries each span's observed start and end
  through untouched alongside the drawn ones, so the emission site can annotate every slice
  without knowing which fields the sweep may have moved.
- `src/gcmon/exporters/perfetto_format.py`, `_emit_root_descriptor`, guarded by
  `has_root_descriptor`.
- `tests/exporters/test_perfetto_format.py`: `TestClipSpansToLaminar` covers the sweep
  directly at full statement and branch coverage; `TestProcessLifetimeLaminarClipping` covers
  the same shapes through `finalize_perfetto_packets` and additionally checks the emitted
  BEGIN/ENDs form a well-formed stack; `TestProcessLifetimeState` for the accumulator.
- `tests/exporters/test_perfetto_exporter_integration.py`: `TestCrossingProcessSpans` asserts
  `misplaced_end_event == 0` against a deliberately crossing trace,
  `TestZeroDurationProcessSpans` that a same-ts BEGIN/END is paired rather than orphaned and
  that every pid keeps a slice, and
  `TestMultiFlushProcessesTrack::test_slice_end_is_last_event_ts`, which forces many flushes
  with `flush_threshold=5` and asserts `slice.ts + slice.dur == last_event_ts`.
