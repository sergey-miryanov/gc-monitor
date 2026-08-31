# 0066: Give each process on a reused pid its own rows

- **Status:** **Landed** 2026-08-31. The pin inverted:
  `tests/exporters/test_perfetto_track_state.py::TestTwoProcessesOnOnePidShareTheirRows`
  became `TestTwoProcessesOnOnePidGetTheirOwnRows`.
- **Kind:** feature (enhancement)
- **Effort:** M
- **Origin:** spec 0059, retired, see [RETIRED.md](RETIRED.md); it separated
  the spans and left every other row merged
- **Respects:**
  [ADR-0002](../docs/adr/0002-perfetto-track-uuid-and-hierarchy.md) (uuid
  allocation and explicit parenting),
  [ADR-0003](../docs/adr/0003-gc-metrics-group-track.md) (the counter group
  hangs off a process track; this spec amends it),
  [ADR-0005](../docs/adr/0005-counter-y-axis-share-key.md) (a shared y axis
  cannot cross a process; this spec amends it),
  [ADR-0010](../docs/adr/0010-process-identity-cmdline-and-start-marker.md)
  (the cmdline pair and the `Start Process` marker; this spec amends it),
  [ADR-0011](../docs/adr/0011-process-lifetime-and-ordering.md) (the
  `Processes` track, its spans and process ordering; this spec amends it),
  [ADR-0015](../docs/adr/0015-gc-loss-spans-on-their-own-track.md) (the
  `GC Loss` row and its flatness; this spec amends it),
  [ADR-0016](../docs/adr/0016-the-ring-is-the-statistics-unit.md) (the keys
  the trace side draws on; this spec amends it),
  [ADR-0024](../docs/adr/0024-an-event-names-the-track-it-is-drawn-on.md) (an
  event names its `Track` and the encoder derives the rest),
  [ADR-0025](../docs/adr/0025-create-every-process-in-one-place.md) (a
  `Process` is what a record is filed under, and only the monitor creates one)

## 1. Problem statement

A pid handed on in a worker tree names two processes, and one row tells them
apart: an operator reads `Process 12345` and `Process 12345#2` on the
`Processes` track. Every other row merges them into a single `Process 12345`
process track. Its thread row interleaves both processes' pauses, its
`GC Loss` row tiles both their blind intervals, its counters step from one
process's values to the other's with nothing marking where, and its
`start_timestamp_ns` and `Start Process` marker are the first process's, so
the row is stamped before the second process existed and the UI orders it
there.

The command line is wrong rather than merged: gcmon reads one per process and
draws the right one on each span, so the `#2` span and the track above it name
different programs. In SQL one `upid` covers both, so a per-process aggregate
joins through the `Processes` track on a timestamp range instead of grouping.

## 2. Solution

Each process gets its own rows. A pid held twice draws two process tracks,
`Process 12345` and `Process 12345#2`, each carrying the thread row, the loss
row, the counter group and counter rows, the `Start Process` marker, the
command line and the start time of the process it names. A span on the
`Processes` track and the process track of the same name describe the same
process, and `GROUP BY upid` is per process.

Each process draws only the rows its own events name. If the second process
never ran a gen-2 collection, `G2 collected` appears under the first alone.

Where no pid was reused there is nothing to split, and the trace is unchanged.

## 3. User stories

1. As someone reading a counter for a recycled pid, I want one line per
   process, so that a step in `G0 collected` is a fact about a process rather
   than an artifact of where one ended and the next began.
2. As someone reading a pause row, I want one per process, so that two
   processes' collections are not interleaved into a history neither of them
   had.
3. As someone reading a process track, I want its start time to be its own, so
   that the row is not stamped before the process existed and the UI does not
   order it as though it were.
4. As someone reading a command line, I want the process track and the span
   above it to agree, so that a trace holds one answer rather than two.
5. As someone querying a trace from SQL, I want two `upid`s for a pid held
   twice, so that a per-process aggregate is a `GROUP BY` rather than a join
   through the `Processes` track on a timestamp range.
6. As an operator on an ordinary run where no pid was reused, I want my trace
   unchanged, so that this costs me nothing and dates nothing.
7. As a user of `--format jsonl`, I want my output byte-identical, so that a
   Perfetto-only feature stays Perfetto-only.

## 4. Implementation decisions

