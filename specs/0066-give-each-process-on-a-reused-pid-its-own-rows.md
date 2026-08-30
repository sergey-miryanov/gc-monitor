# 0066: Give each process on a reused pid its own rows

- **Status:** **Pinned**
  (`tests/exporters/test_perfetto_track_state.py::TestTwoProcessesOnOnePidShareTheirRows`)
- **Kind:** feature (enhancement)
- **Effort:** M
- **Origin:** spec 0059, retired, see [RETIRED.md](RETIRED.md); it separated
  the spans and left every other row merged. The measurement in section 4 was
  taken 2026-08-31
- **Respects:**
  [ADR-0002](../docs/adr/0002-perfetto-track-uuid-and-hierarchy.md) (uuid
  allocation and explicit parenting),
  [ADR-0003](../docs/adr/0003-gc-metrics-group-track.md) (the counter group
  hangs off a process track),
  [ADR-0010](../docs/adr/0010-process-identity-cmdline-and-start-marker.md)
  (the cmdline pair and the `Start Process` marker; this spec amends it),
  [ADR-0011](../docs/adr/0011-process-lifetime-and-ordering.md) (the
  `Processes` track, its spans and process ordering; this spec amends it),
  [ADR-0024](../docs/adr/0024-an-event-names-the-track-it-is-drawn-on.md) (an
  event names its `Track` and the encoder derives the rest),
  [ADR-0025](../docs/adr/0025-create-every-process-in-one-place.md) (a
  `Process` is what a record is filed under, and only the monitor creates one)

## 1. Problem statement

An operator opening a trace of a worker tree reads `Process 12345` and
`Process 12345#2` on the `Processes` track and knows the pid was handed on.
Every other row in the trace merges the two.

There is a single `Process 12345` group. Its `Thread 0` row draws both
processes' pauses in one line of slices. Its `G0 collected` counter steps from
one process's values to the other's with nothing marking where. Its
`start_timestamp_ns` is the first process's, so the row is stamped before the
second one existed and the UI orders it on that. The `Start Process` marker
went out for the first process alone.

The command line goes further and names the wrong program. gcmon reads one per
process and puts the right one on each span, but the group carries only the
first process's, on both `ProcessDescriptor.cmdline` and the track's
`description`. One trace then holds two answers: the `#2` span names the
program that process ran, the group above it names its predecessor's, and
nothing says which to believe.

In SQL a per-process aggregate cannot be `GROUP BY upid`, because one `upid`
covers both. It has to join through the `Processes` track and compare each
event's timestamp against a span.

## 2. Solution

Each process gets its own rows. A pid held twice draws two process groups,
`Process 12345` and `Process 12345#2`, each carrying the thread rows, counter
rows, `Start Process` marker, command line and start time of the process it
names. A span on the `Processes` track and the group of the same name describe
the same process, and `GROUP BY upid` is per process.

A run in which no pid was reused writes the trace it writes today, byte for
byte.

## 3. User stories

1. As someone reading a counter for a recycled pid, I want one line per
   process, so that a step in `G0 collected` is a fact about a process rather
   than an artifact of where one ended and the next began.
2. As someone reading a pause row, I want one per process, so that two
   processes' collections are not interleaved into a history neither of them
   had.
3. As someone reading a process group, I want its start time to be its own, so
   that the row is not stamped before the process existed and the UI does not
   order it as though it were.
4. As someone reading a command line, I want the group and the span above it
   to agree, so that a trace holds one answer rather than two.
5. As someone querying a trace from SQL, I want two `upid`s for a pid held
   twice, so that a per-process aggregate is a `GROUP BY` rather than a join
   through the `Processes` track on a timestamp range.
6. As an operator on an ordinary run where no pid was reused, I want my trace
   unchanged, so that this costs me nothing and dates nothing.
7. As a user of `--format jsonl`, I want my output byte-identical, so that a
   Perfetto-only feature stays Perfetto-only.

## 4. Implementation decisions

**The trace processor splits every row a pid shares, given two descriptors.**
[ADR-0011](../docs/adr/0011-process-lifetime-and-ordering.md) leaves this
unmeasured. Measured against the `trace_processor` the suite pins in
`tests.perfetto_prebuilt` (v58.2): two process descriptors on pid 4242 at
different `start_timestamp_ns`, each with a `Thread 0` descriptor carrying the
`(pid, tid)` pair gcmon writes for interpreter zero, and a `G0 collected`
counter track.

```
process    upid=1 pid=4242 start_ts=1000      upid=2 pid=4242 start_ts=9000
thread     utid=1 tid=4242 upid=1             utid=2 tid=4242 upid=2
counter    track=2 "G0 collected" upid=1      track=5 "G0 collected" upid=2
slices     GC Pause A -> utid=1               GC Pause B -> utid=2
counters   ts=1000 value=7   track=2          ts=9000 value=99  track=5
args       upid=1 description="python3 -m a"  upid=2 description="python3 -m b"
stats      no non-info row raised
```

Interpreter zero takes `tid = pid`
([spec 0027](0027-thread-descriptor-tid-for-interpreter-zero.md)), so both
thread descriptors carry `(4242, 4242)`. The trace processor resolves the
thread through its `upid` rather than that pair, so nothing needs a synthetic
pid or a synthetic tid.

