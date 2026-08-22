# 0056: Intern the strings a trace repeats

- **Status:** Blocked ([0057](0057-compress-the-packets-a-trace-writes.md), which supersedes
  section 1: re-open and re-measure against a compressed baseline)
- **Kind:** feature (efficiency)
- **Effort:** M
- **Origin:** raised 2026-08-22, against the interning alternative
  [ADR-0001](../docs/adr/0001-hand-rolled-perfetto-protobuf-encoder.md) rejected as premature
- **Respects:** [ADR-0001](../docs/adr/0001-hand-rolled-perfetto-protobuf-encoder.md) (hand-rolled
  encoder, every field number a named `IntEnum`, `perfetto` stays dev-only),
  [ADR-0008](../docs/adr/0008-buffered-exporter-and-encoder-protocol.md) (the `EventEncoder` seam),
  [ADR-0011](../docs/adr/0011-process-lifetime-and-ordering.md) (the shared `Processes` track),
  [ADR-0014](../docs/adr/0014-perfetto-integration-test-strategy.md) (assert what the trace means,
  through the trace processor)

## 1. Problem statement

A gcmon trace is mostly text it has already written. Every slice carries its name, its category and
the name of each of its debug annotations spelled out in full, and a run that reads a hundred
thousand records writes the same few dozen strings a hundred thousand times over.

What an operator gets is a file about twice the size it needs to be: slower to copy off the host
the target runs on, slower to attach to an issue, slower for the UI to load.

Scanned at the wire level over the four `.pftrace` files in this repo, counting the bytes spent on
`TrackEvent.name`, `TrackEvent.categories` and `DebugAnnotation.name` including their tags and
lengths:

| trace | size | spent on those strings | distinct strings |
| :-- | --: | --: | --: |
| `output/loss_variants.pftrace` | 267 KB | 62.7% | 25 |
| `output/loss_pauses.pftrace` | 7.4 MB | 67.7% | 28 |
| `scripts/analysis/cyclotron/cyclotron.pftrace` | 36.6 MB | 53.7% | 99 |
| `scripts/analysis/pyperformance/trace.pftrace` | 127.4 MB | 53.4% | 1,619 |

The annotation name is the largest single class. The pyperformance capture writes 2,866,841 of them
drawn from thirteen distinct strings, 29.5% of the file on its own. The distinct count in the last
column grows only with the number of pids, because a `Processes`-track span is named
`Process <pid>` and that capture forked 14,579 times. The strings that repeat are a fixed set.

ADR-0001 rejected interning on the grounds that gcmon "emits one slice per GC pause". It does not:
one record becomes a pause slice, up to seven sub-phase slices and two counter events, and the
pause slice alone carries seven annotation names. The rejection was right about the mechanism and
wrong about the volume.

## 2. Solution

The same trace. Same slices, same names, same categories, same args, same counters, same tracks,
same SQL keys. The file is around half the size.

Nothing an operator types changes and nothing they read changes. There is no flag: a trace is
interned, or gcmon did not write it.

## 3. User stories

1. As an operator attaching to a production process, I want the trace small enough to copy off the
   host without thinking about it, so that moving the file is not the slow part of an
   investigation.
2. As someone opening a capture in the Perfetto UI, I want every slice, arg, counter and track to
   read exactly as it does today, so that traces from either side of this change are comparable.
3. As someone querying a trace with Perfetto SQL, I want `debug.gen0.lost_count` and its neighbours
   to keep their keys, so that the queries in `docs/perfetto-sql.md` keep working unedited.
4. As an operator whose run was killed part way, I want the truncated file to open with its names
   intact, so that a capture that did not finish is still worth reading.
5. As a CI job archiving traces, I want the saving without setting anything, so that no pipeline has
   to be edited to get it.
6. As a gcmon maintainer adding a debug annotation, I want one rule saying which of its two strings
   is interned, so that I do not have to derive it from the wire format.
7. As a gcmon maintainer, I want a wrong intern id to fail a test rather than to be found by a human
   opening the UI, so that this does not become the fifth bug of ADR-0001's kind.

## 4. Implementation decisions

### 4.1 Intern what repeats, never what is unique

Three classes in:

| gcmon writes | interned into | distinct, pyperformance capture |
| :-- | :-- | --: |
| `TrackEvent.name` | `InternedData.event_names` | 1,582 |
| `TrackEvent.categories` | `InternedData.event_categories` | 24 |
| `DebugAnnotation.name` | `InternedData.debug_annotation_names` | 13 |

The third holds at every depth. `_build_debug_annotation_dict` nests its entries as
`DebugAnnotation` messages and their names intern the same way, with the trace processor still
flattening the pair to `debug.gen0.lost_count`.

