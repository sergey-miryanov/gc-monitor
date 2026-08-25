# 0065: Name the track an event is drawn on

- **Status:** Not started
- **Kind:** feature (cleanup)
- **Effort:** M
- **Origin:** grilling session, 2026-08-26, on whether `TraceEvent` survives
  the Chrome format's removal. **Supersedes** spec 0026 entirely and §4.1 of
  [0037](0037-one-meta-emission-path-for-live-and-combined-traces.md); 0037's
  §4.2 is independent of this and stays open.
- **Respects:** [ADR-0006](../docs/adr/0006-begin-end-slice-pairs.md) (a span
  is a begin/end pair),
  [ADR-0007](../docs/adr/0007-shared-trace-converter-pipeline.md) (one
  conversion pipeline),
  [ADR-0008](../docs/adr/0008-buffered-exporter-and-encoder-protocol.md) (the
  exporter buffers, the encoder serializes, and `combine` drives an encoder
  with no exporter),
  [ADR-0015](../docs/adr/0015-gc-loss-spans-on-their-own-track.md) (loss gets
  a row of its own)
- **Overturns:** [ADR-0004](../docs/adr/0004-toplevel-shared-counters.md) in
  part, and one anchor each in
  [ADR-0007](../docs/adr/0007-shared-trace-converter-pipeline.md) and
  [ADR-0013](../docs/adr/0013-rss-sampling.md). ADR-0024 records what replaces
  them.

## 1. Problem statement

A process running two interpreters draws two counter rows, both labelled
`heap_size`, and nothing in the trace says which interpreter either belongs
to. They are distinct series over distinct heaps. An operator reading them has
to guess, and a PerfettoSQL query selecting `name = 'heap_size'` returns both
without telling them apart.

A capture carries a `tid` field on every line. `docs/formats.md` documents it
as the sentinel the trace formats draw the `GC Loss` track on, with `-1`
reserved for `rss`. No trace format reads it: `read_jsonl` hands each line to
`from_mapping`, which rebuilds the record from its own fields, and every loss
record already carries the `iid` the sentinel was derived from. For a GC
record the field is a second copy of `iid`. An operator writing `jq '.tid'`
against a capture is reading a number gcmon writes and never uses.

Behind both sits one cause. `TraceEvent` still has the shape the Chrome Trace
Event format gave it, and gcmon stopped writing that format in
[ADR-0021](../docs/adr/0021-write-one-trace-format.md). A track is identified
by a `(pid, tid)` pair, so a row belonging to no interpreter takes a tid no
interpreter would claim: `-1` for RSS, `-2 - iid` for loss. Every event
carries a `ph` discriminator the union already encodes, an instant carries an
`s`, and `ProcessMeta` and `ThreadMeta` are metadata events whose only
payload, a `NameInfo`, no encoder has read since the Chrome one went.

ADR-0021 named this and deferred it: "`TraceEvent` keeps its Chrome-derived
shape... reshaping it around Perfetto's own vocabulary is a separate change to
the converter, the track state and the loss-slice builder." This is that
change.

## 2. Solution

A counter row is named for its metric, qualified by the interpreter that owns
it when an interpreter does. `heap_size` becomes `Thread 0 heap_size`,
`Thread 1 heap_size`, and so on, so two interpreters in one process read
apart. `rss` stays `rss`: it belongs to the process, and there is never a
second one to tell it from.

A capture stops carrying `tid`. A reader who wants the interpreter reads
`iid`, which every record already carries and which is what `tid` was derived
from.

Nothing else in a trace moves. The same slices, names, categories, args,
counters and tracks come out, in the same order.

## 3. User stories

1. As someone reading a trace from a process with subinterpreters, I want each
   `heap_size` row labelled with the interpreter behind it, so that I can tell
   two heaps apart without opening the capture.
2. As someone writing PerfettoSQL against a trace, I want a counter track name
   that identifies its series, so that a join on track name does not silently
   merge two interpreters.
3. As an operator with a capture on disk, I want every field in it to mean
   something, so that reading the format's documentation does not send me
   after a number nothing uses.
4. As an operator running one interpreter, which is nearly everyone, I want
   nothing else about my trace to change, so that upgrading costs me one
   renamed row and no reading.
5. As a maintainer adding a row kind, I want to name it rather than allocate
   it a negative number, so that nothing has to document which integers are
   spoken for.
