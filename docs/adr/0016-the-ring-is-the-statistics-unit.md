# ADR-0016: Report statistics per ring, and drop the per-process row from the `--stats` table

- **Status:** Accepted
- **Date:** 2026-08-15

## Context

Every interpreter in a target keeps its own collector, its own rings and its own cumulative
counters. The trace side has said so since [ADR-0015](0015-gc-loss-spans-on-their-own-track.md):
records go on a thread track per `(pid, iid)`, loss spans on a `GC Loss` track per `(pid, iid)`,
and [ADR-0003](0003-gc-metrics-group-track.md)'s counter group is per `(pid, iid)` as well. Open
a trace of a process running three interpreters and you see three rows.

The statistics side kept an older key. Sampled durations accumulated per pid, and loss
accumulated per `(pid, gen)`, so two interpreters' gaps landed in one slot with nothing left to
tell them apart. Lifetime totals were the one quantity keyed per `(pid, iid, gen)`, and their
only reader summed the interpreter away before printing.

An operator saw three consequences. A `GC Pause(0)` row reported one blended distribution for
interpreters that may run different workloads, so its `P50` through `P99` described none of
them. `Cov` divided a pid-wide sampled count by a pid-wide lost count, so a busy interpreter
masked a starved one beside it, and the mid-run advisory that fires below 90% stayed silent on
captures worth discarding. The footer's lifetime note read "Since interpreter start" in the
singular over a sum across interpreters that started at different moments, and across processes
when a pid was reused.

Coverage averaged before it is stored cannot be recovered downstream, so the fix has to reach
the key rather than the footer.

## Decision

**The ring is the unit statistics are keyed on and reported for.** `gcmon.stats` keys sampled
metrics, loss and lifetime totals on `(pid, iid, gen)`, the same key `gcmon.loss` already uses
for its accumulators and the same one the exporters already draw. Folding happens when a figure
is read, not when it is recorded, so both a ring's number and a roll-up over rings stay
available.

**The `--stats` table prints two levels: the run, and the ring.** The `Total` block stays, as
the one answer to what a run cost. The per-process block goes: its rows blended interpreters
that the trace keeps apart.

**The first column is headed `PID:IID`, and every ring row carries both parts**, `12345:0` on an
ordinary single-interpreter run as much as on a tree. Dropping the `:0` would leave a header
naming two fields over cells holding one.

**`Total` keeps its percentiles.** They are quantiles of a mixture and the footer says so.

**The bound of 256 counts the interpreters still running**, replacing a bound of 64 processes.
A run holds full sampled state for that many rings. At the same footprint per entry as before,
256 buys the previous 64 processes four interpreters each.

**A ring settles when its process exits, and never before.** gcmon learns which pids are gone on
the tick that lists the target's children, so it settles their rings there. The sample buffers
go back, the slots go back, and the four percentiles left behind cover each of those rings end
to end. A target that spawns and exits keeps a row per process it ran without exhausting the
bound.

**A ring gets its row on its first record and keeps it for the run.** Where there is no slot to
give, the ring gets none and its records go to `Total` alone. gcmon says so twice: in a warning
when it first happens, and in a footer note counting the rings left out. Two cases reach it, 256
interpreters already running, and a pid claimed by a successor process while its predecessor's
row stands. A slot freed later opens no row for either, since a row starting mid-life reads as a
whole one.

**The coverage advisory tests rings.** It names the least covered one, interpreter alongside pid
and generation, and it still fires once per run, latched in `gcmon.monitor`: the remedy it
suggests is `--rate`, which no ring owns. Saying it once is what decides the worst over the
first, since a marginal figure would otherwise stand for the whole capture.

**The lifetime note folds, and says what it folded.** One line per generation whatever the size
of the tree, stating the interpreter and process counts it summed over:

```
2. Since each interpreter started, monitored window included, summed over 3 interpreters
   in 2 processes: Gen0 4820 in 6231.400 ms.
```

**Process-wide quantities stay keyed per process.** `heap_size` has no generation and no thread
affinity ([ADR-0004](0004-toplevel-shared-counters.md)), so its high-water mark is still taken
per pid. The end-of-run summary and the coverage footnote stay run-wide, the scope they were
written for and the scope `Total` reports.

## Consequences

- **Ordinary output changes.** A single-interpreter run's rows go from `12345` to `12345:0`, so
  the documented examples and the row-level assertions in the suite move with it. This is
  user-visible and belongs in the CHANGELOG.
