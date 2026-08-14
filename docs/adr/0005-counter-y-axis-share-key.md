# ADR-0005: Use the metric name itself as `CounterDescriptor.y_axis_share_key`

- **Status:** Accepted
- **Date:** 2026-06-28

## Context

`G0 collected`, `G1 collected` and `G2 collected` are three separate Perfetto counter
tracks plotting the same quantity for different generations. By default each gets its own
auto-scaled Y-axis, so a spike on G0 and a spike on G1 look the same size even when they
differ by two orders of magnitude. Comparing generations means mentally re-scaling.

Perfetto solves this with `CounterDescriptor.y_axis_share_key` (field 7, optional string):
counter tracks that share a key **and** share a parent track are rendered on one Y-axis
range. gcmon was already emitting a `CounterDescriptor` at `TrackDescriptor` field 8 for
every counter track, but it was always the empty submessage.

All per-generation counter tracks already share a parent, the per-`(pid, iid)`
`GC Metrics` group from [ADR-0003](0003-gc-metrics-group-track.md), so the "same parent"
half of the requirement holds and sharing is scoped to a single process.

## Decision

**The share key is the metric name, verbatim.** `G0 collected`, `G1 collected` and
`G2 collected` all get `y_axis_share_key = "collected"`; the per-generation `candidates`,
`duration` and `uncollectable` tracks get their own metric names.

There is **no lookup table**, on purpose. The grouped-counter emission path sets
`y_axis_share_key` to the metric name it already has, so any metric added to the counter
payload in future gets correct Y-axis sharing with no code change. This is the whole point
of keying on the name.

Two normalizations guard the edges:

- An empty share key is treated as an absent one: no field is emitted, and the
  `CounterDescriptor` stays the empty submessage. This defends against silently disabling
  sharing for a future metric with an empty name.
- A track that is not a counter ignores any share key passed to it; field 8 is not emitted
  at all.

When a share key is set, the `CounterDescriptor` submessage contains **only** field 7. No
other `CounterDescriptor` field (`type`, `categories`, `unit`, `unit_multiplier`,
`is_incremental`, `unit_name`) is written.

**`heap_size` and `rss` get no share key.** They are the top-level counters from
[ADR-0004](0004-toplevel-shared-counters.md), parented to the process track, with no peers
to share an axis with. A key there would be a no-op, and omitting it keeps the wire format
minimal.

## Consequences

- Generation-to-generation magnitude comparison is readable without re-scaling.
- Y-axis sharing and sibling ordering are independent features; both are preserved.
- Sharing cannot cross processes, because each pid has its own `GC Metrics` group and
  Perfetto requires a shared parent. That is the documented scope of the feature.
- Older trace processors ignore the unknown field, so no write-time version gate is
  needed.
- **The SQL-level tests are permanently `@pytest.mark.xfail(strict=False)`.** The trace
  processor does not expose `y_axis_share_key` as a `counter_track` column (established
  against Perfetto 0.56.0, which is what the tests' `reason` strings still cite).
  `strict=False` means they flip to passing, rather than to XPASS-and-fail, when a future
  Perfetto version surfaces it, with no test edit. The wire-level tests are the source of
  truth.
- Per [ADR-0001](0001-hand-rolled-perfetto-protobuf-encoder.md), the `CounterDescriptor`
  submessage is hand-encoded against a local field-number enum. Do not reach for the
  generated class from the `perfetto` package.

## Alternatives considered

- **A lookup table mapping metric to share key.** Rejected: it duplicates the metric name
  and needs an edit whenever someone adds a metric. Forget the edit and that metric
  silently loses its shared axis.
- **A share key on `heap_size` / `rss` for forward-compatibility.** Rejected: no peers, so
  it is bytes on the wire that do nothing.
- **Setting `unit` / `unit_name` at the same time.** Deferred to a separate change; a
  wire-level test locks the current minimal submessage, so the scope creep would be
  caught.

## Implementation

- `src/gcmon/exporters/perfetto_proto.py` carries `y_axis_share_key` as field 7 of
  `CounterDescriptor`.
- `src/gcmon/exporters/perfetto_builders.py` decides what reaches the wire: a populated
  submessage when the key is truthy, an empty one otherwise, and nothing at all for a
  track that is not a counter.
- `src/gcmon/exporters/perfetto_format.py` passes the metric name as the share key on the
  grouped branch and omits it on the top-level branch.
- Tests: `tests/exporters/test_perfetto_builders.py` covers field 8 at the wire level
  (empty-submessage fallback, non-counter ignore, empty-string normalization,
  only-field-7 guard); `tests/exporters/test_perfetto_counter_tracks.py` checks the same
  values as reached through a convert pass;
  `tests/exporters/test_perfetto_exporter_integration.py` holds the `xfail`'d SQL pair.