**Rejected: `DebugAnnotation.string_value`.** gcmon's string values come from `lost_collections` and
`duration_text`, which are near unique by construction: 1,418 distinct out of 1,432 occurrences in
`output/loss_pauses.pftrace`. A table for them costs more bytes than it saves and grows without a
bound over a long run. The rule is **intern the name, never the value**, and it is the rule to apply
to the next annotation anyone adds.

`TrackDescriptor.name` is not an internable field. Perfetto interns on `TrackEvent` and
`DebugAnnotation` only, so every descriptor gcmon writes goes out unchanged and the `descriptors`
list of `convert_trace_events_to_perfetto` is untouched by this spec.

### 4.2 The table lives in `PerfettoTrackState`

It is per-trace identifier allocation, which is what that class already does five times over in
`get_or_create_counter_track_uuid` and its neighbours, and the state is already threaded through
every `_emit_*` helper and the whole conversion pass. No new plumbing.

**Three id spaces, one per interned field, each numbering from 1**, none of them sharing
`_next_uuid`. An `event_names` id 3 and a `debug_annotation_names` id 3 are different strings.
Perfetto reserves `iid = 0`, so the allocators start at 1 as `_next_uuid` does.

The lookup reports whether it minted the id, because the caller has to emit the entry on first use
and only then.

**Rejected: a seventh module in ADR-0001's table.** The auditable part of interning is the field
numbers and the flag discipline, and those live in `perfetto_proto.py` and `perfetto_format.py`
either way. What would move is a dict and a counter.

**Accepted wart:** `PerfettoTrackState` then holds three tables that are not about tracks. Renaming
it touches every `_emit_*` signature and every test module, and it is a separate change.

### 4.3 The entry rides the packet that first uses it

A slice BEGIN that introduces its name, its category and eleven annotation names carries an
`InternedData` holding all thirteen, and no later packet repeats them. This is what the Perfetto SDK
does, it puts the string and its table entry at one point in the code so the two cannot drift, and
it makes every prefix of the file self-describing, which matters because
`ProtobufEventEncoder.write_events` appends per flush and a killed run leaves a truncated file.

**Rejected: one entry packet per batch**, prepended to that batch. Equivalent in bytes, and it
separates the entry from the use.

**Rejected: an upfront table** built from the fixed name and category sets. It splits one mechanism
across two code paths and puts a table beside `trace_converter` that has to be kept in step by hand,
which is the shape ADR-0001 exists to avoid.

### 4.4 The flag discipline

`TracePacket.sequence_flags`, two rules:

- **`SEQ_INCREMENTAL_STATE_CLEARED` on the first packet in the file, and on no other.**
- **`SEQ_NEEDS_INCREMENTAL_STATE` on every packet carrying an intern id.** That is the slice BEGINs
  and the `Processes`-track pair. Not the sub-phase ENDs, which `_make_slice_end` writes as type and
  `track_uuid` alone, and not the counters, which carry no name and no category.

Measured against the trace processor on minimal traces built from gcmon's own primitives, one slice,
one category, one annotation:

| flags | what the trace processor returns |
| :-- | :-- |
| `CLEARED` first, `NEEDS` on the event | the slice, its name, its arg |
| `CLEARED` first, no `NEEDS` | the slice, its name, its arg |
| neither | the slice, `name = NULL`, no args |
| `NEEDS`, no `CLEARED` | no slice at all |

Both wrong rows are silent: the `stats` table reported no error and no data-loss counter for either.
That is ADR-0001's characteristic failure, and it is why `NEEDS` is written even though row two
shows the reader does not currently require it. Conforming to the format is the hedge; relying on a
leniency nothing documents is not.

A second `CLEARED` part way through discards the table. A slice reusing an id after one comes back
with `name = NULL`, which rules out setting the flag per batch.

### 4.5 The root descriptor becomes the first packet on both paths

`_emit_root_descriptor` runs only from `convert_trace_events_to_perfetto`, and only when the batch
has events. A run whose monitor loop reported liveness and whose target never collected writes its
whole file from `finalize_perfetto_packets`, which emits no root descriptor.

That is invisible today: the root descriptor carries `process_ordering` and `thread_ordering`, which
govern top-level process and thread tracks, and such a trace has none. It stops being invisible the
moment that packet carries `CLEARED`. So `finalize_perfetto_packets` emits the root descriptor when
it has not already gone out, and `CLEARED` is a constant on one packet built in one place rather
than a flag every emission site has to remember to ask for.

The guarantee rests on `ProtobufEventEncoder.write_events` writing its descriptors before its
packets. Nothing in the code stops a future edit from swapping those two loops, so section 5 pins
it.

### 4.6 An intern id is not an iid

