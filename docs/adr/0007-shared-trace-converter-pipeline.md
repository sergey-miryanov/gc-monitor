# ADR-0007: Convert GC stats to `TraceEvent` once, in a shared pipeline

- **Status:** Accepted
- **Date:** 2026-06-14 (`LossMsg` noted as the third record type 2026-08-05)

## Context

`chrome_trace_format.py` and `perfetto_format.py` each independently turned a
`TGCStatsInfo` into output. Both re-implemented the same GC sub-phase
discovery (the `has_*` guards for mark-alive, fill-increment,
deduce-unreachable, handle-weakrefs, finalize-garbage, handle-resurrected,
clear-weakrefs, delete-garbage), the same name and category strings for each
sub-phase, and the same counter-metric collection. Only the `has_*` TypeGuards
in `protocol.py` had been factored out.

Adding a sub-phase meant editing both files identically. Getting one of them
wrong produced two traces of the same run that disagreed, which is a slow,
confusing bug to find.

Once [ADR-0006](0006-begin-end-slice-pairs.md) made both backends agree that a
span is a begin/end pair, nothing structural stood in the way of sharing the
conversion.

## Decision

A single pipeline `TGCStatsInfo → list[TraceEvent]` lives in
`src/gcmon/exporters/trace_converter.py`. It owns the only copy of the
sub-phase logic and the naming strings.

`TraceEvent`, the union of the begin, end, instant and counter events plus
`ProcessMeta` and `ThreadMeta` in `src/gcmon/model/trace_event.py`, is the
contract between the converter and the backends. Each backend consumes that
list and does nothing but encode: Chrome to JSON, Perfetto to protobuf.
Neither inspects `TGCStatsInfo` fields any more. Track UUID management stays
where it was, and cmdline handling is untouched.

The refactor also settled two behaviours:

**Descriptors carry no timestamp.** The process `TrackDescriptor` is emitted
without a timestamp on its containing `TracePacket`. The previous code set it
only on the valid-pause path, which was inconsistent with the thread and
counter descriptors, neither of which ever carried one. Descriptors are now
time-independent across the board. Consumers must not rely on a descriptor
timestamp.

**Invalid-timestamp filtering moved to the producer.** The
`ts_start < ts_stop` guard now lives in the monitor's poll, not in the
Perfetto converter. This is an intentional behaviour change for the Chrome
backend, which previously emitted zero-duration events for such records and
now drops them the way Perfetto always did. Filtering at the producer is
exporter-agnostic and keeps the shared converter pure: filter once, emit
everywhere.

**Amended 2026-08-26 by
[ADR-0024](0024-an-event-names-the-track-it-is-drawn-on.md).** There were meta
events here, and `ProcessMeta` preceding `ThreadMeta` for a pid was called
part of the public contract of the event stream. It is not a contract a
producer keeps any more: an event names the `Track` it is drawn on, the
encoder derives the descriptors from that, and the ordering follows by
construction. Everything else here stands.

## Consequences

- A new sub-phase or metric is added in one place, and both output formats get
  it.
- Every output format carries the same events by construction. While there
  were two, that equivalence was asserted by comparing one against the other;
  with one format left, the trace is asserted against the `list[TraceEvent]`
  it was built from ([ADR-0021](0021-write-one-trace-format.md)).
- `chrome_trace_format.py` became a thin re-export module so existing
  importers kept working. It went with the format.
- Records with `ts_start >= ts_stop` no longer reach any exporter, and the
  monitor's poll is the one place that drops them.
- Adding an output format means writing an encoder, not a converter.
  [ADR-0008](0008-buffered-exporter-and-encoder-protocol.md) builds on that.
- `LossMsg` is emitted from the same poll, so one converter branch carries it
  to every exporter. See [ADR-0015](0015-gc-loss-spans-on-their-own-track.md).

## Alternatives considered

- **Share only the `has_*` guards, keep two converters.** That was the status
  quo, and it was insufficient: the guards were the small part. The naming
  strings, the categories and the metric collection were where the two copies
  drifted.
- **Make Perfetto consume the Chrome JSON structures.** Rejected: it would
  make the Chrome format the internal model, so a Perfetto-only concept
  (nested slice hierarchy, counter descriptors) would have no place to live,
  and every Perfetto feature would need a Chrome representation first.
- **Keep the invalid-timestamp filter in the exporters.** Rejected: two copies
  of a filter is how the exporters diverged in the first place.

## Implementation

- `src/gcmon/exporters/trace_converter.py` converts one record, and a whole
  batch, to `TraceEvent`s.
- `src/gcmon/model/trace_event.py` holds the `TraceEvent` union and its
  factories.
- `src/gcmon/exporters/perfetto_format.py` encodes those events, emitting a
  counter track's descriptor and its UUID together so the call site does not
  look the UUID up twice.
- `src/gcmon/monitoring/monitor.py` keeps the per-`(pid, iid, gen)`
  `collections` cursor and applies the `ts_start < ts_stop` validity guard
  before anything reaches the converter.
- Tests: `tests/monitoring/test_monitor.py` covers the records the poll drops,
  and `tests/exporters/test_perfetto_format.py` the events that survive to
  conversion.
