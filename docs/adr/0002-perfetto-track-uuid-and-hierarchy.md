# ADR-0002: Allocate track UUIDs sequentially and parent every track explicitly

- **Status:** Accepted
- **Date:** 2026-06-08 (UUID allocator revised 2026-06-18)

## Context

Perfetto identifies every track by a 64-bit `uuid`, and `TrackDescriptor.parent_uuid`
builds the tree the UI renders. gcmon emits four kinds of track per monitored process:
the process track, one thread track per interpreter, counter tracks, and the shared
`Processes` lifetime track (see [ADR-0011](0011-process-lifetime-and-ordering.md)).

An early design derived UUIDs arithmetically from the identifiers (process as
`pid | (1 << 60)`, thread as `(pid << 20) | iid | (1 << 60)`, counters from a `3 << 60`
base), on the theory that bit 60 marked "default" (OS-scoped) tracks and that deterministic
UUIDs were collision-free by construction. The scheme was fragile: the bit-packing had to
be re-derived for every new track kind, `(pid << 20) | iid` collides for large pids, and it
put an enormous varint in every packet.

The descriptor layout was wrong in four further places, all presenting the same way, with
the Perfetto UI rendering gcmon traces in the wrong order while the Chrome trace import
looked right: `ProcessDescriptor` written at field 6 (`ChromeProcessDescriptor`) instead of
field 3, thread UUIDs setting bit 61 instead of bit 60, process and thread descriptors
carrying no ordering fields, and counter tracks parented to the *thread* track, which
pushed the thread down below its own counters.

## Decision

**UUIDs are allocated sequentially from a per-trace counter starting at 1**
(`PerfettoTrackState._alloc_uuid`), lazily, on first use of each track. The state object
maps identity (`pid`, `(pid, iid)`, `(pid, iid, name, metric)`) to the allocated UUID, so
the same track reuses its UUID across flushes. Collision-freedom comes from the counter,
not from bit arithmetic.

**`uuid = 0` is reserved.** It is Perfetto's special root descriptor, used to carry
`process_ordering` / `thread_ordering` hints. It is not a parent, and nothing may point
`parent_uuid` at it. The allocator starting at 1 guarantees no user track can collide
with it.

**Descriptor layout:**

- Process: `ProcessDescriptor` at field **3**, with `child_ordering = EXPLICIT` so its
  children can be ordered.
- Thread: `parent_uuid` = the process track, `sibling_order_rank = 0`, and **no**
  `child_ordering`, because thread tracks are leaves and the field would be a no-op. Their
  children (the counters) are siblings under the process, not under the thread.
- Counters: parented to the `GC Metrics` group or to the process track, following
  [ADR-0003](0003-gc-metrics-group-track.md) and [ADR-0004](0004-toplevel-shared-counters.md).
- Track descriptors carry **no timestamp** on their containing `TracePacket`. Descriptors
  are time-independent; an earlier version set a timestamp only on the valid-pause path,
  which was inconsistent with the thread and counter descriptors.

**"Parented to the trace root" means the `parent_uuid` field is absent on the wire**
(`parent_uuid=None`, which the encoder skips), never `parent_uuid=0`.

## Consequences

- Adding a new track kind needs no bit-layout design: ask the state object for a UUID.
- UUIDs are not stable across runs or reproducible from a pid. Nothing depends on that;
  identity is carried by the descriptor's `pid`/`tid`/`name`, which is what the trace
  processor keys on.
- UUIDs stay small, so their varints stay short.
- The `1 << 60` bit-marking is gone. Perfetto's "default track" recognition comes from the
  presence of the `ProcessDescriptor` / `ThreadDescriptor` sub-message, not from the UUID
  value. That is what the field-6 and bit-61 errors above turned on.

## Alternatives considered

- **Arithmetic UUIDs derived from pid/iid** (`pid | 1<<60` etc.). Superseded. Deterministic
  but fragile: it required a new bit-range per track kind, `(pid << 20) | iid` collides for
  large pids, and it encoded a very large varint into every packet for no benefit.
- **`child_ordering = EXPLICIT` on thread tracks.** Rejected as a no-op once counters were
  reparented away from the thread; thread tracks have no children.

## Implementation

- `src/gcmon/exporters/perfetto_track_state.py`, `PerfettoTrackState._next_uuid` seeded to
  `1` and `_alloc_uuid`.
- `:234`, `:239`, `get_process_track_uuid` and `get_thread_track_uuid` (lazy, memoized).
- `:79-83`, `ProcessDescriptorField` (`PID = 1`, `CMDLINE = 2`, `PROCESS_NAME = 6`,
  `START_TIMESTAMP_NS = 7`); the sub-message itself is written at `TrackDescriptor` field 3.
- `:705-725`, `_emit_thread_descriptor`: `parent_uuid` = process track,
  `sibling_order_rank = 0`, no `child_ordering`.
- `:324`, `build_track_descriptor`, which omits `parent_uuid` when it is `None`.
- Tests: `tests/exporters/test_perfetto_format.py`,
  `tests/exporters/test_perfetto_exporter_integration.py` (the trace-processor `track`
  table assertions confirm the parent links survive parsing).
