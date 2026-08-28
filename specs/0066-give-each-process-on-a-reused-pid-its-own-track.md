# 0066: Give each process on a reused pid its own track

- **Status:** Landed 2026-08-28, to retire with
  [0059](0059-say-which-process-held-a-pid-in-the-trace.md)
- **Kind:** feature (enhancement)
- **Effort:** M
- **Origin:** measured 2026-08-28 while landing
  [0059](0059-say-which-process-held-a-pid-in-the-trace.md), which separated
  the spans and left every other row merged
- **Respects:**
  [ADR-0002](../docs/adr/0002-perfetto-track-uuid-and-hierarchy.md) (uuid
  allocation and parenting),
  [ADR-0003](../docs/adr/0003-gc-metrics-group-track.md) (the counter group
  under a process track),
  [ADR-0010](../docs/adr/0010-process-identity-cmdline-and-start-marker.md)
  (cmdline and the `Start Process` marker; this spec amends it),
  [ADR-0011](../docs/adr/0011-process-lifetime-and-ordering.md) (the
  `Processes` track and process ordering; this spec amends it),
  [ADR-0012](../docs/adr/0012-trace-output-formats.md) (a Perfetto-only
  feature is allowed to be Perfetto-only),
  [ADR-0024](../docs/adr/0024-an-event-names-the-track-it-is-drawn-on.md) (an
  event names its `Track`, and the encoder derives the rest)

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
reads a pid's command line once per trace, during the first process's life,
and puts that string on **every** span of that pid. On a tree where the pid is
handed to a different program, the `#2` span carries a `cmdline` annotation
naming a program that process never ran, and nothing in the trace says so.
Absent would be better than wrong.

0059 measured the scale of reuse on a 4840 s pyperformance run over 1862
processes; section 1 of that spec carries the numbers.

## 2. Solution

Each process gets its own row. A pid held twice draws two process groups,
`Process 12345` and `Process 12345#2`, each with the thread rows, counter rows
and `Start Process` marker of the process it names, its own start time, and
its own command line read while that process was running. A span on the
`Processes` track and the group of the same name describe the same process.

A run in which no pid was reused writes the trace it writes today, byte for
byte.

## 3. User stories

1. As someone reading a counter for a recycled pid, I want one line per
   process, so that a step in `heap_size` is a fact about a process rather
   than an artifact of where one ended and the next began.
2. As someone reading a process group, I want its start time to be its own, so
   that the row is not stamped before the process existed and the UI does not
   sort it as though it were.
3. As someone reading a `cmdline` annotation, I want it to name the program
   that process ran, so that a wrong command line is not attributed to a
   process silently.
4. As someone querying a trace from SQL, I want two `upid`s for a pid held
   twice, so that a per-process aggregate is a `GROUP BY upid` rather than a
   join through the `Processes` track.
5. As an operator on an ordinary run where no pid was reused, I want my trace
   unchanged, so that this costs me nothing.
6. As a user of `--format jsonl`, I want my output byte-identical, so that a
   Perfetto-only feature stays Perfetto-only.
7. As a maintainer, I want the epoch resolved in one place, so that a row and
   the span of the same name cannot come from different counters.

## 4. Implementation decisions

**Two `ProcessDescriptor`s carrying one pid give two `upid`s.** This is the
measurement [0059](0059-say-which-process-held-a-pid-in-the-trace.md) section
4 named as what would settle splitting, taken against the `trace_processor`
the suite pins in `tests/perfetto_prebuilt.py` (v58.2):

```
two descriptors, pid 4242, start_timestamp_ns 1000 and 9000
  upid=1 pid=4242 start_ts=1000     slice A -> upid 1
  upid=2 pid=4242 start_ts=9000     slice B -> upid 2
  stats: no non-info row raised
```

Each keeps its own `start_ts`, and a slice routes to the descriptor whose
track uuid it names. Nothing needs a synthetic pid.

**Every per-pid map in `PerfettoTrackState` keys on `(pid, pid_epoch)`**:
`_pids`, `_pid_uuids`, `_track_uuids`, `_counter_tracks`,
`_counter_group_uuids`, `_start_process_marker_emitted` and `_cmdlines`. The
span accumulator already does. A uuid is then per process, so the thread and
counter tracks parent to the group of the process that produced them with no
further rule.

**The epoch is resolved in the encoder, not carried on `Track`.**
[ADR-0024](../docs/adr/0024-an-event-names-the-track-it-is-drawn-on.md) has an
event name the track it is drawn on, and `ProcessTrack` / `InterpreterTrack` /
`LossTrack` are the model the JSONL exporter reads too. An epoch field there
would widen a shape two formats share, and `combine` would have to invent a
value for it from a capture that carries none. `PerfettoTrackState` answers
instead:

