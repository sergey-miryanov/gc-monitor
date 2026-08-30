# 0059: Say which process held a pid in the trace

- **Status:** Not started
- **Kind:** feature (enhancement)
- **Effort:** M
- **Origin:** design session 2026-08-23 on comparing two tracefiles; a reader
  of a trace cannot make the distinction the live table already makes.
  Measured on a pyperformance trace 2026-08-28, section 1
- **Respects:**
  [ADR-0010](../docs/adr/0010-process-identity-cmdline-and-start-marker.md)
  (cmdline as a debug annotation on the process slice; this spec amends it),
  [ADR-0011](../docs/adr/0011-process-lifetime-and-ordering.md) (the
  `Processes` track, its spans and their clipping; this spec amends it),
  [ADR-0012](../docs/adr/0012-trace-output-formats.md) (a Perfetto-only
  feature is allowed to be Perfetto-only),
  [ADR-0017](../docs/adr/0017-monitor-owns-the-pid-lifecycle.md) (the monitor
  reports which pids are live; this spec amends it)

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

A 4840 s pyperformance run over 1862 processes puts numbers on it. On 133 of
them the `start_timestamp_ns` on the process descriptor falls more than a
second before the process's first event, on 90 more than a minute, and on one
1021 s before it. The clipping then pulls a crossing span's end back, and 133
spans end before the process they name produced anything. Pid 44952 kept its
width instead: a 954 s span over a process whose events cover 0.73 s, so an
operator zooming to that span reads a process with no events and no counters.

The distinction exists only in memory, on `StreamingStats`, and it is gone the
moment the run ends. No file gcmon writes carries it.

## 2. Solution

Each process gets its own slice on the `Processes` track. The first to hold a
pid reads `Process 12345`, as it does today; the second reads
`Process 12345#2`, matching what the statistics table already prints. Each
carries a `pid_epoch` annotation beside the `real_start_ts` and `real_end_ts`
it already has, so a SQL query reads the number without parsing a name.

The epoch gets one definition for the whole run: a `Process` value carries it,
and every record gcmon files, every track it draws and every statistics key it
holds names one of those instead of a pid.

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
5. As an operator on an ordinary run where no pid was reused, I want the trace
   to keep every track, slice and counter it had, so that this costs me
   nothing beyond the field it adds.
6. As a maintainer, I want the epoch to have one definition, so that the table
   and the trace cannot disagree about which process a record belonged to.
7. As a user of `--format jsonl`, I want my output byte-identical, so that a
   Perfetto-only feature stays Perfetto-only.

## 4. Implementation decisions

**The epoch is minted once, in `monitoring`, and a record is filed under the
process rather than the pid.** A registry is the only thing that mints a
`Process`, the monitor is the only caller that may, and a `Process` is what
`Track`, the exporter protocol and every statistics key carry where they
carried a pid. [ADR-0025](../docs/adr/0025-mint-every-process-in-one-place.md)
owns that seam.

That reverses the two decisions this spec opened with: deriving the epoch in
the encoder from the liveness reports, and rejecting a record that carries its
own epoch. The rejection missed a third shape. A record's epoch is implied by
which process produced it, so naming that process costs one field on a `Track`
that already exists rather than one on every record, and the formats that
ignore the epoch never see it.

Three things the liveness derivation could not do:

- **The control plane.** A client names an operating-system pid and gcmon has
  to draw its instant on a process. An instant stamped before the pid was
  retired belongs to the process that has gone, so answering needs the
  retirements, which a set of live pids does not carry.
- **A suppressed pid.** The control server can stop a pid being polled. It is
  then absent from one liveness report and present in a later one, which is
  the shape the derivation reads as a new process. The monitor can tell the
  two apart because the suppression is its own.
- **The command line.** Read once per pid at the first flush, it names the
  first process's program on every later span of that pid, and by then that
  process is usually gone
  ([ADR-0010](../docs/adr/0010-process-identity-cmdline-and-start-marker.md)).

It also removes the cost this spec priced in. One minter leaves no second
counter to drift, so the epoch is not something the table and the trace can
disagree about and no cross-check has to stop them.

**The span key widens from `pid` to the process.** The span accumulator keys
on `Process`, and the sweep sorts on start, longer span first, then the
process, which orders by pid and then epoch. The laminar clipping in ADR-0011
is unchanged in kind: it takes a list of spans and makes them disjoint or
nested, and it does not care that two of them now carry one pid.

**The slice name follows the `--stats` label.** Epoch 1 stays `Process 12345`
and later epochs take the `#N` suffix, off the same `Process` the table reads,
so the first process on every pid is unchanged and the rule has one home.

**The END repeats the suffixed name.** The trace processor matches a named END
to the BEGIN carrying that name, so two spans on one pid sharing a name would
close each other.

