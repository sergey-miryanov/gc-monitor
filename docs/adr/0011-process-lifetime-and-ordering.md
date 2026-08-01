# ADR-0011: Show process lifetimes on one shared track, ordered by first event

- **Status:** Accepted
- **Date:** 2026-06-27 (ordering added 2026-06-28; laminar clipping added 2026-07-31)

## Context

[ADR-0010](0010-process-identity-cmdline-and-start-marker.md) gives each pid a
`Process <pid>` track and keeps it visible. Two gaps remained.

**Monitoring duration.** A process track in isolation says nothing about
span, and nothing groups the processes together for cross-process comparison.

**Track order.** The process tracks came out in dict-insertion order, so
the same input in a different arrival order produced a differently-ordered trace. On a
multi-process capture you expect chronological order.

Perfetto's mechanism for the second is `sibling_order_rank` on each process track
descriptor, but Perfetto consults that field for process tracks only when the special root
track descriptor at `uuid = 0` carries `process_ordering = PROCESS_ORDERING_EXPLICIT`. This
is the same OS-scoped-parent rule that [ADR-0003](0003-gc-metrics-group-track.md) ran into,
seen from the other side: for process and thread tracks, ordering is configured on the root
descriptor rather than on the parent.

**Crossing spans.** Putting every pid on one track has a constraint the original
design missed: slices on a single Perfetto track are a *stack*. A `TYPE_SLICE_END`
closes whatever is open, so a pair that merely crosses — A starts first, B starts
inside A, B ends after A — cannot be expressed. Feeding the trace processor
pid 1111 `[100ms, 400ms]` and pid 2222 `[200ms, 600ms]` returns pid 2222 with a
200ms duration instead of 400ms and reports `misplaced_end_event: 1`: one END had
nothing left to close and was discarded. The failure is quiet, because the slice
table still holds one row per pid and only the durations are wrong.

This is not a corner case. gcmon monitors a process *tree*, and any two siblings
whose lifetimes overlap without nesting cross. The repository's own integration
fixture crosses, and every assertion in the suite passed anyway.

## Decision

**A single shared top-level track named `Processes`** holds one
`TYPE_SLICE_BEGIN`/`TYPE_SLICE_END` pair per pid, named `Process <pid>`, spanning that
pid's first to last non-meta event.

- It is parented to the trace root, meaning `parent_uuid` is **absent on the wire**, and
  not `0`, the reserved root descriptor ([ADR-0002](0002-perfetto-track-uuid-and-hierarchy.md)).
- It carries no `process` / `thread` / `counter` sub-message, no `child_ordering` (it is a
  leaf; it has slice events, not child tracks) and no `sibling_order_rank` (it is neither
  an explicit-ordered child nor a process or thread track, so the field would be ignored).
- Perfetto-only. Chrome JSON and JSONL are unchanged.

**A root `TrackDescriptor` at `uuid = 0`** is emitted exactly once per trace with
`process_ordering = EXPLICIT` and `thread_ordering = EXPLICIT` (fields 19 and 20), and
nothing else: no name, no parent, no sub-message.

**Process tracks are ranked by first event timestamp**, ties broken by ascending pid,
sequential from 0. Only pids with at least one non-meta event get a rank.

**The whole track is emitted at encoder close**, via `finalize_perfetto_packets`, called
once from `ProtobufEventEncoder.close()`: the track descriptor, then both ends of every
pid's pair. Convert passes record spans and emit nothing. Two reasons the BEGIN cannot go
out earlier. Keeping the track laminar needs every pid's span in hand at once, and a clip
discovered at close has no way to correct a BEGIN already written. `BufferedTraceExporter`
flushes in chunks of `flush_threshold` (default 1000), so a long trace makes many convert
calls; per-call emission would put one BEGIN and N ENDs on the wire per pid, and Perfetto
pairs a BEGIN with the **first** matching END and orphans the rest.

**Spans are clipped to a laminar set.** Sorted by ascending start, ties broken by longer
span first and then ascending pid, a stack sweep pulls each crossed span's end back to one
nanosecond before the span that crosses it. Nesting is left untouched, so a parent
outliving its children costs nothing. Spans that merely touch (`A.end == B.start`) are
treated as crossing, because the relative order of an END and a BEGIN sharing a timestamp
is not something the wire format lets us pin down. Sorting longer-span-first on equal
starts is what makes this safe: two spans with the same start always nest, so a clip only
happens when `A.start < B.start`, and `B.start - 1` therefore never lands before `A.start`.

**Every slice carries `real_start_ts` and `real_end_ts` debug annotations** on its BEGIN,
holding the span as observed. They go on *every* slice, not only clipped ones, so a
consumer reads the observed span the same way regardless of what the drawing had to give
up — no annotation-present check, no branch. The slice's own `ts` and `dur` are what could
be drawn; where the two disagree, the annotations are the truth.