**The records carry the decision; this spec implements it.**
[ADR-0011](../docs/adr/0011-process-lifetime-and-ordering.md) reverses "the
Perfetto process track is not split", takes the measurement it left open, and
records that the epoch cannot reach a `process` row as a column of its own.
The measurement came back stronger than this section assumed: the trace
processor keys `upid` on the track uuid, so two descriptors on one pid split
whether they share a `start_timestamp_ns`, share a name, or carry none at all.
`start_timestamp_ns` therefore earns its place by stamping a row where its
process started, not by making the split work.
[ADR-0010](../docs/adr/0010-process-identity-cmdline-and-start-marker.md)
gains the per-process descriptor and marker. ADR-0003, ADR-0005, ADR-0015 and
ADR-0016 each state a track key as `(pid, iid)` and become `(process, iid)`.
Section 7 has the order.

**The encoder stops dropping the epoch from its keys.** Every event already
names a `Process`
([ADR-0025](../docs/adr/0025-create-every-process-in-one-place.md)), and
`PerfettoTrackState` throws that half away before filing anything.
`_shared_row` rewrites a `Track` to epoch 1 for `has_track`, `get_track_uuid`,
the counter tracks and the counter group. `get_process_track_uuid`,
`has_process_descriptor` and `has_start_process_marker` key on the bare pid.
Two more pin the epoch without going through `_shared_row`:
`get_process_lifetime_start_ts` reads `Process(pid, 1)`, and
`get_process_track_ranks` filters on `pid_epoch == 1`. Deleting `_shared_row`
and keying those nine on the process is the change. A uuid then belongs to a
process, and the thread, loss and counter tracks parent to the process track
of the process that produced them with no further rule. The span accumulator
already keys this way.

**Each descriptor carries its own process's name, start and rank.**
`_emit_process_descriptor` names the process track from the `Process` itself,
so a process track and its span match as strings. The `pid` field stays the
operating system's pid, the field a query filters on. `start_timestamp_ns`
comes from that process's own span rather than the pid's first.
`get_process_track_ranks` returns a rank per process, ordered by start
timestamp and then by process, so a successor sorts on its own first
observation.

**One spelling of the name.** `perfetto_format` builds it from a pid and
`perfetto_process_lifetime` from a `Process`. Both become one
`process_track_name` in `perfetto_process_lifetime`, which `perfetto_format`
already imports from. The two strings have to be equal for a process track and
its span to name one process, and `docs/perfetto-sql.md` joins on that
equality.

**The command line needs no change of its own.** `set_cmdline` and
`get_cmdline` key on the `Process` already (ADR-0025). The wrong command line
reaches the trace because the successor's descriptor is never emitted, and
emitting it is the whole of story 4.

**A run with no reuse stays byte-identical.** With one process per pid,
`_shared_row` is the identity on every key it rewrites, the two sites that pin
epoch 1 pin the only epoch there is, uuid allocation order is unchanged, and
`Process 12345#2` is `Process 12345` on the first epoch.

**What the rest of the header constrains.**

- ADR-0002: uuids stay allocated in first-reference order and every child
  names its parent, which the split keeps by allocating one more parent.
- ADR-0003: the counter group is non-OS-scoped and hangs off a process track,
  so a second group hangs off the second process track.
- ADR-0005: a shared y axis needs a shared parent, and after the split two
  processes on one pid no longer have one.
- ADR-0015: the loss row holds one span per poll and has to stay flat.
  Splitting it leaves each poll on the row of the process it polled, so no
  span holds across a handover.
- ADR-0024: an event names its `Track` and the encoder derives the rest, which
  is the rule `_shared_row` breaks.
- ADR-0025: the monitor decided which process a record belongs to before the
  record reached an exporter, so nothing here resolves an epoch.

**`combine` is unaffected.** Offline conversion creates no process and builds
every pid a first process (`trace_converter`, ADR-0024), so every key reduces
to what it is today.

The rejected alternatives live in ADR-0011: resolving the epoch inside the
encoder, keeping one process track and annotating the counter with the epoch,
writing a synthetic pid for a successor, and fixing the command line alone.

## 5. Seams and testing decisions

- **Seam:** a trace-processor SQL assertion over `process`, `thread` and
  `counter_track`, and the golden trace in
  `tests/fixtures/monitored_run_perfetto_trace.txt`. Per CONVENTIONS rule 6
  the SQL asserts what the trace means rather than that the bytes
  round-tripped: a byte assertion passes on two descriptors the trace
  processor merged back into one.
- **New seam needed:** one fixture, and wire-level tests beside it. The
  fixture landed in `tests/exporters/test_perfetto_exporter_integration.py`, a
  run that hands a pid on beside the crossing and liveness ones, needing no
  new helper. Two of this spec's claims reach no SQL column, though:
  `sibling_order_rank` has none at all, and `ProcessDescriptor.cmdline` is not
  surfaced
  ([ADR-0010](../docs/adr/0010-process-identity-cmdline-and-start-marker.md)).
  Those are asserted on the wire, the rank and the stamp in
  `test_perfetto_ordering.py` and the per-process cmdline in
  `test_perfetto_format.py`.