**The Perfetto process track is not split.** Two `ProcessDescriptor` messages
carrying one pid may collapse to a single `upid` in the trace processor;
nothing here has verified which, and the gain would be a UI nicety. The
encoder drops the epoch from the key every row is filed under, so two
processes on one pid share a process descriptor, a thread track, a counter
group, its counters and one `Start Process` marker, and a reader attributes a
record to a process by which span its timestamp falls in.
[0066](0066-give-each-process-on-a-reused-pid-its-own-track.md) takes the
measurement and splits them.

**The process descriptor keeps the first epoch on both its fields.**
`start_timestamp_ns` and `sibling_order_rank` come from the first process to
hold the pid. The track is not split, so it covers every process that held the
pid: the first of them is when it opens, and ranking on that same timestamp
leaves process order unchanged.

The consequence is that a recycled pid keeps a process track stamped before
its later occupant existed. The `Processes` track carries that distinction
instead.

**A record older than a retirement belongs to the process that retired.** A
pid pruned from the tree loses its read cursor
([ADR-0017](../docs/adr/0017-monitor-owns-the-pid-lifecycle.md)), so whatever
claims that pid next re-reads the ring and hands gcmon records its predecessor
produced. Each record is filed under the process that held the pid when the
record was made rather than under the one just polled, and a settled ring
turns away a record it has already counted
([ADR-0016](../docs/adr/0016-the-ring-is-the-statistics-unit.md)).

**Liveness is stamped when the reads that proved it returned.** A tick polls
its pids in sequence, so a process polled second is observed later than one
polled first, and the report carries the instant the tick's last read
returned. Every process alive in one tick then shares an end, which the sweep
nests rather than clips.

**Byte identity is not kept, and story 5 is met without it.** Two things move
in every trace. Every slice on the `Processes` track carries `pid_epoch`
whether or not a pid was reused, because story 3 wants the number without a
name to parse and a field present only sometimes is one a query handles twice.
And the liveness stamp above moves the end of each span. Nothing else changes:
the tracks, slices, counters and descriptors of a run with no reuse are what
they were.

**JSONL is left alone.** A capture carries no epoch and no exit record, so
`combine` cannot recover one, and a trace built offline will collapse what a
live trace now separates. That divergence is accepted here and named in
section 6.

## 5. Seams and testing decisions

- **Seam:** a trace-processor SQL assertion over the `Processes` track. It is
  the highest seam that can observe the change, and per CONVENTIONS rule 6 it
  asserts what the trace means rather than that the bytes round-tripped.
- **New seam needed:** none for the trace. The registry is a unit with a
  boundary of its own, so what a pid handed on means is asserted there rather
  than through a whole run.
- **What makes a good test here:** query the slices on the `Processes` track
  and assert two rows for the recycled pid, with the annotations that separate
  them. A byte assertion on the encoder would pass on a slice nested under the
  wrong parent.
- **Prior art:** the existing `Processes`-track assertions, and the laminar
  clipping tests that already build crossing spans by hand.
- **Cases:**
  1. Two processes on one pid produce two slices, named apart, each carrying
     its own `pid_epoch`, `real_start_ts` and `real_end_ts`.
  2. Each of them carries the command line of the program it was running, not
     its predecessor's.
  3. Evidence that reaches gcmon after the pid was retired belongs to the
     process that produced it, and a ring that has settled counts it once.
  4. Two spans on one pid that would cross are clipped the way two spans on
     different pids are.
  5. Regression guard: a whole run over a recycled pid is pinned packet by
     packet, and JSONL output is byte-identical with the change and without.

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
- **Reading a command line for a process gcmon never polled.** psutil has
  nothing to answer with once the process is gone, which is what ADR-0010
  already accepts.

## 7. Further notes

Landing this amends four records.
[ADR-0011](../docs/adr/0011-process-lifetime-and-ordering.md) gets one
`TYPE_SLICE_BEGIN`/`TYPE_SLICE_END` pair per process rather than per pid, the
widened span key, and the liveness stamp moving to the end of the poll phase.
[ADR-0010](../docs/adr/0010-process-identity-cmdline-and-start-marker.md)
reads a command line once per process, at discovery, rather than once per pid
at the first flush, which is where its "collection is the exporter's job"
decision goes. [ADR-0016](../docs/adr/0016-the-ring-is-the-statistics-unit.md)
and [ADR-0017](../docs/adr/0017-monitor-owns-the-pid-lifecycle.md) name the
process where they named a pid and an epoch beside it. Amend the records
rather than writing new ones, per CONVENTIONS rule 4: they are the same
decisions, revised.

It graduates one: the registry, the `Process` value and where each may be
reached from are a durable design question and become
[ADR-0025](../docs/adr/0025-mint-every-process-in-one-place.md).

Spec 0061 depends on this. Without it, a table built from a tracefile can
report a ring but not which process held it, and the offline table would drop
a distinction the live one makes.
[0066](0066-give-each-process-on-a-reused-pid-its-own-track.md) is what this
one leaves merged.