```python
def epoch_at(self, pid: int, ts: int) -> int:
    """Which process holding *pid* was running at *ts*. Read-only."""
```

`convert_trace_events_to_perfetto` already folds every event of a batch into
the span state in a pre-pass before it emits anything, so by the time a
descriptor is written every timestamp in the batch sits inside a recorded
span. Spans on one pid are disjoint, which
[0059](0059-say-which-process-held-a-pid-in-the-trace.md) made an invariant,
so the answer is unambiguous.

**A slice belongs to the epoch of its `ts_start`**, and so does the span it
widens. A collection that began before a process exited belongs to that
process, so both ends go to it: folding them one at a time would split one
slice across two processes and draw a span for the second out of the tail
alone.

**Each descriptor carries its own epoch's start and rank.** Ranking becomes
over processes rather than pids, so the second process sorts on its own first
observation instead of inheriting its predecessor's place. This reverses
0059's "the process descriptor keeps the first epoch on both its fields",
which existed only because the track was not split.

**The descriptor name takes the same suffix as the span**, from
`gcmon.support.pid_epoch.epoch_suffix`, so an operator matching a group to a
span matches identical strings.

**A command line is read once per process, not once per trace.**
`ProtobufEventEncoder._cmdline_read` keys on `(pid, pid_epoch)`.
[ADR-0010](../docs/adr/0010-process-identity-cmdline-and-start-marker.md)'s
rule is unchanged in kind, whatever holds the pid at capture time; what
changes is that capture time happens once per process. A process gcmon never
reached in life still gets no command line, and now that is the only way to
get none.

Rejected: **keeping one group and annotating the counter with the epoch.** It
leaves the counter line stepping between two processes and asks every reader
to de-interleave it, which is the work splitting does once.

Rejected: **fixing the command line alone**, leaving the group merged. It is
the cheap half and it strands the other three stories; the same `(pid, epoch)`
key does both.

**`combine` is unaffected.** An offline conversion reports no liveness, so
every pid has one epoch and every key reduces to what it is today.

## 5. Seams and testing decisions

- **Seam:** a trace-processor SQL assertion over `process`, `thread` and
  `counter_track`. It is the highest seam that can observe the change, and per
  CONVENTIONS rule 6 it asserts what the trace means rather than that the
  bytes round-tripped. A byte assertion would pass on two descriptors the
  trace processor had merged.
- **New seam needed:** none.
  `tests/exporters/test_perfetto_exporter_integration.py` already drives the
  real `trace_processor`, and its `reused_pid_trace_processor` fixture already
  builds a run over a handed-on pid.
- **What makes a good test here:** query `upid` and assert each process's
  slices and counters hang off its own, and that `start_ts` differs. Counting
  rows alone would pass on two descriptors the processor collapsed into one.
- **Prior art:** `TestReusedPidSpans` in
  `tests/exporters/test_perfetto_exporter_integration.py`, and
  `tests/exporters/test_perfetto_ordering.py` for ranks and
  `start_timestamp_ns`.
- **Cases:**
  1. A pid held twice gives two `upid`s, each with its own `start_ts`, its own
     thread track and its own counter tracks.
  2. Each `Start Process` marker and each `cmdline` annotation belongs to the
     process it is drawn under, with the second process's command line read
     while it was running.
  3. Regression guard: a run with no reuse writes a byte-identical trace, and
     JSONL output is byte-identical under either.
  4. A slice whose ends straddle a handover is drawn under the process its
     `ts_start` falls in.

## 6. Out of scope

- **The epoch in JSONL**, for the reason
  [0059](0059-say-which-process-held-a-pid-in-the-trace.md) section 6 gives: a
  capture carries no epoch and no exit record, so `combine` cannot recover
  one.
- **Reading a command line for a process that has already exited.** psutil has
  nothing to answer with, which is what
  [ADR-0010](../docs/adr/0010-process-identity-cmdline-and-start-marker.md)
  already accepts.
- **Applying a rank to a descriptor already emitted.**
  [ADR-0011](../docs/adr/0011-process-lifetime-and-ordering.md) records why
  emission stays idempotent, and splitting the track does not change it.
- **Merging the two groups in the UI under one collapsible parent.** Perfetto
  groups by `upid`, and two of them is the point.

## 7. Further notes

Landing this amends two records. `ADR-0010` gains the per-process command line
and marker. `ADR-0011` loses the sentence saying two `ProcessDescriptor`s
carrying one pid may collapse to a single `upid`, which the measurement in
section 4 answers, and its ranking decision becomes per process.

It also corrects [0059](0059-say-which-process-held-a-pid-in-the-trace.md) in
three places: the "not split" decision, the descriptor keeping the first epoch
on both fields, and the out-of-scope bullet for a command line per epoch. Per
CONVENTIONS rule on retiring, 0059 is corrected before it is retired rather
than after.
