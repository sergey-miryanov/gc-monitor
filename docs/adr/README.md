# Architecture Decision Records

An ADR records a decision that shaped gcmon's design, along with the reasoning and the
evidence behind it: the trade-offs accepted, the alternatives rejected, and the
constraints the decision puts on future work. Read one when you want to know why a piece
of the design looks the way it does.

An ADR does not document *what* the code does. The code and its tests are the authority on
that. Each ADR anchors into the current source so you can check the two against each other.

Forward-looking work that has been specified but not yet built lives in
[`specs/`](../../specs/README.md), not here — one file per open item, deleted when it lands.
A spec that settles a durable design question graduates into a record below.

## Conventions

- **Filename:** `NNNN-kebab-case-title.md`. Assign numbers in order and **never
  reuse or renumber them**, so a reference to ADR-0007 keeps meaning the same record.
- **Status:** `Accepted`, or `Superseded by ADR-NNNN`. Leave a superseded record in place;
  the history is worth keeping. Do not delete or rewrite one. Write a new record that
  supersedes it and link both ways.
- **Date:** when the change shipped, not when you wrote the file.
- **New records:** copy [`0000-template.md`](0000-template.md).

Amend an existing ADR when the reasoning is refined or the code moves. Write a new one when
the decision itself changes.

## Index

| # | Decision |
|---|---|
| [0001](0001-hand-rolled-perfetto-protobuf-encoder.md) | Hand-roll the Perfetto protobuf encoder; keep `perfetto` out of the runtime dependency tree |
| [0002](0002-perfetto-track-uuid-and-hierarchy.md) | Allocate track UUIDs sequentially and parent every track explicitly |
| [0003](0003-gc-metrics-group-track.md) | Parent per-generation counters to a non-OS-scoped `GC Metrics` group track |
| [0004](0004-toplevel-shared-counters.md) | Emit `heap_size` and `rss` as single top-level counters, outside the `GC Metrics` group |
| [0005](0005-counter-y-axis-share-key.md) | Use the metric name itself as `CounterDescriptor.y_axis_share_key` |
| [0006](0006-begin-end-slice-pairs.md) | Represent durations as Begin/End pairs in both backends |
| [0007](0007-shared-trace-converter-pipeline.md) | Convert GC stats to `TraceEvent` once, in a shared pipeline |
| [0008](0008-buffered-exporter-and-encoder-protocol.md) | Split exporters into a buffering base class and a pluggable `EventEncoder` |
| [0009](0009-nanoseconds-canonical-time-unit.md) | Store `TraceEvent.ts` in nanoseconds; convert at the encoder |
| [0010](0010-process-identity-cmdline-and-start-marker.md) | Carry process cmdline in two places, and force the process track to render |
| [0011](0011-process-lifetime-and-ordering.md) | Show process lifetimes on one shared track, ordered by first event |
| [0012](0012-trace-output-formats.md) | Support Perfetto output in `combine`, and dual output only in live mode |
| [0013](0013-rss-sampling.md) | Sample RSS in a standalone `RssSampler`, on a `tid = -1` sentinel track |
| [0014](0014-perfetto-integration-test-strategy.md) | Validate traces against the real trace processor; deselect slow suites by marker |
| [0015](0015-gc-loss-spans-on-their-own-track.md) | Draw reconstructed GC loss on a per-interpreter track, one span per poll interval |

## Reading order

ADRs 0001–0005 are about the Perfetto wire format and track layout, and build on each other
in that order. 0006–0009 cover the internal event model shared by all backends. 0010–0013
and 0015 are individual features. 0014 explains how any of it is verified.

---

*These records were extracted from a set of implementation specs that lived in
a git-ignored working directory. Those specs were forward-looking plans with step-by-step
instructions. The part worth keeping, meaning the decisions and their rationale, is here
under version control, and the original specs were removed once extracted. What remained of
that folder was re-verified and rewritten on 2026-08-05, and `specs/` is now tracked — see
[its README](../../specs/README.md#provenance).*
