# ADR-0011: Show process lifetimes on one shared track, ordered by first event

- **Status:** Accepted
- **Date:** 2026-06-27 (ordering added 2026-06-28)

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

**The slice END is emitted exactly once per pid, at encoder close**, via
`finalize_perfetto_packets`, called once from `ProtobufEventEncoder.close()`, and *not* at
the end of each convert call. `BufferedTraceExporter` flushes in chunks of
`flush_threshold` (default 1000), so a long trace makes many convert calls; per-call
closeout would put one BEGIN and N ENDs on the wire per pid. Perfetto pairs a BEGIN with
the **first** matching END and orphans the rest, collapsing the slice to the end of the
first batch. So end-timestamp state accumulates across convert calls and drains once.

**Counter events are excluded from the end timestamp.** The encoder emits counter packets
at `ts_start_ns` of each GC pause, alongside the pause's BEGIN, so including them would
drag the end timestamp back to the *start* of the last pause and report a near-zero
lifetime. The span is `[first non-meta event, last Begin/End/Instant event]`.

END packets are emitted in ascending end-timestamp order, ties by ascending pid.

## Consequences

- You can see each monitored process's lifetime at a glance and compare across processes.
- Traces are reproducible: the same events in a different input order produce the same
  ranks.
- **`sibling_order_rank` is not exposed as a SQL column.** It is a UI rendering hint, so the
  trace-processor tests act as a *schema-validity guard*: they confirm the trace
  processor accepts the new layout and that the `process` and `track` tables survive
  intact. They cannot assert display order. Only the Perfetto UI can.
- Perfetto's docs call these orderings "strong hints"; the UI may still rearrange tracks in
  special contexts.
- **Ranks are not applied retroactively.** If a pid's `ProcessMeta` lands in an
  earlier batch than its first non-meta event, the descriptor goes out before the rank is
  known, and descriptor emission is idempotent, so that pid gets no rank. The wire format
  stays correct: a rank is present only when it was known at emission time.
- A pid seen only through `ProcessMeta` / `ThreadMeta` gets no lifetime slice and no rank.
- `thread_ordering = EXPLICIT` comes along as a free side benefit. Thread tracks do not
  set `sibling_order_rank` today, so thread order is unchanged, and the hint is in place if
  that changes.
- Consumers enumerating slices must filter `track.name == 'Processes'`, as the equivalence
  test does, since these slices are Perfetto-only.

## Alternatives considered

- **One lifetime track per pid.** Rejected: it multiplies the track count and gives up the
  single visual row that makes cross-process comparison work.
- **`parent_uuid = 0` to mean "root".** Rejected as incorrect: `uuid = 0` is the special
  root *descriptor* carrying ordering hints, not a parent, and pointing at it has no
  defined meaning.
- **OS-level process times via `psutil.Process(pid).create_time()`.** Rejected: the span
  should describe what gcmon observed, not when the OS started the process. Those differ,
  and the difference would be misread as monitoring coverage.
- **Emitting the slice END at the end of each convert call.** This was the original
  implementation and it is wrong; see above.
- **Re-emitting a process descriptor with a corrected rank in a later batch.** Rejected:
  it breaks idempotent emission for a cosmetic gain in a rare ordering.

## Implementation

- `src/gcmon/exporters/perfetto_format.py:191`, `_PROCESS_LIFETIME_TRACK_NAME = "Processes"`.
- `:52-53`, `PROCESS_ORDERING = 19`, `THREAD_ORDERING = 20`. Fields 6 and 7 on the same
  message are `chrome_process` and `chrome_thread`, so a wrong number here writes a
  different message and fails silently ([ADR-0001](0001-hand-rolled-perfetto-protobuf-encoder.md)).
- `:531-562`, `_emit_root_descriptor`, guarded by `has_root_descriptor` so it fires once.
- `:303-315`, `get_process_track_ranks()`, sorting by `(first_ts, pid)`.
- `:1027`, `finalize_perfetto_packets`, the single drain point at encoder close.
- Tests: `tests/exporters/test_perfetto_format.py:1863-2070` (rank by first ts, ties by pid,
  meta-only pids, input-order independence, single root descriptor);
  `TestConvertItemToPerfettoPackets::test_no_closeout_emitted_during_convert`;
  `TestMultiFlushProcessesTrack::test_slice_end_is_last_event_ts` in
  `tests/exporters/test_perfetto_exporter_integration.py`, which forces many flushes with
  `flush_threshold=5` and asserts `slice.ts + slice.dur == last_event_ts`.