**No span is ever dropped.** A pid observed at a single instant, and a pid clipped down to
nothing, both still get a BEGIN/END pair — a zero-duration slice, which the trace processor
accepts and reports as `dur = 0`. Drawing a hairline overstates nothing: the drawn duration
is the *lower* bound and the annotations carry the real one. Dropping the slice, by
contrast, removes the only record that the process was monitored at all, and an absence is
the one distortion a reader has no way to detect. Between a slice that is hard to see and a
process that is impossible to find, the first is the lesser failure.

**Counter events are excluded from the end timestamp**, though not from the start. The span
means *the range over which gcmon observed GC activity*, not *the range over which the
process was alive*. RSS samples are counter events ([ADR-0013](0013-rss-sampling.md)) emitted
on their own 1 Hz schedule with no GC work behind them, and letting them extend the span
would report sampler liveness as monitoring coverage. The span is
`[first non-meta event, last Begin/End/Instant event]`.

**This carve-out is provisional, and the two ends do not currently agree.** The start is a
minimum over *every* non-meta event, counters included, so it already means "first evidence
the process existed"; only the end means "last GC activity". When monitor-reported lifetime
lands, liveness becomes the definition of the whole span, and an RSS sample is evidence of
it on exactly the same footing as the monitor's own observation — both say *this process
existed at time T* with no GC behind them, and admitting one while rejecting the other would
be arbitrary. The carve-out is then expected to be **removed** rather than extended to the
start, making the span `[first observed event, last observed event]`. Two things to weigh
when that happens: the end would land within one sample interval of a process's death rather
than at its last collection, which is the point; and a `--rss` run would report a wider span
than a non-`--rss` run of the same workload, which is honest — more was observed — but means
spans are only comparable across traces captured the same way.

## Consequences

- You can see each monitored process's lifetime at a glance and compare across processes.
- Traces are reproducible: the same events in a different input order produce the same
  ranks.
- **A clipped slice under-reports how long the process was observed**, and how badly
  depends on how close together the starts are, not on how much the spans overlap. A clip
  pulls the end back to `later.start - 1`, so a span crossed by one starting two seconds
  later keeps a visible two-second stub, while the same span crossed by one starting a
  microsecond later keeps a microsecond. Concurrent siblings that fan out from a single
  fork loop start microseconds apart, so this is the normal case, not the limiting one:
  1000 children with nanosecond start jitter and varying lifetimes retain **0.37%** of
  their total observed duration on the track. `--rss` makes it likelier still, because
  `RssSampler.tick` samples every live pid in one loop and counters move a span's start, so
  every pid live in a given round starts within microseconds of every other one.
- **Which sibling gets sacrificed is not meaningful.** Within an RSS round the sample order
  is `set` iteration order, so the pid that ends up with the earliest start — and therefore
  gets clipped — is decided by hash order rather than by anything about the processes.
- **The drawn duration is a lower bound, never an upper one.** Both distortions the track
  can apply — clipping an end, and drawing a zero-length span — shorten. A `Processes` slice
  never claims more lifetime than gcmon observed, and `real_end_ts - real_start_ts` recovers
  what it did observe. `docs/perfetto-sql.md` carries the query.
- **Deaths are misreported as early, never as late.** Given the choice of which side of a
  crossing to distort, making a live process look dead is the safer error for a GC monitor
  than making a dead one look alive.
- **There is exactly one slice per pid that did GC work**, so consumers may join `Processes`
  slices to pids one-to-one. A pid seen only through counters or only through meta events
  still has none.
- **`sibling_order_rank` is not exposed as a SQL column.** It is a UI rendering hint, so the
  trace-processor tests act as a *schema-validity guard*: they confirm the trace
  processor accepts the new layout and that the `process` and `track` tables survive
  intact. They cannot assert display order. Only the Perfetto UI can.
- Perfetto's docs call these orderings "strong hints"; the UI may still rearrange tracks in
  special contexts.
- **Ranks are not applied retroactively.** If a pid's `ProcessMeta` lands in an
  earlier batch than its first non-meta event, the descriptor goes out before the rank is
  known, and descriptor emission is idempotent, so that pid gets no rank. The wire format
  stays correct: a rank is present only when it was known at emission time. Within a single
  batch the pre-pass in `convert_trace_events_to_perfetto` folds every non-meta event into
  the span state *before* the main loop, so same-batch `ProcessMeta` still gets its rank.
- A pid seen only through `ProcessMeta` / `ThreadMeta` gets no lifetime slice and no rank.
- The whole `Processes` block lands at the end of the file, after the events its slices
  span. The descriptor leads the block, so it still precedes its own slices. The trace
  processor accepts either arrangement; it resolves track references across the whole
  trace rather than in file order.
- Consumers enumerating slices must filter `track.name == 'Processes'`, as the equivalence
  test does, since these slices are Perfetto-only.

## Alternatives considered

