# 0061: Build the statistics table from a tracefile

- **Status:** Not started
- **Kind:** feature (enhancement)
- **Effort:** L
- **Origin:** design session 2026-08-23 on comparing two tracefiles; the
  comparison in spec 0063 needs a table it can build twice
- **Respects:**
  [ADR-0001](../docs/adr/0001-hand-rolled-perfetto-protobuf-encoder.md) (the
  encoder is hand-rolled and `perfetto` stays out of the *monitoring* runtime;
  this spec adds it as an extra on the analysis path and says why that is not
  the same decision),
  [ADR-0009](../docs/adr/0009-nanoseconds-canonical-time-unit.md) (nanoseconds
  inside gcmon),
  [ADR-0016](../docs/adr/0016-the-ring-is-the-statistics-unit.md) (the ring is
  the unit),
  [ADR-0018](../docs/adr/0018-stats-requires-a-view-and-keeps-no-bare-alias.md)
  (the view words come from one enum)

## 1. Problem statement

The statistics table exists only while gcmon is running. An operator who ran a
capture last week, or who was handed a tracefile by someone else, has the
whole of the data in the file and no way to see the table it would have
printed. The numbers are all there: every pause is a slice, every loss window
is a slice with the counts on it, and the cumulative counters ride the counter
tracks.

Reopening the trace in the Perfetto UI answers a different question. It shows
where the pauses fell; it does not report a p99, a coverage figure or a scale
factor, and computing one by hand from the slice table is the work
`docs/perfetto-sql.md` exists to make possible rather than pleasant.

The gap also blocks anything that wants to compare two runs, because there is
nothing to compare.

## 2. Solution

`gcmon report <trace>` prints the statistics table for a capture that has
already finished. It is the table `--stats` prints, from a file instead of
from a live session, with the same columns, the same footer notes and the same
view words.

Two rows the live table carries are absent, and the footer says so rather than
printing a blank. `Read Time` measures how long gcmon took to read the target
and is never written to a trace. The note counting rings that got no row
records a limit that applied while the session ran and is not a property of
the file.

## 3. User stories

1. As an operator handed a tracefile, I want the statistics table for it, so
   that I can read a p99 and a coverage figure without writing SQL.
2. As an operator who ran a capture without `--stats`, I want the table
   afterwards, so that forgetting a flag does not cost me a run.
3. As an operator reading an offline table, I want to know which rows are
   missing and why, so that I do not think a run had no read cost.
4. As an operator who used `--stats=full` live, I want `report --view full` to
   print the same blocks in the same order, so that the two are comparable by
   eye and by diff.
5. As someone installing gcmon next to a production process, I want the
   analysis dependency to be optional, so that the monitored application does
   not inherit it.
6. As a maintainer, I want one path from records to statistics, so that the
   live table and the offline table cannot drift apart the way the live and
   offline trace paths already did twice.
7. As a maintainer, I want the reader replaceable, so that a hand-rolled
   decoder can take over without anything else in the codebase noticing.

## 4. Implementation decisions

**The reader is a protocol, with one implementation over `TraceProcessor`.**
The seam is a path in, and records plus process metadata out. `perfetto` and
`protobuf` become an optional extra, and importing the reader without it fails
with a message naming the extra.

ADR-0001 keeps `perfetto` out of the runtime because gcmon is installed beside
the process it watches, so every runtime dependency is one the target
inherits. `report` is not on that path: it runs after the fact, on a different
machine as often as not. The extra is the shape that keeps ADR-0001's reason
intact while letting this exist.

Rejected for now, and worth naming because it is close: a hand-rolled decoder
mirroring the hand-rolled encoder, sharing the field numbers in
`perfetto_proto` and needing no dependency at all. It reads only traces gcmon
wrote, interning was declined in spec 0056 so there is no incremental state to
rebuild, and `zlib` undoes ADR-0022's batches. Against it: `perfetto` works
today, and the decoder is a second place that has to track the wire format.
The protocol above is what makes the choice reversible, and the round-trip
test in section 5 is what would validate the replacement.