- **The advisory fires on runs that are silent today.** A starved interpreter beside a busy one
  now trips the 90% floor on its own figure. That is the defect being fixed, and it makes the
  warning noisier on trees that were this incomplete before.
- **`Total` holds the table's only blended percentile.** Every other row describes one
  distribution.
- **Footprint scales with interpreters running at once.** A process creating many interpreters
  consumes ring slots that a process-keyed bound gave it for free. A tree wide enough to exhaust
  the bound degrades by leaving its later rings out of the table.
- **Every printed row covers one process's ring over one unbroken stretch**, so a row's `Count`,
  its `Sum` and its percentiles always describe the same records. A settled ring cannot take
  more values: `Stats.materialize` raises on one that tries.
- **The rows can be short of the run**, which the footer note states as a count. `Total` is fed
  once per record whatever the table holds, so the run's cost stays whole where its detail does
  not.
- **`Total` is still a separate accumulator**, fed once per record, so it does not depend on how
  many rings the table can hold.
- **Reading a capture back from JSONL gives the same numbers as reading it live**, because the
  replay path keys loss per ring too. A key without an iid could not.
- **A reused pid still corrupts the lifetime fold.** The new key does not fix it: a successor
  process's much smaller cumulative counters overwrite its predecessor's, so a folded lifetime
  total can decrease mid-run. ADR-0015 already accepted the related hazard on the monitor side;
  closing this one needs an epoch on the pid, and is specified separately. The sampled table is
  out of its reach, at the price of printing no row for the successor.
- **A benchmark's metadata keeps its released key names.** They are flat, run-wide scalars,
  which is the scope a benchmark wants and the scope `Total` reports. Per-ring keys would embed
  pids that differ every run.

## Alternatives considered

- **Keep the process rows and add ring rows beneath them**, printed only where a process ran
  more than one interpreter. Rejected: ordinary output stays untouched, but the blended process
  row stays too, and the table's depth then depends on the target's shape.
- **A separate `IID` column beside `PID`.** Machine-readable and uniform, and it widens every
  table by a column reading `0` on most runs. The compound cell carries both fields in the width
  already there.
- **Leave the arithmetic alone and fix only the wording.** Rejected: coverage folded at record
  time cannot be unfolded, so no footnote recovers a per-interpreter figure that was never
  stored.
- **Key only loss and lifetime totals per ring, leaving the sampled buffers per process.**
  `Count`, `Sum`, `Cov` and `F` become per-ring while percentiles stay blended. Rejected: the
  blended percentile is the number most likely to be quoted out of the table, and the split
  needs explaining wherever a column is documented.
- **Blank the distribution columns on any row folding more than one ring.** Consistent, and it
  costs `Total` its `Avg` and its `P99`, the two figures a run is usually summarized by. The
  footnote guards the same misreading for less.
- **Drop `Total` as well, printing rings only.** Rejected: a run has one cost, so a roll-up over
  everything has one answer. A roll-up over the interpreters of one process does not.
- **Bound interpreters per process rather than rings overall**, admitting every interpreter of
  an admitted process. Rejected: the footprint then has no bound in the dimension that grows.
- **Evict the least recently used ring, and let it resume its entry when it comes back.** This
  is what the branch did first. Rejected on two counts. A resumed row printed `Count` and `Sum`
  over the ring's whole life beside percentiles covering only the stretch since it returned, and
  no column said so. And a materialized entry outlives its process, so a reused pid resumed a
  dead one and added a second process's records to it. Settling on exit costs the LRU policy and
  buys back an entry that only one process can ever write to.

## Implementation

- `src/gcmon/stats.py` keys sampled metrics, loss and lifetime totals on the ring, bounds the
  active set, and answers both a ring's totals and a fold over them.
- `src/gcmon/stats_output.py` builds the table's two levels and the footer notes, and owns the
  `PID:IID` spelling.
- `src/gcmon/monitor.py` passes the iid it already has in hand when recording loss, holds the
  advisory's once-per-run latch, and settles a pid's rings where it already drops that pid's
  monitor state.
- `src/gcmon/pyperf/hook.py` keys loss per ring when replaying a capture from JSONL, so the
  offline path reconstructs what the live path recorded.
- `tests/stats/test_stats_output.py` pins the table's two levels and the footer wording;
  `tests/stats/test_stats.py` pins the per-ring arithmetic, the settling and the bound;
  `tests/test_loss_replay.py` pins that a replayed capture agrees with the live one.