6. As a maintainer changing how a track is named or described, I want one
   place to change it, so that the offline path cannot keep the old form. That
   is spec 0026's story, and this spec is how it gets answered.
7. As a maintainer, I want the meta dedup to stay correct under concurrency,
   so that removing a critical section does not reopen the race ADR-0008
   closed.
8. As the person landing the pyperf marks, I want an instant able to carry
   annotations, so that a mark's parts do not have to be packed into its name.

## 4. Implementation decisions

**4.1: A `Track` names a row; an event names its track.**
`src/gcmon/model/trace_event.py` gains three frozen structs and loses the
`(pid, tid)` pair from every event:

```python
class ThreadTrack(msgspec.Struct, frozen=True):   # its collections
    pid: int
    iid: int

class LossTrack(msgspec.Struct, frozen=True):     # its loss row
    pid: int
    iid: int

class ProcessTrack(msgspec.Struct, frozen=True):  # marks, RSS
    pid: int

type Track = ThreadTrack | LossTrack | ProcessTrack
```

`ThreadTrack` and `LossTrack` carry identical fields and are distinguished by
type alone. That is deliberate: they are two rows for one interpreter, which
is what ADR-0015 decided, and a boolean saying "draw this elsewhere" is the
same distinction with its name taken off. `PerfettoTrackState` keys its uuid
tables on a `Track` rather than a signed int; `RSS_TID`, `LOSS_TID_BASE`,
`loss_tid` and `loss_iid` go; and the guard in the loss-descriptor emitter
that tests a tid against a sentinel becomes an `isinstance`.

**Rejected: name what an event is *about* rather than the row it lands on.**
Two members, a process and an interpreter, matching the entries `CONTEXT.md`
already carries. It fails on loss: a loss slice and a GC pause are about the
same interpreter and differ only in where they are drawn, so that model needs
a second discriminator to say the one thing this one says for free.

**Rejected: emit Perfetto packets straight from the converter and delete the
intermediate.** ADR-0007's stated reason for `TraceEvent` was two backends,
and that reason is gone, but two others outlive it. `BufferedTraceExporter`
needs something batchable between a poll and a flush, and buffering bytes
instead would put the encoder's track state under the exporter's IO lock on
every record. `combine` drives `ProtobufEventEncoder` with no exporter at all,
which ADR-0008 chose on purpose. It also costs the two seams that make the
encoder testable: `TestTheTraceMatchesTheEventsItWasBuiltFrom` has no oracle
without a list of events to compare a trace against, and the encoder's unit
tests place one event and read one descriptor, which no `TGCStatsInfo` can
express — RSS does not arrive as a record at all.

**4.2: The events are the four things gcmon draws.**

```python
class SliceBegin(msgspec.Struct):
    track: Track
    name: str
    cat: str
    ts: int
    args: EventArgs

class SliceEnd(msgspec.Struct):
    track: Track
    ts: int

class Instant(msgspec.Struct):
    track: ProcessTrack
    name: str
    ts: int
    args: EventArgs = msgspec.field(default_factory=dict)

class Counter(msgspec.Struct):
    track: Track
    metric: str
    display_name: str
    ts: int
    value: int | float

type TraceEvent = SliceBegin | SliceEnd | Instant | Counter
```

`ph` and `s` go: the union member is the discriminator, and the only reader of
`ph` outside the tests is the timestamp normalizer in `combine`, whose filter
for the events carrying a `ts` now matches every member. `SliceEnd` loses
`name` and `cat`, which the encoder has never read — it closes a slice with
the track uuid alone.

A span stays a begin/end pair. ADR-0006's reason holds: Perfetto has no
complete-event primitive, so a struct carrying both timestamps would move the
split into the encoder, and `_loss_in_time_order` depends on an END and the
next BEGIN keeping their emission order at a shared instant.

**4.3: One `Counter` per metric, with the display name written not derived.**
`convert_item_to_trace_format` emits one `Counter` per metric instead of one
event carrying a dict of them, and fills `display_name` itself. The encoder
loses its inner loop and the branch that reads

```python
single_arg = len(event.args) == 1
display_name = metric if single_arg else f"{event.name} {metric}"
```

That branch exists for one reason, which ADR-0004 states: without it a counter
event named `heap_size` carrying one arg keyed `heap_size` produces the track
name `heap_size heap_size`. With the name written by the converter the
collision cannot arise, and the last naming string moves to where ADR-0007 put
the rest of them.

