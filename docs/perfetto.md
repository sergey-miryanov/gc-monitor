---
id: spec-feat-02
title: Perfetto Binary Format Exporter
status: draft
version: 1.0.0
last_updated: 2026-06-06
depends_on: 
  - spec-01-chrome-trace-exporter
owner: @engineering-team
complexity: medium
---

# Feature: Perfetto Binary Format Exporter

## 1. Objective & User Story
**As a** performance engineer analyzing Python GC behavior,  
**I want to** export GC monitoring data in Perfetto's native binary protobuf format,  
**So that** I can visualize traces in Perfetto UI with full fidelity (nested slices, counter tracks, debug annotations) and benefit from smaller file sizes compared to JSON.

*Example: As a user debugging GC pauses in a long-running service, I want to export to `.pb` format so that Perfetto UI renders nested GC sub-phases (mark alive, deduce unreachable, etc.) as hierarchical slices with per-generation counter tracks.*

## 2. Context & Dependencies
- Builds on the existing `EventsExporter` base class (`src/gc_monitor/exporters/exporter.py`).
- Reuses the GC-to-event conversion logic from `chrome_trace_format.py` (same sub-phase detection, same timestamp conversion).
- Requires a minimal protobuf wire-format encoder (no external `protobuf` dependency).
- Follows the Perfetto TracePacket specification: https://perfetto.dev/docs/reference/trace-packet-proto
- Uses the same threading/flush pattern as `TraceExporter` for incremental writes.

## 3. Functional Requirements