**The reader returns `dict[int, list[TItem]]`,** the type `read_jsonl` already
returns. Folding a capture's records back into a `StreamingStats` becomes the
single path from records to a table. `pyperf/hook.py` carried a private
`_replay` doing exactly that; spec 0064 left it with no caller, since the hook
marks the benchmark and computes nothing, and it was deleted rather than left
to rot. Recover it from git history: it and its tests are the commit before
"Drop the hook's replay of a capture". JSONL and a tracefile then reach the
table through the same code, and the zero-duration rule in
`streaming_stats._record` is applied once rather than reimplemented.

That costs the reader some fabrication. A sub-phase has its own slice, so its
duration is in the file directly, and reassembling the timestamp pair the
writer started from only to have it differenced again is round-tripping
through a shape the trace does not use. The alternative is a second entry into
`StreamingStats` and a second copy of the zero-duration rule, which is the
drift specs 0026 and 0037 exist to prevent. The fabrication is the cheaper
mistake.

**Two joins the reader has to make.** The cumulative `duration` that
`observe_cumulative` needs is a sample on the `G{gen} duration` counter track,
not an argument on the pause slice, and the sub-phase slices are separate
slices inside the pause they belong to. Both are joined by timestamp on one
track.

**The command is `report`, and its view flag is `--view`.** `--stats` stays
the flag on `monitor` and `run`, where a table is one thing a run can also do;
on `report` the table is the output. The words are the same words, read from
`StatsView`, so ADR-0018's single source still holds.

**Absent rows are absent.** No `Read Time` row, no rings-got-no-row note, and
one footer note saying the source was a file. This follows spec 0020's rule
for metadata that cannot be known: absent rather than guessed.

## 5. Seams and testing decisions

- **Seam:** the reader protocol, and `stats_output.print_stats` above it. The
  reader is unit-testable against a trace written by the encoder in the same
  test, with no subprocess and no fixture file to keep current.
- **New seam needed:** the reader protocol itself. Nothing existing reaches
  from a path to a `TItem`, and `read_jsonl` is the shape to model it on.
- **What makes a good test here:** a round trip. Write a known record set
  through the encoder, read it back, and assert the records match. Per
  CONVENTIONS rule 6 this is exactly the test that a round trip alone does not
  give, so it is paired with case 2 below, which compares against a table
  built without going through the trace at all.
- **Prior art:** `tests/exporters/` for the encoder side, and the existing
  statistics tests for the table.
- **Cases:**
  1. A trace written from a known record set reads back as that record set,
     loss records and cumulative counters included.
  2. The offline table equals the live one: run a session with `--stats=full`
     writing a trace, then `report --view full` over that trace, and assert
     the tables are identical apart from the `Read Time` row and the footer
     note. This is the guard the whole spec rests on.
  3. A trace from a run that lost records reports the same `Cov` and `F` as
     the session did.
  4. Importing the reader without the extra fails with a message naming the
     extra, and every other command still runs.

## 6. Out of scope

- **The workload view.** `report` ships with `total` and `full`; spec 0062
  adds the level between them.
- **Comparing two traces.** Spec 0063.
- **Reading a trace gcmon did not write.** The reader is specified against
  gcmon's own output, and nothing checks a foreign trace for the tracks it
  expects.
- **Whole-file compressed input.** ADR-0022 compresses each batch inside the
  file, which the trace processor already undoes. A `.pftrace.zst` wrapper is
  a different thing and is not read.
- **Reading JSONL through `report`.** `combine` already turns captures into a
  trace, and a second path into the same table earns nothing.
- **A hand-rolled decoder**, for the reasons in section 4. The protocol is
  what keeps it available.

## 7. Further notes

Landing this earns an ADR: the reader is a protocol, with `TraceProcessor`
behind it for now. It partially reverses ADR-0001 on the analysis path, a
future reader will ask why, and both the reason and the rejected alternative
belong in the record.

Depends on spec 0059, without which the offline table cannot say which process
held a pid and would drop a distinction the live table makes. Spec 0063
depends on this one.

Nothing here touches the hook. Spec 0064 already took the replay out of
`pyperf/hook.py`, so this spec writes the shared implementation rather than
moving one.
