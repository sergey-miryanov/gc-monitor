# ADR-0004: Emit `heap_size` and `rss` as single top-level counters, outside the `GC Metrics` group

- **Status:** Accepted
- **Date:** 2026-06-27 (`rss` added 2026-07-13)

## Context

`heap_size` was originally carried on the per-generation counter payload, alongside
`collected`, `candidates` and `duration`. Because the encoder materializes one counter
track per `(track name, metric)` pair, that produced three tracks (`G0 heap_size`,
`G1 heap_size`, `G2 heap_size`) all plotting the same process-wide number, sampled at
whichever generation happened to collect. Reading heap size over time meant mentally
merging three partial series.

`heap_size` has no per-generation meaning. Neither does RSS
([ADR-0013](0013-rss-sampling.md)), which arrived later with the same shape: a
process-level value with no generation and no thread affinity.

Naming is a second constraint on any fix. The encoder names a counter track
`f"{event_name} {metric}"`, so a counter event named `heap_size` carrying a single arg keyed
`heap_size` would produce the track name `heap_size heap_size`.

## Decision

**`heap_size` is emitted as its own counter event**, separate from the per-generation one:
`counter_event(pid, tid, "heap_size", ts_start_ns, {"heap_size": ...})`. It is removed from
the per-generation `counter_data` payload, which now carries only `collected`, `candidates`,
`duration`, and `uncollectable` (the last only when non-zero). All generations on a
`(pid, tid)` feed the same single `heap_size` track; latest value wins on the time axis,
which is the correct semantics for a process-wide gauge.

**Single-argument counter events use the metric as the display name.** When
`len(event.args) == 1`, the Perfetto track name is the metric alone (`heap_size`, not
`heap_size heap_size`); the Chrome encoder blanks the event name for the same reason,
since Chrome's trace processor derives the track name as `f"{event_name} {arg_key}"`.

**`_TOPLEVEL_COUNTER_METRICS = frozenset({"heap_size", "rss"})` is the single switch.**
Metrics in this set are parented directly to the process track, outside the collapsible
`GC Metrics` group. Adding a metric to the set moves it out of the group.

`heap_size` stays on the `GC Pause(N)` slice's args as well, so it remains queryable
per-pause from the slice `args` table.

## Consequences

- One continuous `heap_size` series per `(pid, tid)`, and one `rss` series per pid.
- **Accepted trade-off:** because these tracks are parented to the OS-scoped process
  track, the trace processor drops their `sibling_order_rank` (the rule from
  [ADR-0003](0003-gc-metrics-group-track.md)). Their position is a UI heuristic; in the
  Perfetto UI they render *below* the `GC Metrics` group. `_COUNTER_RANKS["heap_size"]`
  is retained for documentation and forward-compatibility but is not honored.
- **Chrome-consumer break:** `convert_item_to_trace_format` now emits two `C` events per
  item rather than one. Downstream Chrome-trace tooling that assumed a single counter event
  per GC pause, and read every metric from it, must read the consolidated event separately.
  No consumer in this repository made that assumption.
- New process-level metrics need only be added to `_TOPLEVEL_COUNTER_METRICS`; the naming
  and parenting fall out.

## Alternatives considered

- **`heap_size` inside the `GC Metrics` group with `sibling_order_rank = 0`.** Tried in the
  Perfetto UI. The rank *is* honored there and `heap_size` renders first inside the group,
  but the group is collapsible, so the heap size stays hidden until the user expands it.
  Consolidating the metric was meant to make it easy to read. Rejected in favour of a
  standalone always-visible track; the cost is the dropped rank described above.
- **Cross-thread or cross-process consolidation** (one `heap_size` track per pid rather
  than per `(pid, tid)`). Out of scope; per-`(pid, tid)` matches how the interpreter
  reports the value.
- **`tid = 0` for the process-level RSS counter.** Rejected; see
  [ADR-0013](0013-rss-sampling.md).
- **Smoothing or aggregating samples.** Rejected; "latest value wins" is standard Perfetto
  counter semantics and keeps the exporter stateless.

## Implementation

- `src/gcmon/exporters/trace_converter.py:50-56`, per-generation `counter_data`, without
  `heap_size`.
- `:281-289`, the separate consolidated `heap_size` counter event.
- `src/gcmon/exporters/perfetto_format.py`, `_TOPLEVEL_COUNTER_METRICS`.
- `:775-787`, the top-level branch, parenting directly to the process track.
- `src/gcmon/exporters/encoder.py`, where `JsonEventEncoder` blanks the name of single-arg
  counter events so Chrome does not derive a doubled track name.
- Tests: `tests/exporters/test_chrome_trace_format.py:359-384`
  (`test_heap_size_counter_event_is_shared_across_generations`, asserting
  `"heap_size" not in` the per-generation args);
  `tests/exporters/test_perfetto_exporter_integration.py` asserts exactly one `heap_size`
  track and zero `G{N} heap_size` tracks.