- **One lifetime track per pid**, which would represent crossing spans exactly, with no
  clipping. Rejected: gcmon is used on captures with hundreds to thousands of processes,
  and a track per pid makes the timeline unreadable at that scale. Parenting them to a
  collapsible group does not help; the row count is the problem.
- **Packing spans into lanes** — colouring the interval graph and giving each colour its own
  track, so the row count is the maximum *concurrency* rather than the process *count*.
  Strictly better than a track per pid: a pool of 8 workers running 1000 tasks needs 8 rows,
  not 1000. Rejected because it does not address the shape that actually hurts. A fan-out of
  N children alive at once has N mutually crossing intervals, so it needs N lanes — the same
  unreadable timeline, reached by a longer route.
- **Dropping a slice that ends up zero-length**, which was the original decision here.
  Reversed: it optimised the rendering at the cost of the record. A hairline slice is hard
  to see, but a missing one is impossible to find, and the pids most likely to be clipped to
  nothing are exactly the short-lived children a reader is looking for. A drawn slice with
  `real_*` annotations is legible to SQL whatever its width.
- **Snapping near-equal starts together before the sweep**, converting a jittered fan-out
  back into the nesting it almost is. Spans with exactly equal starts always nest, so this
  would preserve every end truthfully at a cost of at most ε on each start — the 0.37%
  above becomes 100%. Not adopted here, and still open: ε is a heuristic, nesting N deep
  costs N rows of vertical space inside the track rather than N tracks, and neither the
  trace processor's nor the UI's behaviour at that depth has been measured.
- **Extending the earlier span's end instead of clipping it**, so the later span nests
  inside it. Rejected: it makes a dead process look alive, and the resulting nesting implies
  a parent/child relationship between pids that may not exist.
- **Clipping whichever side loses fewer nanoseconds.** Rejected: unpredictable, and it makes
  the direction of the distortion depend on the data rather than on a stated rule.
- **Leaving crossing spans alone and documenting the mismatch.** Rejected: the trace
  processor silently produces wrong durations, and `misplaced_end_event` is not something a
  reader of the UI would think to check.
- **OS-level process times via `psutil.Process(pid).create_time()`.** Rejected: the span
  should describe what gcmon observed, not when the OS started the process. Those differ,
  and the difference would be misread as monitoring coverage.
- **Emitting the slice END at the end of each convert call.** This was the original
  implementation and it is wrong; see above.
- **Re-emitting a process descriptor with a corrected rank in a later batch.** Rejected:
  it breaks idempotent emission for a cosmetic gain in a rare ordering.

## Implementation

- `src/gcmon/exporters/perfetto_format.py:194`, `_PROCESS_LIFETIME_TRACK_NAME = "Processes"`.
- `:55-56`, `PROCESS_ORDERING = 19`, `THREAD_ORDERING = 20`. Fields 6 and 7 on the same
  message are `chrome_process` and `chrome_thread`, so a wrong number here writes a
  different message and fails silently ([ADR-0001](0001-hand-rolled-perfetto-protobuf-encoder.md)).
- `PerfettoTrackState.update_process_lifetime`, the single span accumulator; its
  `extends_end` flag is where the counter carve-out lives.
- `PerfettoTrackState.pop_process_lifetimes`, which applies the sort order the sweep
  depends on and drains once so `finalize_perfetto_packets` is safe to call twice.
- `_clip_spans_to_laminar`, the stack sweep. It carries each span's observed start and end
  through untouched alongside the drawn ones, so the emission site annotates every slice
  without needing to know which fields the sweep may have moved.
- `finalize_perfetto_packets`, the single emission point at encoder close.
- `_emit_root_descriptor`, guarded by `has_root_descriptor` so it fires once.
- `get_process_track_ranks()`, sorting by `(start_ts, pid)`.
- Tests: `tests/exporters/test_perfetto_format.py`,
  `TestProcessLifetimeLaminarClipping` (crossing, containment, disjoint, touching, equal
  starts, a span crossed by two later spans, both zero-length cases, and a randomized
  property test asserting the output is always laminar, that no pid is ever dropped, and
  that an end is only ever pulled in); `TestProcessLifetimeState` for the span accumulator;
  `TestConvertItemToPerfettoPackets::test_no_closeout_emitted_during_convert`.
  `tests/exporters/test_perfetto_exporter_integration.py`, `TestCrossingProcessSpans`
  (asserts `misplaced_end_event == 0` against a deliberately crossing trace),
  `TestZeroDurationProcessSpans` (a pid seen at a single instant and a pid clipped to
  nothing; asserts the trace processor pairs a same-ts BEGIN/END rather than orphaning the
  END, reports `dur = 0`, and keeps a slice for all three pids), and
  `TestProcessesTrack::test_slice_per_pid`, which asserts per-pid timestamps rather than
  just the row count;
  `TestMultiFlushProcessesTrack::test_slice_end_is_last_event_ts` forces many flushes with
  `flush_threshold=5` and asserts `slice.ts + slice.dur == last_event_ts`.
