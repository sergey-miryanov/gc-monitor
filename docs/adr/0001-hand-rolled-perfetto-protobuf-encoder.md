# ADR-0001: Hand-roll the Perfetto protobuf encoder; keep `perfetto` out of the runtime dependency tree

- **Status:** Accepted
- **Date:** 2026-06-08 (`perfetto_format.py` split into five modules, and its tests to match, 2026-08-01)

## Context

gcmon writes Perfetto binary traces (`.pftrace`). The obvious implementation imports the
generated message classes from the official `perfetto` Python package and lets it
serialize. That package pulls in `protobuf`, and gcmon is a monitoring tool meant to be
installable next to the process it watches, so every runtime dependency is one the target
application inherits.

The slice of the Perfetto wire format gcmon needs is small: varints, length-delimited
submessages, and roughly thirty field numbers, a few hundred lines to write by hand.

The cost is owning those field numbers against a proto that changes upstream. Three
field-number bugs have shipped: `TrackEvent.type` and `track_uuid` renumbered upstream,
timestamps written inside `TrackEvent` where field 1 is a `timestamp_delta_us` oneof
member, and `DebugAnnotation.name` moving from field 1 to field 10 (field 1 is now a
`uint64` interned ID). A fourth was arithmetic: `encode_varint` masked to 32 bits, so
`sibling_order_rank = -1` was written as `0`.

All four fail the same way. The trace still parses; it renders wrong, and only a human
opening the UI notices. That failure mode, rather than any individual bug, is what the
decision has to address.

## Decision

The production encoder is hand-rolled, and the `perfetto` package is a **dev-only**
dependency used exclusively on the read side in tests
(see [ADR-0014](0014-perfetto-integration-test-strategy.md)).

It is layered across six modules in `src/gcmon/exporters/`, each importing only from ones
above it:

| Module | Holds |
|--------|-------|
| `protobuf_encoder.py` | varint and length-delimited wire primitives |
| `perfetto_proto.py` | field numbers and enum values, nothing else |
| `perfetto_track_state.py` | uuid allocation and per-trace bookkeeping |
| `perfetto_builders.py` | message builders, pure values in and bytes out |
| `perfetto_process_lifetime.py` | the shared `Processes` track ([ADR-0011](0011-process-lifetime-and-ordering.md)) |
| `perfetto_format.py` | track layout policy, the conversion pass, and the re-exports importers use |

**The direction must stay acyclic.** It is what keeps the wire-format layer small enough to
audit against upstream Perfetto, which is the reason for hand-rolling at all. Refuse an
import that points the other way, such as `perfetto_builders` reaching for a layout
constant.

`tests/exporters/` carries a `test_` module per row, importing from the module that owns
each symbol rather than through the `perfetto_format` re-exports, so a test failure names
the layer. Two subjects that `perfetto_format.py` implements are large enough to get their
own files anyway: `test_perfetto_ordering.py` and `test_perfetto_counter_tracks.py`.
Helpers shared by more than one of them live in `tests/exporters/perfetto_helpers.py`.

Three rules make this safe:

- **Every protobuf field number is a named `IntEnum` member**, one enum class per proto
  message: `TraceField`, `TracePacketField`, `TrackDescriptorField`, `ProcessDescriptorField`,
  `ThreadDescriptorField`, `CounterDescriptorField`, `TrackEventField`, `DebugAnnotationField`,
  plus the value enums `ChildTracksOrdering`, `ProcessOrdering`, `ThreadOrdering`,
  `TrackEventType`. `IntEnum` members are `int` subclasses, so they pass anywhere an `int`
  is expected at zero runtime cost. All are exported via `__all__`. A future upstream
  renumbering is one edit per field, in one file.
- **Timestamps live on `TracePacket.timestamp` (field 8), never inside `TrackEvent`.**
- **Every `TracePacket` carries `trusted_packet_sequence_id` (field 10, uint32).**
  Perfetto drops packets without it, since it needs the sequence for incremental state
  tracking. The value is generated as `id(self) & 0x7FFFFFFF`, which is unique per
  encoder instance and needs no external source of entropy.

**Future maintainers must not import message classes from the `perfetto` package into the
encoder.** A line such as `from perfetto.protos.perfetto.trace.perfetto_trace_pb2 import
CounterDescriptor` would work and would be tempting, and it would put `protobuf` back in
the runtime tree. Sub-messages are built field-by-field using the local encoder helpers.

## Consequences

- Installing gcmon pulls in no protobuf machinery. The only optional runtime dependency
  is `psutil`, and that degrades gracefully.
- Field-number drift in upstream Perfetto is a real, recurring risk, and it fails
  silently. So the regression tests assert the **raw wire format**, meaning field number
  and wire type, instead of round-tripping through gcmon's own enums. A round-trip test
  reads back through the same constant it wrote with, so it is equally happy with a
  correct and an incorrect value; it would not have caught any of the field-number bugs
  above.
  The end-to-end guard is ADR-0014's trace-processor tests.
- The `DebugAnnotationField.NAME = 10` constant carries an inline comment explaining the
  oneof constraint and warning against "fixing" it back to 1. Keep that comment.
- Byte-level parity with the official package was verified once: for a full 1450-event GC
  trace, both encoders produced identical output (162,793 bytes, zero differences), and
  the trace processor reported matching rows across the `track`, `process`, `thread`,
  `slice`, and `counter` tables.
- **Prove a no-behaviour-change refactor by comparing emitted bytes, not by a green
  suite.** Some tests assert packets are present without asserting order, and order is
  load-bearing on the `Processes` track. Pass an explicit `sequence_id`, or the default
  `id(self) & 0x7FFFFFFF` changes every run and swamps the comparison. This is how the
  five-module split was verified.

## Alternatives considered

- **Use the `perfetto` package as a runtime dependency.** Rejected: it makes gcmon a heavy
  install for the process being monitored, for a serialization job that is a few hundred
  lines of well-specified wire format.
- **Interned annotation names (`name_iid` + a name table).** Rejected as premature: it is
  an optimization for high-frequency annotation writers, and gcmon emits one `BeginEvent`
  per GC pause. The bandwidth saving is negligible and it would add state to the encoder.
- **A back-compat shim writing `DebugAnnotation.name` at both field 1 and field 10.**
  Rejected: field 1 is now a `uint64` IID. A string written there is either dropped or
  read as a garbage IID that could collide with a real interned name.

## Implementation

- `src/gcmon/exporters/protobuf_encoder.py:21`, `encode_varint`, with 64-bit sign
  extension for negative values; `:44`, `encode_varint_field`.
- `src/gcmon/exporters/perfetto_proto.py`, every field enum: `TrackDescriptorField`,
  `CounterDescriptorField.Y_AXIS_SHARE_KEY`, `ProcessDescriptorField`, and
  `DebugAnnotationField.NAME = 10` with its warning comment.
- `src/gcmon/exporters/perfetto_builders.py`, `build_track_descriptor`, which builds each
  sub-message field-by-field.
- Wire-level regression tests: `tests/exporters/test_perfetto_builders.py` (assertions on
  raw field numbers and wire types, not round-trips), and
  `tests/exporters/test_perfetto_proto.py`, which reads the numbers back out of the
  `perfetto` package's generated descriptors rather than trusting our own.