**4.4: `heap_size` is qualified by its interpreter, unconditionally.** The
display name is `f"Thread {iid} heap_size"`, matching the
`f"{owner} {metric}"` shape `G0 collected` already uses and the
`f"Thread {iid}"` the interpreter's own track carries.

The qualifier cannot be conditional. gcmon emits a counter descriptor the
first time it sees that metric, inside `write_events`, batch by batch. When
interpreter 0's descriptor goes out, interpreter 1 may not have produced a
record yet. Any rule of the form "qualify only when there is a sibling to
qualify against" is unimplementable on a streaming writer, and that includes
leaving iid 0 bare. `CONTEXT.md` resists the same shortcut on its own terms:
an iid of 0 is an interpreter too.

`rss` stays bare. Its owner is the process, its row is already drawn under
`Process {pid}`, and a process holds one.

**4.5: `rss` leaves the top-level metric set; `heap_size` stays.** ADR-0004's
set held both and did one job for them: parent to the process track, outside
the `GC Metrics` group. Under 4.1 those are no longer one job. `rss`'s track
*is* `ProcessTrack(pid)`, so parenting it to the process row is identity
rather than policy and needs no entry. `heap_size` is owned by a
`ThreadTrack(pid, iid)` and deliberately drawn one level up, which is a real
override and the set's only remaining member.

**4.6: Meta events go; the encoder derives every descriptor from
`event.track`.** `ProcessMeta`, `ThreadMeta`, `NameInfo`, `process_meta` and
`thread_meta` go, with `BufferedTraceExporter._build_meta` and its
`_seen_pids` / `_seen_tids` sets, and the thread-meta loop in
`convert_to_trace_format`.

Nothing is lost, because nothing read them. The encoder names a process track
`f"Process {pid}"` and a thread track `f"Thread {iid}"` from the pid and iid
themselves, and has never looked at the `NameInfo` either event carried. That
is why 0026 retires rather than lands: the two spellings it found are real,
`f"Process {pid}"` against `f"{pid}"` and `f"Thread {iid}"` against
`f"{pid}:{tid}"`, and neither reaches a trace.

ADR-0007's "`ProcessMeta` precedes `ThreadMeta` for a given pid... part of the
public contract of the event stream" stops being a contract a producer keeps
and becomes true by construction: one descriptor goes out per track, and a
process descriptor is emitted for any track naming a pid not yet described.

**The dedup race closes by deletion, not relocation.** ADR-0008 closed a
check-and-emit race between two producers under `BufferedTraceExporter._lock`.
With no producers left, dedup lives only in `PerfettoTrackState`, which is
reached through `write_events` and `record_process_liveness`, both of which
run under `_io_lock`. There is one site, and it is already serialized.

**4.7: `tid` leaves the capture.** `JsonlExporter` stops writing it on all
three record kinds, and `docs/formats.md` loses the field from both tables.
Captures written before this still read: `from_mapping` already tolerates the
field, which is why a capture round-trips today.

**4.8: An `Instant` can carry args, and nothing fills it yet.** The encoder
attaches debug annotations to an instant the way it does to a slice, skipped
when the dict is empty. No producer exists: `TInstantMsg` is `type`, `name`
and `ts`, so neither the live path nor `combine` has anything to put there.

The field is added here rather than later because this spec rewrites the
module and the encoder branch, and because `.scratch/pyperf-marks/issues/03`
needs it. The mark grammar `<benchmark>:<n>:<i>:begin` stays packed into the
name until `02-mark-grammar-module` decides otherwise: moving it into
annotations is that ticket's call, and giving `TInstantMsg` an `args` field is
a wire-format and capture-format change that belongs with the caller needing
it.

**4.9: The factories go.** `begin_event`, `end_event`, `instant_event` and
`counter_event` exist to fill `ph` and reorder arguments. With `ph` gone they
are a second name for a constructor, and `trace_converter` is the only caller
that matters. The module is left holding structs and two type aliases.

One comment survives the deletion, onto `SliceBegin`: that the slice owns its
`args` dict rather than copying it, because every caller builds one for a
single event and drops it, and the copy was the largest single cost of
converting a record. That is a measured decision, not a restatement of the
code.

## 5. Seams and testing decisions