- [ ] **REQ-1**: The exporter must produce a valid binary protobuf file loadable by Perfetto UI (https://ui.perfetto.dev).
- [ ] **REQ-2**: Each GC pause must be represented as a `TYPE_SLICE_BEGIN` / `TYPE_SLICE_END` pair on a thread-associated track.
- [ ] **REQ-3**: GC sub-phases (mark alive, fill increment, deduce unreachable, handle weakrefs, finalize garbage, handle resurrected, clear weakrefs, delete garbage) must be emitted as nested slices within the parent pause slice.
- [ ] **REQ-4**: Each counter metric (collected, uncollectable, candidates, heap_size, increment_size, alive_size, finalized_garbage_count, deleted_garbage_count, clear_weakrefs_count) per generation must have its own counter track parented to the thread track.
- [ ] **REQ-5**: Process and thread metadata must be emitted as `TrackDescriptor` packets with `name`, `pid`, `tid`, and `parent_uuid` fields.
- [ ] **REQ-6**: Instant events (monitor start/stop markers) must be emitted as `TYPE_INSTANT` TrackEvents.
- [ ] **REQ-7**: GC metadata (generation, iid, collections, heap_size, etc.) must be attached to slice events as `DebugAnnotation` key-value pairs.
- [ ] **REQ-8**: The exporter must support incremental flushing (write packets to disk as they are generated, not buffer the entire trace in memory).
- [ ] **REQ-9**: The CLI must accept `--format perfetto` as a valid output format choice.
- [ ] **REQ-10**: The output file must be written in binary mode (`"ab"`) and contain a valid sequence of `TracePacket` messages wrapped in a `Trace` message.

## 4. Technical Implementation Details

### 4.1 Perfetto Proto Schema (Subset Used)

```
message Trace {
  repeated TracePacket packet = 1;
}

message TracePacket {
  optional uint64 timestamp = 8;
  optional TrackEvent track_event = 11;
  optional TrackDescriptor track_descriptor = 60;
}

message TrackEvent {
  enum Type {
    TYPE_SLICE_BEGIN = 1;
    TYPE_SLICE_END = 2;
    TYPE_INSTANT = 3;
    TYPE_COUNTER = 4;
  }
  optional Type type = 1;
  optional uint64 track_uuid = 2;
  repeated string categories = 3;
  optional string name = 4;
  optional int64 counter_value = 5;
  repeated DebugAnnotation debug_annotations = 6;
}

message TrackDescriptor {
  optional uint64 uuid = 1;
  optional string name = 2;
  optional uint64 parent_uuid = 5;
  optional ThreadDescriptor thread = 4;
  optional CounterDescriptor counter = 8;
}

message ThreadDescriptor {
  optional int32 pid = 1;
  optional int32 tid = 2;
}

message CounterDescriptor {
  enum BuiltinCounterType {
    COUNTER_UNSPECIFIED = 0;
    COUNTER_THREAD_TIME_NS = 1;
    COUNTER_THREAD_INSTRUCTION_COUNT = 2;
  }
  optional BuiltinCounterType type = 1;
  repeated CounterCategory categories = 2;
}

message CounterCategory {
  optional string name = 1;
}

message DebugAnnotation {
  optional string name = 1;
  oneof value {
    bool bool_value = 2;
    uint64 uint_value = 3;
    int64 int_value = 4;
    double double_value = 5;
    string string_value = 6;
  }
}
```

### 4.2 Track UUID Scheme (Deterministic, Collision-Free)

| Track Type | UUID Formula | Example (pid=12345, iid=0, gen=0) |
|---|---|---|
| Process | `pid \| (1 << 60)` | `1152921504606846977` |
| Thread | `(pid << 20) \| iid \| (2 << 60)` | `12970371072000000000` |
| Counter (per metric per gen) | Sequential counter starting at `3 << 60` | `3458764513820540928` |

### 4.3 File Structure to Generate/Modify

**New Files:**
- `src/gc_monitor/exporters/protobuf_encoder.py` — Minimal write-only protobuf wire-format encoder (~100 LOC)
- `src/gc_monitor/exporters/perfetto_format.py` — Perfetto message builders + GC-to-Perfetto conversion (~250 LOC)
- `src/gc_monitor/exporters/perfetto_exporter.py` — `PerfettoExporter` class (~150 LOC)
- `tests/exporters/test_protobuf_encoder.py` — Varint encoding, field encoding tests (~100 LOC)
- `tests/exporters/test_perfetto_format.py` — Message construction, track UUID tests (~150 LOC)
- `tests/exporters/test_perfetto_exporter.py` — End-to-end exporter tests (~200 LOC)

**Modified Files:**
- `src/gc_monitor/exporters/exporter_factory.py` — Add `"perfetto"` case
- `src/gc_monitor/exporters/__init__.py` — Export `PerfettoExporter`
- `src/gc_monitor/commands/monitoring_options.py` — Add `"perfetto"` to `--format` choices
- `tests/exporters/conftest.py` — Add `perfetto_exporter` factory fixture
- `tests/helpers.py` — Add `assert_valid_perfetto_trace()` helper

### 4.4 Protobuf Wire-Format Encoder API

```python
# protobuf_encoder.py

def encode_varint(value: int) -> bytes:
    """Encode unsigned varint (0 to 2^64-1)."""

def encode_signed_varint(value: int) -> bytes:
    """Encode signed varint using zigzag encoding (for sint64)."""

def encode_field_key(field_number: int, wire_type: int) -> bytes:
    """Encode field key: (field_number << 3) | wire_type."""

def encode_varint_field(field_number: int, value: int) -> bytes:
    """Encode a varint field (wire type 0)."""

def encode_fixed64_field(field_number: int, value: int) -> bytes:
    """Encode a fixed64 field (wire type 1)."""

def encode_string_field(field_number: int, value: str) -> bytes:
    """Encode a string field (wire type 2)."""

def encode_bytes_field(field_number: int, value: bytes) -> bytes:
    """Encode a bytes field (wire type 2, also for embedded messages)."""

def encode_double_field(field_number: int, value: float) -> bytes:
    """Encode a double field (wire type 1, fixed 64-bit)."""
```

### 4.5 Perfetto Message Builders API

```python
# perfetto_format.py

TYPE_SLICE_BEGIN = 1
TYPE_SLICE_END = 2
TYPE_INSTANT = 3
TYPE_COUNTER = 4

class PerfettoTrackState:
    """Tracks which pids/iids have been seen, assigns counter track UUIDs."""
    def get_process_track_uuid(self, pid: int) -> int: ...
    def get_thread_track_uuid(self, pid: int, iid: int) -> int: ...
    def get_counter_track_uuid(self, pid: int, iid: int, gen: int, metric: str) -> int: ...

def build_track_descriptor(
    uuid: int,
    name: str,
    pid: int | None = None,
    tid: int | None = None,
    parent_uuid: int | None = None,
    is_counter: bool = False,
) -> bytes:
    """Build a TrackDescriptor message (serialized bytes)."""

def build_trace_packet(
    timestamp: int | None = None,
    track_event: bytes | None = None,
    track_descriptor: bytes | None = None,
) -> bytes:
    """Build a TracePacket message (serialized bytes)."""

def build_track_event(
    type: int,
    track_uuid: int,
    name: str | None = None,
    categories: list[str] | None = None,
    counter_value: int | None = None,
    debug_annotations: list[tuple[str, int | str | float | bool]] | None = None,
) -> bytes:
    """Build a TrackEvent message (serialized bytes)."""

def build_trace(packets: list[bytes]) -> bytes:
    """Wrap packets in a root Trace message."""

def convert_item_to_perfetto_packets(
    pid: int,
    item: TGCStatsInfo,
    state: PerfettoTrackState,
) -> list[bytes]:
    """Convert a GCStatsInfo to a list of TracePacket bytes (mirrors convert_item_to_trace_format)."""
```

### 4.6 PerfettoExporter Class

```python
# perfetto_exporter.py

class PerfettoExporter(EventsExporter):
    """
    Exporter for Perfetto binary protobuf format.
    
    Writes TracePacket messages incrementally to a binary file.
    On close(), wraps all packets in a root Trace message.
    """
    
    def __init__(self, output_path: Path, flush_threshold: int = 1000) -> None:
        super().__init__()
        self._lock = threading.Lock()
        self._io_lock = threading.Lock()
        self._packets: list[bytes] = []
        self._flush_threshold = flush_threshold
        self._output_path = output_path
        self._closed = False
        self._event_count = 0
        self._track_state = PerfettoTrackState()
        self._pending_descriptors: list[bytes] = []
    
    def add_event(self, pid: int, item: TGCStatsInfo) -> None:
        # Emit TrackDescriptors for new pids/iids
        # Convert item to packets
        # Buffer and flush at threshold
    
    def add_instant_event(self, pid: int, item: TInstantMsg) -> None:
        # Emit TYPE_INSTANT TrackEvent
    
    def close(self) -> None:
        # Flush remaining packets
        # Wrap all packets in root Trace message
        # Write final binary file
    
    def get_event_count(self) -> int:
        return self._event_count
```

## 5. Edge Cases & Error Handling

- **Empty Trace**: If no events are added before `close()`, write a minimal valid `Trace` message with zero packets (empty bytes).
- **Multiple Processes**: Each unique `pid` gets its own process track. Thread tracks are parented to their respective process tracks.
- **Counter Track Proliferation**: If a trace has many generations and metrics, the number of counter tracks can grow. Cap at 100 counter tracks per (pid, iid) to avoid UI clutter. If exceeded, attach excess metrics as debug annotations instead.
- **Invalid Timestamps**: If `ts_start >= ts_stop`, skip the event entirely (do not emit a zero-duration slice).
- **File I/O Errors**: Wrap file writes in try/except. On `OSError`, log the error and raise `RuntimeError` with context.

## 6. Acceptance Criteria (Definition of Done)

- [ ] `PerfettoExporter` class is implemented and inherits from `EventsExporter`.
- [ ] Protobuf encoder passes all unit tests (varint encoding, field encoding, message nesting).
- [ ] Perfetto message builders produce valid binary output loadable by Perfetto UI.
- [ ] All GC sub-phases are emitted as nested slices with correct parent-child relationships.
- [ ] Counter tracks are created per metric per generation, parented to thread tracks.
- [ ] Debug annotations are attached to slice events with correct key-value pairs.
- [ ] CLI accepts `--format perfetto` and produces a `.pb` file.
- [ ] Incremental flushing works (packets written to disk before `close()`).
- [ ] Unit tests cover happy paths and all edge cases (minimum 85% coverage for new modules).
- [ ] Code passes `ruff check` and `pyrefly check` with zero errors.
- [ ] Example trace file (`gc_trace.pb`) is generated and validated in Perfetto UI.

## 7. Out of Scope (For This Iteration)

- [ ] Reading/parsing Perfetto binary traces (only write support).
- [ ] Integration with the `combine` command (Perfetto-to-Perfetto merging).
- [ ] Custom Perfetto UI plugins or extensions.
- [ ] Compression (deflate) of trace packets.
- [ ] Interning of repeated strings (categories, names) for size optimization.
- [ ] Clock synchronization (uses default trace clock).

## 8. AI Implementation Plan (Step-by-Step)

1. **Step 1**: Create `src/gc_monitor/exporters/protobuf_encoder.py` with minimal wire-format encoder functions. Write unit tests in `tests/exporters/test_protobuf_encoder.py`. Verify varint encoding matches protobuf spec.
   
2. **Step 2**: Create `src/gc_monitor/exporters/perfetto_format.py` with Perfetto message builders (`build_track_descriptor`, `build_trace_packet`, `build_track_event`, `build_trace`). Implement `PerfettoTrackState` for UUID management. Write unit tests in `tests/exporters/test_perfetto_format.py`.

3. **Step 3**: Implement `convert_item_to_perfetto_packets()` in `perfetto_format.py` to convert `GCStatsInfo` to Perfetto packets (mirrors `convert_item_to_trace_format`). Include debug annotations for GC metadata.

4. **Step 4**: Create `src/gc_monitor/exporters/perfetto_exporter.py` with `PerfettoExporter` class. Follow `TraceExporter` patterns for threading, flushing, and closing. Write end-to-end tests in `tests/exporters/test_perfetto_exporter.py`.

5. **Step 5**: Wire `PerfettoExporter` into `exporter_factory.py`, `__init__.py`, and `monitoring_options.py`. Add `"perfetto"` to CLI `--format` choices.

6. **Step 6**: Update `tests/exporters/conftest.py` with `perfetto_exporter` fixture. Add `assert_valid_perfetto_trace()` helper to `tests/helpers.py`.

7. **Step 7**: Run `ruff check` and `pyrefly check` to ensure code quality. Generate a sample trace file and validate in Perfetto UI.

8. **Step 8**: Report results to the user for approval.

---
*Note to AI: Before generating any code for this feature, confirm you have read this spec and the dependent files (exporter.py, chrome_trace_format.py, chrome_trace_exporter.py). Execute Step 1 and wait for my confirmation before proceeding to Step 2.*