- **What makes a good test here:** query `upid` and assert each process's
  slices and counters hang off its own, and that the two `start_ts` differ.
  Counting rows alone passes on two descriptors the processor collapsed, and
  so does asserting on `pid`, which is equal by construction.
- **Prior art:** `crossing_trace_processor` and `liveness_trace_processor` in
  that file for the fixture shape, and
  `tests/exporters/test_perfetto_ordering.py` for ranks and
  `start_timestamp_ns`.
- **Cases:**
  1. A pid held twice gives two `upid`s, each with its own `start_ts`, its own
     thread row, its own `GC Loss` row and its own counter tracks, with the
     counter values apart.
  2. Each `Start Process` marker, `ProcessDescriptor.cmdline` and
     `description` belongs to the process it is drawn under, and a process
     track's command line equals the one on its own `Processes` span. This
     case lands in the trace-processor fixture and not the golden one:
     `test_monitored_run_trace` builds a registry with no cmdline provider, so
     no packet in the golden trace carries a command line.
  3. Each process track is ranked on its own first observation.
  4. The pin inverts. `TestTwoProcessesOnOnePidShareTheirRows` asserts a
     shared uuid for the thread track, the counter group and a counter; each
     becomes a distinct one, and the loss row joins them. Its
     `test_two_interpreters_are_still_two_rows` control stays as it is.
  5. `tests/fixtures/monitored_run_perfetto_trace.txt` moves, because that run
     hands pid 33512 on. The diff is the feature and gets read, not
     regenerated: no packet is deleted, no packet is modified except in
     `track_uuid`, and every added packet is one of `Process 33512#2`'s
     descriptors or its `Start Process` marker. The `Processes` track is
     allocated last, so its uuid moves too. The diff carries story 6: the pid
     nobody reused is described first, and nothing already numbered moves.
  6. JSONL output is byte-identical, and needs no test to say so.
     `jsonl_exporter` imports nothing from any `perfetto_` module, so the
     change cannot reach it.

Twelve of the thirteen tests these became fail against the encoder as it stood
after the records landed. The thirteenth asserts no non-info stat is raised,
which was true before the split too: a merge raises none, and that is why the
whole seam reads tables rather than the `stats` one.

## 6. Out of scope

- **The epoch in JSONL.** A capture carries no epoch and no exit record, so
  `combine` could not honour one; spec 0059 accepted that divergence and this
  adds nothing to it.
- **Reading a command line for a process gcmon never polled.** psutil has
  nothing to answer with, and
  [ADR-0010](../docs/adr/0010-process-identity-cmdline-and-start-marker.md)
  already accepts that.
- **Applying a rank to a descriptor already emitted.**
  [ADR-0011](../docs/adr/0011-process-lifetime-and-ordering.md) records why
  emission stays idempotent, and splitting the rows does not change it.
- **Merging the two process tracks in the UI under one collapsible parent.**
  Perfetto groups by `upid`, and this spec asks for two.
- **[0027](0027-thread-descriptor-tid-for-interpreter-zero.md).** Interpreter
  zero keeps `tid = pid`, which the measurement in ADR-0011 shows does not
  block the split.

## 7. Further notes

The records move first, in one commit, ahead of any code: ADR-0011 and
ADR-0010 for the two decisions, ADR-0003, ADR-0005, ADR-0015 and ADR-0016 for
the key they each state. CONVENTIONS rule 10 sets that order.

Two pages and the glossary move after the code. `docs/formats.md`'s
command-line section says the two copies on the process track name the first
process to hold the pid. `docs/perfetto-sql.md` warns that a join to `p.pid`
is many-to-one; after the split `p.pid` matches a process row per process, so
the warning becomes "scope by `upid`", and the page gains the key that pairs a
span with its process track: their names are equal. `CONTEXT.md` has no entry
for either container and gains two, a process track and a counter group,
naming each other.

Three things that pass turned up on the way, none of them foreseen here.

The golden fixture numbered its packets, so inserting twelve renumbered the
876 after them and three quarters of the diff was noise. The numbering went in
a commit of its own, ahead of the pages, which is what makes case 5's reading
of that diff worth anything on the next change.

The SQL page carried two false claims about the `process` table that predate
this spec: a process known from liveness alone draws no row of its own and has
no entry there, and every trace carries a synthetic `pid = 0` row besides. The
example query added here inner-joined on the first of them and dropped exactly
those processes until it was rewritten to start from the span.

The CHANGELOG gained two lines rather than one. The rows are a feature; the
process track showing the first process's program was a defect an operator
saw, and `docs/agents/prose.md` routes that to `Bugfixes`, where the sibling
line about the `Processes` span already sat.
