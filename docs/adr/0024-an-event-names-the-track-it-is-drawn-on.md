# ADR-0024: An event names the track it is drawn on

- **Status:** Accepted
- **Date:** 2026-08-26
- **Supersedes:** [ADR-0004](0004-toplevel-shared-counters.md)

## Context

`TraceEvent` came from the Chrome Trace Event format, and kept its vocabulary
after [ADR-0021](0021-write-one-trace-format.md) removed the format that
needed it. A `ph` discriminator carried `B`, `E`, `C`, `I` and `M`; an instant
carried `s: "p"`; and a track was a `(pid, tid)` pair in which a row belonging
to no interpreter took a tid no interpreter would claim: `-1` for RSS
([ADR-0013](0013-rss-sampling.md)), `-2 - iid` for a loss row
([ADR-0015](0015-gc-loss-spans-on-their-own-track.md)).

The encoder is the only consumer left.
[ADR-0021](0021-write-one-trace-format.md) predicted this in its consequences:
with one format, the intermediate's job narrows from "the thing two encoders
agree on" to "the thing the buffer holds and the encoder reads".

Three things followed from the Chrome shape:

- `ProcessMeta` and `ThreadMeta` existed so a producer could tell the encoder
  which rows to draw. Two producers implemented that, and the names they
  carried were never read: the encoder builds `f"Process {pid}"` and
  `f"Thread {iid}"` itself.
- A counter event carried a dict of metrics and the encoder concatenated a
  display name from `f"{event_name} {metric}"`, with a special case so a
  single-arg `heap_size` event would not be called `heap_size heap_size`.
- Loss and RSS were identified by sentinel integers, so the encoder had to ask
  `tid > LOSS_TID_BASE` to find out what kind of row it was writing.

## Decision

**A `Track` names a row, and every event carries one.** `ProcessTrack(pid)`,
`ThreadTrack(pid, iid)` and `LossTrack(pid, iid)`, frozen and hashable. The
`(pid, tid)` pair and the sentinels go.

Three members, not two, because a track names *a row* rather than *what an
event is about*. `LossTrack` and `ThreadTrack` are the same pid and the same
interpreter and different rows; naming what an event is about would need a
second discriminator to say which of the two to draw on, which is the sentinel
back in another spelling.

**The encoder derives every other row from those.** A track's descriptor goes
out because an event named that track, ahead of the packet that named it: the
pid's process descriptor whichever kind of track it is, then the track's own.
A counter's track, the `GC Metrics` group holding it, and the `Processes`
track are derived too, so nothing gcmon writes names them. `ProcessMeta`,
`ThreadMeta` and both implementations that built them are gone.

This closes [ADR-0008](0008-buffered-exporter-and-encoder-protocol.md)'s meta
dedup race **by deletion rather than by relocation.** That race was two
producers racing on a check-and-add under `BufferedTraceExporter._lock`. With
no producers, dedup lives only in `PerfettoTrackState`, reached through
`write_events` and `record_process_liveness`, both already under `_io_lock`.

**A counter carries one metric, its value and a written display name.** The
converter writes `G0 collected` and `Thread 0 heap_size` itself; the encoder's
`f"{name} {metric}"` concatenation and its single-arg special case go with the
loop they guarded. `metric` still drives the sibling rank and the shared y
axis ([ADR-0005](0005-counter-y-axis-share-key.md)), so `G0 collected` and
`G1 collected` keep one scale.

**`heap_size` is qualified by its interpreter, unconditionally.**
`Thread 0 heap_size`, interpreter 0 included. gcmon writes a counter
descriptor the first time it sees that metric, batch by batch; when
interpreter 0's goes out, interpreter 1 may not have produced a record yet, so
no rule of the form "qualify only when there is a sibling" is implementable in
a streaming writer.

**What survives ADR-0004:** one `heap_size` series per `(pid, iid)` and one
`rss` series per pid; `heap_size` parented to the process track rather than
inside the collapsible `GC Metrics` group, with the accepted trade-off that
the trace processor drops its `sibling_order_rank` there; and `heap_size`
staying on the `GC Pause(N)` slice args, so it remains queryable per-pause.