**The encoder stops dropping the epoch from its keys.** Every event already
names a `Process`
([ADR-0025](../docs/adr/0025-create-every-process-in-one-place.md)), and
`PerfettoTrackState` throws that half away before filing anything.
`_shared_row` rewrites a `Track` to epoch 1 for `has_track`, `get_track_uuid`,
the counter tracks and the counter group; `get_process_track_uuid`,
`has_process_descriptor` and `has_start_process_marker` key on the bare pid.
Deleting `_shared_row` and keying those seven on the process is the change. A
uuid then belongs to a process, and the thread and counter tracks parent to
the group of the process that produced them with no further rule. The span
accumulator already keys this way.

Rejected: **resolving the epoch inside the encoder**, through an
`epoch_at(pid, ts)` on the track state answered from the span accumulator.
There is nothing to resolve. The monitor decided which process a record
belongs to before the record reached an exporter, so a slice belongs to the
process its `ts_start` was filed under, and a collection that began before a
process exited belongs to that process.

**Each descriptor carries its own process's name, start, rank, command line
and marker.** `_emit_process_descriptor` names the group `Process 12345#2`
from the `Process` itself, so a group and its span match as strings. The `pid`
field stays the operating system's pid, which is what the trace processor
groups on. `start_timestamp_ns` comes from that process's own span rather than
the pid's first, and `get_process_track_ranks` returns a rank per process,
ordered by start timestamp and then pid and epoch, so a successor sorts on its
own first observation. This reverses ADR-0011's "not split, and stamped and
ranked from the first process to hold the pid", which stood only because
nobody had measured.

**A run with no reuse stays byte-identical.** With one process per pid,
`_shared_row` is already the identity on every key it rewrites, uuid
allocation order is unchanged, and `Process 12345#2` is `Process 12345` on the
first epoch.

Rejected: **keeping one group and annotating the counter with the epoch.** It
leaves the counter line stepping between two processes and asks every reader
to de-interleave it, which is the work splitting does once.

Rejected: **fixing the command line alone**, leaving the group merged. It is
the cheap half of story 4 and it strands 1, 2, 3 and 5; the same key does all
of them.

**`combine` is unaffected.** Offline conversion creates no process and builds
every pid a first process
([ADR-0024](../docs/adr/0024-an-event-names-the-track-it-is-drawn-on.md)), so
every key reduces to what it is today.

## 5. Seams and testing decisions

- **Seam:** a trace-processor SQL assertion over `process`, `thread` and
  `counter_track`. It is the highest seam that can observe the change, and per
  CONVENTIONS rule 6 it asserts what the trace means rather than that the
  bytes round-tripped: a byte assertion passes on two descriptors the trace
  processor merged back into one.
- **New seam needed:** one fixture.
  `tests/exporters/test_perfetto_exporter_integration.py` builds a trace per
  shape it needs to observe and drives the real processor over it; this wants
  one more, a run that hands a pid on, beside the crossing and liveness ones.
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
     thread track and its own counter tracks, with the counter values apart.
  2. Each `Start Process` marker, `ProcessDescriptor.cmdline` and
     `description` belongs to the process it is drawn under, and a group's
     command line equals the one on its own `Processes` span.
  3. The pin inverts. `TestTwoProcessesOnOnePidShareTheirRows` asserts a
     shared uuid for the thread track, the counter group and a counter; each
     becomes a distinct one, in the commit that splits them. Its
     `test_two_interpreters_are_still_two_rows` control stays as it is.
  4. Regression guard: a run with no reuse writes a byte-identical trace, and
     JSONL output is byte-identical with the change and without.
  5. `tests/fixtures/monitored_run_perfetto_trace.txt` moves, because that run
     hands pid 33512 on. The diff is the feature and gets read, not
     regenerated.

## 6. Out of scope

- **The epoch in JSONL.** A capture carries no epoch and no exit record, so
  `combine` could not honour one; spec 0059 accepted that divergence and this
  adds nothing to it.
- **Reading a command line for a process gcmon never polled.** psutil has
  nothing to answer with, which is what
  [ADR-0010](../docs/adr/0010-process-identity-cmdline-and-start-marker.md)
  already accepts.
- **Applying a rank to a descriptor already emitted.**
  [ADR-0011](../docs/adr/0011-process-lifetime-and-ordering.md) records why
  emission stays idempotent, and splitting the rows does not change it.
- **Merging the two groups in the UI under one collapsible parent.** Perfetto
  groups by `upid`, and this spec asks for two.
- **[0027](0027-thread-descriptor-tid-for-interpreter-zero.md).** Interpreter
  zero keeps `tid = pid`, which section 4 shows does not block the split.

## 7. Further notes

Landing this amends two records.
[ADR-0010](../docs/adr/0010-process-identity-cmdline-and-start-marker.md)
gains the per-process descriptor and marker, where it now carries the
per-process read alone.
[ADR-0011](../docs/adr/0011-process-lifetime-and-ordering.md) loses the
decision that the process track is not split, which section 4 measures, and
its ranking becomes per process.

Two pages move with it: `docs/formats.md`, whose command-line section says the
process track names the first process to hold the pid, and
`docs/perfetto-sql.md`, which tells a reader to join `Processes` slices to
`p.pid` many-to-one. Both become one-to-one, and the CHANGELOG gains a line.