gcmon identifies an interpreter by its **iid**, and Perfetto calls an interned-string handle by the
same three letters, inside one file: `perfetto_format.py` takes `iid` meaning an interpreter and
imports `loss_iid`. `perfetto_proto.py` keeps Perfetto's spellings, because ADR-0001 requires the
field enums to mirror the proto: `TrackEventField.NAME_IID`, `InternedStringField.IID`. Everywhere
else in gcmon the concept is an **intern id**, spelled `intern_id`. `CONTEXT.md` carries both terms
and points each at the other.

### 4.7 Rejected: compress the packets instead

`TracePacket.compressed_packets` (field 50) with stdlib `zlib` would beat 43% on data this
repetitive and costs one wrapper rather than a state machine. It buys the file size and nothing
else: it adds a deflate pass to a writer that runs beside the process being monitored, and it leaves
the reader inflating a trace whose strings are still repeated. Interning takes bytes out of the
writer, the file and the reader at once. The two are not exclusive, and compression stays available
afterwards.

## 5. Seams and testing decisions

- **Seam:** the trace processor, the highest seam that can observe an intern id at all. A wire-level
  assertion reads an id back through the constant that wrote it, so it passes on a wrong id and a
  right one alike (CONVENTIONS rule 6, ADR-0014).
- **New seam needed:** `tests/exporters/test_perfetto_interning.py`, for the behaviours that span
  layers: an entry arriving before its first use, `CLEARED` landing once, and the multi-batch
  resolution below. The per-layer facts stay in their layer's module, so a failure still names the
  layer: the eight new field numbers in `test_perfetto_proto.py` read out of the `perfetto`
  package's generated descriptors, the raw wire shape of `build_interned_data` and of
  `build_track_event` with an id in `test_perfetto_builders.py`, the three id spaces in
  `test_perfetto_track_state.py`.
- **What makes a good test here:** assert the name, category and args a slice resolves to. Never
  assert an id. An id is an implementation detail of one trace and moves when emission order does.
- **Prior art:** `tests/exporters/test_perfetto_loss_track.py` for the SQL assertions;
  `tests/test_convert_cmd_perfetto.py` for the oracle comparing a decoded trace against the
  `list[TraceEvent]` it was built from, which is the regression guard this change leans on.
- **Cases:**
  1. A run flushed over at least two batches resolves every slice name, category and arg. This is
     the case that catches a second `CLEARED`, and the existing oracle cannot, since `combine`
     converts in one pass. The `perfetto_exporter` fixture already takes a threshold.
  2. The first packet in the file carries `CLEARED` and no later packet does, on the `write_events`
     path and on the liveness-only `close()` path both.
  3. An entry reaches the file no later than the packet referencing it.
  4. The loss slices keep their nested keys, `debug.gen0.lost_count` and its neighbours.
  5. Regression guard: a fixed record sequence resolves to the same slices, args, counters and
     tracks as the `list[TraceEvent]` it was built from.

`tests/monitoring/test_monitored_run_trace.py` pins a whole run as decoded `TracePacket` text, and
interning rewrites every packet in it. Re-pinning it is part of this change rather than a surprise
found during it.

## 6. Out of scope

- **Compressing the trace.** Section 4.7 says why it is not the same change. It stays available
  afterwards and the two compose.
- **Interning `DebugAnnotation.string_value`.** Section 4.1: it costs bytes and bounds nothing.
- **A flag to turn interning off.** A flag with one real value is a question with one answer, the
  ground [ADR-0021](../docs/adr/0021-write-one-trace-format.md) rejected `--input-format` on. It
  would also mean keeping both encodings alive in order to test the second.
- **Reading a Perfetto trace back.** Still outside the encoder's remit (ADR-0001, ADR-0021).
- **Reshaping `TraceEvent` around Perfetto's vocabulary.** ADR-0021 left that open and this does not
  take it.
- **Renaming `PerfettoTrackState`.** Section 4.2.

## 7. Further notes

**ADR.** Write ADR-0023 for the interning decisions -- 0055 took ADR-0021 and 0057 took ADR-0022
-- covering the three classes and the excluded fourth, the flag discipline, and the two silent
failure modes. Amend ADR-0001's rejected `name_iid` bullet to
point at it, saying the alternative was reversed on the shape of what gcmon writes rather than on
new information about Perfetto. ADR-0001 stays `Accepted`; its decision, a hand-rolled encoder with
`perfetto` out of the runtime tree, is untouched. Its "three rules make this safe" list gains
nothing, since the fourth rule belongs to ADR-0023.

**CONTEXT.md** already carries **Intern id**, added 2026-08-22, with **Interpreter** pointing at it.

**CHANGELOG.** One line under `Features`. The file halving is what an operator sees, so it does not
go under the standing `### Internal` line.

**Nothing orders this.** 0035 rewrites how the sub-phase names and categories are produced. It
changes which strings exist, not how they are written, and either order works.