- **Seam:** the two that already read a gcmon trace through a decoder gcmon
  did not write. `TestTheTraceMatchesTheEventsItWasBuiltFrom` in
  `tests/test_convert_cmd_perfetto.py` compares a combined `.pftrace` read
  through the trace processor against the events the same input produced; it
  is the highest seam available, and ADR-0021 installed it for exactly this
  kind of change. `tests/monitoring/test_monitored_run_trace.py` pins a whole
  live run against `tests/fixtures/monitored_run_perfetto_trace.txt`.
- **New seam needed:** none. Every change below is observable through one of
  those two, or through the encoder unit tests in `tests/exporters/` that
  already place one event and read one descriptor.
- **What makes a good test here:** the label change is asserted through the
  trace processor, as a counter track name resolved from a two-interpreter
  capture, not as a string the encoder was handed. The structural steps are
  asserted by what does *not* move: the fixture and the oracle both pass
  untouched.
- **Prior art:** `tests/exporters/test_perfetto_counter_tracks.py` for the
  counter descriptors, `tests/exporters/test_perfetto_loss_track.py` for the
  loss row, `TestMetaDedupRaceClosed` in
  `tests/exporters/test_buffered_exporter.py` for the concurrency case.
- **Cases:**
  1. A capture with two interpreters in one process yields two counter tracks,
     `Thread 0 heap_size` and `Thread 1 heap_size`, resolved through the trace
     processor.
  2. `rss` resolves as `rss`, parented to the process track, with no
     interpreter in its name.
  3. A loss row still resolves as `GC Loss {iid}`, parented to the process
     track, carrying no `thread` sub-message.
  4. Two concurrent `add_event` calls for one pid yield exactly one process
     descriptor in the bytes. `TestMetaDedupRaceClosed` stops reading the
     exporter's buffer and asserts this instead.
  5. A run whose events all sit on one interpreter produces a trace whose
     every other slice name, category, arg, counter and track is what it was.
  6. `mypy --strict` and pyrefly pass, on the Linux leg as well as locally.

The fixture moves twice, and the two moves read differently. Splitting
counters per metric shifts where batch boundaries fall, and `_write_batch`
writes a batch's descriptors ahead of its packets, so descriptors move
relative to the packets around them in the flattened stream. The label change
moves a name. Each is its own commit, so each diff can be read for what it
claims to be.

## 6. Out of scope

- **The `heap_size` parenting.** It stays drawn under the process track,
  outside `GC Metrics`, exactly as ADR-0004 decided. This changes what the row
  is called, not where it hangs.
- **Giving `TInstantMsg` an `args` field**, and with it the control-plane
  message and the capture format. See 4.8: the caller that needs it is
  choosing the shape of those annotations, and it should choose them.
- **[0035](0035-derive-every-gc-sub-phase-from-one-table.md).** The sub-phase
  table is the largest maintenance cost in `trace_converter`, and it is
  orthogonal: this spec changes what a converted event looks like, not how
  many places decide which sub-phases exist. Landing this first leaves 0035
  the same size.
- **[0036](0036-one-exporter-method-per-record-kind.md) and §4.2 of
  [0037](0037-one-meta-emission-path-for-live-and-combined-traces.md).** Both
  are about the exporter's method set and the format dispatch, neither of
  which this touches.
- **The `docs/formats.md` GC-record examples**, which show `"tid": 0` beside
  `"iid": 1` and `"iid": 2` where `JsonlExporter` writes `tid = item.iid`.
  Wrong before this spec, and the field they are wrong about is being deleted;
  correcting the surrounding examples is not this change's business.

## 7. Further notes

`LOSS_TID_BASE` is load-bearing in one more place than the encoder:
`tests/exporters/loss_row.py` selects loss events by
`event.tid <= LOSS_TID_BASE`. That helper becomes an `isinstance` against
`LossTrack`, which is what it was trying to say.

The offline path's thread descriptors become lazy. `convert_to_trace_format`
emits every thread meta for a pid up front, so today a combined trace carries
all of a pid's thread descriptors before any of its slices; deriving them from
`event.track` emits each at that track's first slice. The order of descriptors
in a combined trace therefore changes. No reader is affected, the oracle
compares slices through SQL rather than packet order, and the live path is
unaffected because `_build_meta` already prepends its meta to the same batch
as the events that triggered it.
