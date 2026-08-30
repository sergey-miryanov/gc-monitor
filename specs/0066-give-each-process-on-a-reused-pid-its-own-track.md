# 0066: Give each process on a reused pid its own track

- **Status:** Not started
- **Kind:** feature (enhancement)
- **Effort:** M
- **Origin:** measured 2026-08-28 while landing spec 0059, which separated the
  spans and left every other row merged
- **Respects:**
  [ADR-0002](../docs/adr/0002-perfetto-track-uuid-and-hierarchy.md) (uuid
  allocation and parenting),
  [ADR-0003](../docs/adr/0003-gc-metrics-group-track.md) (the counter group
  under a process track),
  [ADR-0010](../docs/adr/0010-process-identity-cmdline-and-start-marker.md)
  (cmdline and the `Start Process` marker; this spec amends it),
  [ADR-0011](../docs/adr/0011-process-lifetime-and-ordering.md) (the
  `Processes` track and process ordering; this spec amends it),
  [ADR-0024](../docs/adr/0024-an-event-names-the-track-it-is-drawn-on.md) (an
  event names its `Track`, and the encoder derives the rest),
  [ADR-0025](../docs/adr/0025-mint-every-process-in-one-place.md) (a `Process`
  is what a record is filed under)

## 1. Problem statement

An operator opening a trace of a worker tree reads `Process 12345` and
`Process 12345#2` on the `Processes` track and knows the pid was handed on.
Every other row still merges the two.

There is one `Process 12345` group. Its `Thread 0` row carries both processes'
pauses in one line of slices, and its `G0 collected` counter steps from one
process's values to the other's with nothing marking where. Its
`start_timestamp_ns` is the first process's, so the row is stamped before the
second one existed, and the UI sorts it on that. The `Start Process` marker
went out for the first process only.

The command line is the one that misleads rather than merely merges. gcmon
reads one per process now and puts the right one on each span, but the group
carries only the first process's, on `ProcessDescriptor.cmdline` and on the
track's `description`. So one trace holds both answers: the `#2` span names
the program that process ran, and the group above it names its predecessor's,
with nothing saying which to believe.

Spec 0059 measured the scale of reuse on a 4840 s pyperformance run over 1862
processes. [RETIRED.md](RETIRED.md) has its row and git has the numbers.

## 2. Solution

Each process gets its own row. A pid held twice draws two process groups,
`Process 12345` and `Process 12345#2`, each with the thread rows, counter
rows, `Start Process` marker and command line of the process it names, and its
own start time. A span on the `Processes` track and the group of the same name
describe the same process.

A run in which no pid was reused writes the trace it writes today, byte for
byte.

## 3. User stories

1. As someone reading a counter for a recycled pid, I want one line per
   process, so that a step in `heap_size` is a fact about a process rather
   than an artifact of where one ended and the next began.
2. As someone reading a process group, I want its start time to be its own, so
   that the row is not stamped before the process existed and the UI does not
   sort it as though it were.
3. As someone reading a command line, I want the group and the span above it
   to agree, so that a trace holds one answer rather than two.
4. As someone querying a trace from SQL, I want two `upid`s for a pid held
   twice, so that a per-process aggregate is a `GROUP BY upid` rather than a
   join through the `Processes` track.
5. As an operator on an ordinary run where no pid was reused, I want my trace
   unchanged, so that this costs me nothing.
6. As a user of `--format jsonl`, I want my output byte-identical, so that a
   Perfetto-only feature stays Perfetto-only.

## 4. Implementation decisions

**Two `ProcessDescriptor`s carrying one pid give two `upid`s.** This is the
measurement spec 0059 named as what would settle splitting, taken against the
`trace_processor` the suite pins in `tests/perfetto_prebuilt.py` (v58.2):

```
two descriptors, pid 4242, start_timestamp_ns 1000 and 9000
  upid=1 pid=4242 start_ts=1000     slice A -> upid 1
  upid=2 pid=4242 start_ts=9000     slice B -> upid 2
  stats: no non-info row raised
```

Each keeps its own `start_ts`, and a slice routes to the descriptor whose
track uuid it names. Nothing needs a synthetic pid.