**`rss` leaves the top-level metric set.** A counter a `ProcessTrack` owns
parents to the process track by construction, so for `rss` that placement
stops being a policy and becomes its identity. `heap_size` stays in the set: a
`ThreadTrack` owns it and it is deliberately drawn a level up.

## Consequences

- A producer cannot forget to describe a track, and cannot describe one twice.
  Both were possible and one of them had a race.
- A trace an operator opens is unchanged, except that a `heap_size` counter
  track is named `Thread {iid} heap_size` where it was `heap_size`. A
  PerfettoSQL query matching `name = 'heap_size'` stops matching. Two
  interpreters in one process previously drew two sibling rows under one name.
- A JSONL capture carries no `tid`. It was `iid` again on a GC record and
  `-2 - iid` on a loss one, and nothing read it; `from_mapping` rebuilds a
  record from its own fields, so a capture written before this still reads.
- On the `combine` path a pid's thread descriptors arrive at each track's
  first slice rather than up front. Descriptor order in a combined trace
  changes; no reader depends on it.
- `ProtobufEventEncoder` memoizes which pids it has asked for a command line.
  It used to fire once per pid because there was one `ProcessMeta` per pid;
  keyed on events instead, a pid whose command line cannot be read would cost
  a failed read and a warning on every flush.
- `Instant` can carry args, the way a slice can. Nothing fills the field yet.
- Adding a kind of row means adding a member to `Track` and a branch to the
  descriptor derivation. Adding a kind of *event* means adding a member to
  `TraceEvent`; the type checker finds every place that has to change.

## Alternatives considered

- **Delete the intermediate and emit Perfetto packets from the converter.**
  The tempting reading of "one format, one consumer". Rejected on three
  counts. It costs the oracle in `tests/test_convert_cmd_perfetto.py`, which
  compares the trace against the `list[TraceEvent]` it was built from and
  needs both halves to exist. It costs the encoder's unit-test seam, which
  drives `TraceEvent` in and reads packets out. And it puts track state under
  the exporter's IO lock on every record rather than once per flush, since a
  converter emitting packets has to allocate uuids as it goes.
  [ADR-0008](0008-buffered-exporter-and-encoder-protocol.md)'s
  exporter/encoder split and the buffering boundary are what keep the
  intermediate earning its place. The fact that would settle it differently: a
  second encoder never arriving *and* the oracle being retired.
- **Name what an event is about rather than the row it is drawn on.** A
  `LossTrack` would collapse into `ThreadTrack` plus a flag, and the flag is
  the sentinel again. Rejected: a track that names a row is the thing the
  encoder needs, and every derived row falls out of it.
- **Qualify `heap_size` only when a process has more than one interpreter.**
  Rejected as unimplementable rather than undesirable: gcmon is a streaming
  writer and does not know at descriptor time whether a sibling will appear.

## Implementation

- `src/gcmon/model/trace_event.py` holds the three track structs, the `Track`
  and `TraceEvent` unions, and `SliceBegin` / `SliceEnd` / `Instant` /
  `Counter`. No functions.
- `src/gcmon/exporters/perfetto_format.py` holds `_emit_track_descriptors`,
  which derives a track's descriptors, and the top-level metric set.
- `src/gcmon/exporters/perfetto_track_state.py` keys its uuid tables on a
  `Track`.
- `src/gcmon/exporters/trace_converter.py` writes every display name.
- Tests: `TestATrackIsDescribedOffTheEventsOnIt` in
  `tests/exporters/test_perfetto_format.py`; `TestTwoInterpretersHeapSizes` in
  `tests/exporters/test_perfetto_exporter_integration.py`, resolved through
  the trace processor; `TestMetaDedupRaceClosed` in
  `tests/exporters/test_exporter_thread_safety.py` for the race that closed by
  deletion.
