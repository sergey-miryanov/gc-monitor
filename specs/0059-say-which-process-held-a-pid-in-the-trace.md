# 0059: Say which process held a pid in the trace

- **Status:** Not started
- **Kind:** feature (enhancement)
- **Effort:** M
- **Origin:** design session 2026-08-23 on comparing two tracefiles; a reader
  of a trace cannot make the distinction the live table already makes
- **Respects:**
  [ADR-0010](../docs/adr/0010-process-identity-cmdline-and-start-marker.md)
  (cmdline as a debug annotation on the process slice),
  [ADR-0011](../docs/adr/0011-process-lifetime-and-ordering.md) (the
  `Processes` track, its spans and their clipping; this spec amends it),
  [ADR-0012](../docs/adr/0012-trace-output-formats.md) (a Perfetto-only
  feature is allowed to be Perfetto-only),
  [ADR-0017](../docs/adr/0017-monitor-owns-the-pid-lifecycle.md) (the monitor
  reports which pids are live)

## 1. Problem statement

An operator watching a tree of short-lived workers runs `--stats=full` and
reads `12345:0` and `12345:0#2`: two processes held that pid, and their
counts, their coverage and their history are kept apart. The trace of that
same run says `12345` once.

Every record from both processes lands on one process track, and the
`Processes` track draws a single span running from the first observation of
the first process to the last observation of the second. Someone opening the
file months later cannot tell a recycled pid from one long-lived process, and
the span they read covers an interval in which the process they think they are
looking at did not exist.

The distinction exists only in memory, on `StreamingStats`, and it is gone the
moment the run ends. No file gcmon writes carries it.

## 2. Solution

Each process gets its own slice on the `Processes` track. The first to hold a
pid reads `Process 12345`, as it does today; the second reads
`Process 12345#2`, matching what the statistics table already prints. Each
carries a `pid_epoch` annotation beside the `real_start_ts` and `real_end_ts`
it already has, so a SQL query reads the number without parsing a name.

A run in which no pid was reused writes the trace it writes today, byte for
byte.

## 3. User stories

1. As someone opening a trace from a run over short-lived workers, I want each
   process to have its own slice, so that I can see that a pid was reused
   rather than believe one process ran the whole time.
2. As someone reading a process span, I want it to cover only the process it
   names, so that `real_start_ts` and `real_end_ts` bound something real.
3. As someone querying a trace from SQL, I want the epoch as an annotation
   rather than a suffix on a name, so that I can filter on it without a string
   parse.
4. As an operator comparing the `--stats` table against the trace of the same
   run, I want the two to label a process the same way, so that a block and a
   slice can be matched by eye.
5. As an operator on an ordinary run where no pid was reused, I want my trace
   unchanged, so that this costs me nothing and dates nothing.
6. As a maintainer, I want the epoch to have one definition, so that the table
   and the trace cannot disagree about which process a record belonged to.
7. As a user of `--format jsonl`, I want my output byte-identical, so that a
   Perfetto-only feature stays Perfetto-only.

## 4. Implementation decisions

**The span key widens from `pid` to `(pid, pid_epoch)`.**
`PerfettoTrackState.update_process_lifetime` keys on the pair, and
`perfetto_process_lifetime` sorts, clips and emits on it. The laminar clipping
in ADR-0011 is unchanged in kind: it takes a list of spans and makes them
disjoint or strictly nested, and it does not care that two of them now carry
the same pid.

**The encoder derives the epoch from the liveness reports it already gets.**
`EventsExporter.add_process_liveness` hands the encoder the set of live pids
once per tick. A pid absent from one report and present in a later one is a
new process, which is the same rule `StreamingStats` applies when it advances
the epoch on the exit gcmon saw. Nothing new is plumbed through, and no event
has to grow a field.

Rejected: stamping every record with its epoch at the monitor. It puts the
number on the one path where it is never needed, since a record's epoch is
implied by when it arrived, and it would widen the exporter protocol for every
format including the two that will ignore it.

**The cost of that choice, and what pins it.** The epoch is then counted in
two places, on `StreamingStats` and on the encoder, from the same evidence.
They can drift. The test that stops them is a single run over a recycled pid
asserting that the table's `#2` block and the trace's `#2` slice describe the
same process, and it belongs in this spec rather than in a comment.

**The slice name follows `_ring_label`.** Epoch 1 stays `Process 12345` and
later epochs take the `#N` suffix, so the first process on every pid is
unchanged and an ordinary run writes an unchanged trace. The rule lives in one
place and both the table and the trace read it.

**The Perfetto process track is not split.** Two `ProcessDescriptor` messages
carrying one pid may collapse to a single `upid` in the trace processor;
nothing here has verified which, and the gain would be a UI nicety. Records
from both processes continue to share the process track, and a reader
attributes each to a process by which span its timestamp falls in. If someone
later measures what the trace processor does, splitting the track is a
separate spec with an answer to point at.

**JSONL is left alone.** A capture carries no epoch and no exit record, so
`combine` cannot recover one, and a trace built offline will collapse what a
live trace now separates. That divergence is accepted here and named in
section 6.

## 5. Seams and testing decisions

- **Seam:** a trace-processor SQL assertion over the `Processes` track. It is
  the highest seam that can observe the change, and per CONVENTIONS rule 6 it
  asserts what the trace means rather than that the bytes round-tripped.
- **New seam needed:** none for the trace. The cross-check in case 3 needs a
  run that produces both a table and a trace, which the existing integration
  fixtures already do.
- **What makes a good test here:** query the slices on the `Processes` track
  and assert two rows for the recycled pid, with the annotations that separate
  them. A byte assertion on the encoder would pass on a slice nested under the
  wrong parent.
- **Prior art:** the existing `Processes`-track assertions, and the laminar
  clipping tests that already build crossing spans by hand.
- **Cases:**
  1. Two processes on one pid produce two slices, named apart, each carrying
     its own `pid_epoch`, `real_start_ts` and `real_end_ts`.
  2. Regression guard: a run with no reuse writes a byte-identical trace, and
     JSONL output is byte-identical under either.
  3. The table and the trace agree: a run over a recycled pid puts `#2` on the
     same process in both.
  4. Two spans on one pid that would cross are clipped the way two spans on
     different pids are.

## 6. Out of scope

- **The epoch in JSONL.** It would need a field on every record or a new exit
  record, and then `combine` would have to honour it. That is a larger change
  than this one and nothing yet depends on it.
- **`combine`.** A trace built from captures will keep collapsing a recycled
  pid, so a live trace and a combined trace of the same run differ.
  [ADR-0007](../docs/adr/0007-shared-trace-converter-pipeline.md) is what
  keeps that class of divergence small; this one adds to it knowingly rather
  than growing to cover it.
- **Splitting the Perfetto process track**, for the reason in section 4.
- **Reading a command line per epoch.** ADR-0010 already reads whatever holds
  the pid at capture time, and a dead predecessor has none to read.

## 7. Further notes

Landing this amends
[ADR-0011](../docs/adr/0011-process-lifetime-and-ordering.md): the decision
line reading one `TYPE_SLICE_BEGIN`/`TYPE_SLICE_END` pair per pid becomes one
pair per process, and the clipping section gains the widened key. Amend the
record rather than writing a new one, per CONVENTIONS rule 4: it is the same
decision, revised.

Spec 0061 depends on this. Without it, a table built from a tracefile can
report a ring but not which process held it, and the offline table would drop
a distinction the live one makes.