**The encoder stops dropping the epoch from its keys.** Every event already
names a `Process`
([ADR-0025](../docs/adr/0025-mint-every-process-in-one-place.md)), and
`PerfettoTrackState` throws that half away before filing a uuid, a descriptor,
a marker or a counter track, which is what keeps the rows merged. Deleting
that one step is the change: a uuid becomes per process, and the thread and
counter tracks parent to the group of the process that produced them with no
further rule. The span accumulator already keys this way.

This is where the epoch resolution an earlier draft of this spec proposed goes
instead. There is no `epoch_at(pid, ts)` on the track state and no pre-pass to
make it answerable: the monitor decided which process a record belongs to
before the record ever reached an exporter, and the encoder reads that answer
off the `Track`. A slice therefore belongs to the process its `ts_start` was
filed under, and a collection that began before a process exited belongs to
that process, without the encoder deciding anything.

**Each descriptor carries its own epoch's start, rank, command line and
marker.** Ranking becomes over processes rather than pids, so the second
process sorts on its own first observation instead of inheriting its
predecessor's place, and each group's `ProcessDescriptor.cmdline` and
`description` come off the `Process` the events named. This reverses spec
0059's "the process descriptor keeps the first epoch on both its fields",
which existed only because the track was not split.

**The descriptor name takes the same suffix as the span**, off the same
`Process`, so an operator matching a group to a span matches identical
strings.

Rejected: **keeping one group and annotating the counter with the epoch.** It
leaves the counter line stepping between two processes and asks every reader
to de-interleave it, which is the work splitting does once.

Rejected: **fixing the descriptor's command line alone**, leaving the group
merged. It is the cheap half and it strands the other three stories; the same
key does both.

**`combine` is unaffected.** An offline conversion mints no processes, so
every pid reads as its first and every key reduces to what it is today.

## 5. Seams and testing decisions

- **Seam:** a trace-processor SQL assertion over `process`, `thread` and
  `counter_track`. It is the highest seam that can observe the change, and per
  CONVENTIONS rule 6 it asserts what the trace means rather than that the
  bytes round-tripped. A byte assertion would pass on two descriptors the
  trace processor had merged.
- **New seam needed:** one fixture.
  `tests/exporters/test_perfetto_exporter_integration.py` already drives the
  real `trace_processor` and builds a trace per shape it needs to observe;
  this wants one more, a run over a handed-on pid, alongside the liveness and
  crossing ones.
- **What makes a good test here:** query `upid` and assert each process's
  slices and counters hang off its own, and that `start_ts` differs. Counting
  rows alone would pass on two descriptors the processor collapsed into one.
- **Prior art:** the crossing and liveness fixtures in the same file, and
  `tests/exporters/test_perfetto_ordering.py` for ranks and
  `start_timestamp_ns`.
- **Cases:**
  1. A pid held twice gives two `upid`s, each with its own `start_ts`, its own
     thread track and its own counter tracks.
  2. Each `Start Process` marker, `ProcessDescriptor.cmdline` and
     `description` belongs to the process it is drawn under, and the group's
     command line matches the one on that process's span.
  3. Regression guard: a run with no reuse writes a byte-identical trace, and
     JSONL output is byte-identical under either.
  4. A slice whose ends straddle a handover is drawn under the process its
     `ts_start` was filed under.

## 6. Out of scope

- **The epoch in JSONL**, for the reason spec 0059 gave: a capture carries no
  epoch and no exit record, so `combine` cannot recover one.
- **Reading a command line for a process gcmon never polled.** psutil has
  nothing to answer with, which is what
  [ADR-0010](../docs/adr/0010-process-identity-cmdline-and-start-marker.md)
  already accepts.
- **Applying a rank to a descriptor already emitted.**
  [ADR-0011](../docs/adr/0011-process-lifetime-and-ordering.md) records why
  emission stays idempotent, and splitting the track does not change it.
- **Merging the two groups in the UI under one collapsible parent.** Perfetto
  groups by `upid`, and two of them is the point.

## 7. Further notes

Landing this amends two records.
[ADR-0010](../docs/adr/0010-process-identity-cmdline-and-start-marker.md)
gains the per-process descriptor and marker, where it now has the per-process
read alone. [ADR-0011](../docs/adr/0011-process-lifetime-and-ordering.md)
loses the decision saying the process track is not split, including the
sentence that two `ProcessDescriptor`s carrying one pid may collapse to a
single `upid`, which the measurement in section 4 answers, and its ranking
becomes per process.
