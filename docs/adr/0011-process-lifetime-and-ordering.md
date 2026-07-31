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

**A clipped slice carries a `clipped_from_ts` debug annotation** on its BEGIN, holding the
end it would have had. The rendering loses the truth; the trace does not.

**A span that ends up zero-length is dropped entirely** — no BEGIN, no END — and logged at
debug. That covers a pid observed at a single instant and a pid clipped down to nothing. A
zero-duration slice renders as an instant and would claim a lifetime the trace cannot
support.

**Counter events are excluded from the end timestamp**, though not from the start. The span
means *the range over which gcmon observed GC activity*, not *the range over which the
process was alive*. RSS samples are counter events ([ADR-0013](0013-rss-sampling.md)) emitted
on their own 1 Hz schedule with no GC work behind them, and letting them extend the span
would report sampler liveness as monitoring coverage. The span is
`[first non-meta event, last Begin/End/Instant event]`.

## Consequences

- You can see each monitored process's lifetime at a glance and compare across processes.
- Traces are reproducible: the same events in a different input order produce the same
  ranks.
- **A clipped slice under-reports how long the process was observed**, and the more
  processes run concurrently with staggered starts, the more of them get clipped. In the
  limit — many siblings, all crossing — the track degenerates into a row of slivers. This
  is the price of one shared track; see the alternatives below for what the other prices
  were.
- **Deaths are misreported as early, never as late.** Given the choice of which side of a
  crossing to distort, making a live process look dead is the safer error for a GC monitor
  than making a dead one look alive.
- **There is not always one slice per pid.** Consumers must not join `Processes` slices to
  pids one-to-one. `docs/perfetto-sql.md` carries a query for recovering observed durations
  from `clipped_from_ts`.
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
- The `Processes` track descriptor is written after the slices that reference its uuid.
  The trace processor accepts this; it resolves track references across the whole trace
  rather than in file order.
- Consumers enumerating slices must filter `track.name == 'Processes'`, as the equivalence
  test does, since these slices are Perfetto-only.

## Alternatives considered

- **One lifetime track per pid**, which would represent crossing spans exactly, with no
  clipping and no dropped slices. Rejected: gcmon is used on captures with hundreds to
  thousands of processes, and a track per pid makes the timeline unreadable at that scale.
  Parenting them to a collapsible group does not help; the row count is the problem.
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
- `_clip_spans_to_laminar`, the stack sweep.
- `finalize_perfetto_packets`, the single emission point at encoder close.
- `_emit_root_descriptor`, guarded by `has_root_descriptor` so it fires once.
- `get_process_track_ranks()`, sorting by `(start_ts, pid)`.
- Tests: `tests/exporters/test_perfetto_format.py`,
  `TestProcessLifetimeLaminarClipping` (crossing, containment, disjoint, touching, equal
  starts, a span crossed by two later spans, zero-length drops, and a randomized property
  test asserting the output is always laminar); `TestProcessLifetimeState` for the span
  accumulator; `TestConvertItemToPerfettoPackets::test_no_closeout_emitted_during_convert`.
  `tests/exporters/test_perfetto_exporter_integration.py`, `TestCrossingProcessSpans`
  (asserts `misplaced_end_event == 0` against a deliberately crossing trace) and
  `TestProcessesTrack::test_slice_per_pid`, which asserts per-pid timestamps rather than
  just the row count;
  `TestMultiFlushProcessesTrack::test_slice_end_is_last_event_ts` forces many flushes with
  `flush_threshold=5` and asserts `slice.ts + slice.dur == last_event_ts`.
