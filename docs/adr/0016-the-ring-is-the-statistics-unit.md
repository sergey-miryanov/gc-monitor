# ADR-0016: Report statistics per ring, and drop the per-process row from the `--stats` table

- **Status:** Accepted
- **Date:** 2026-08-15

## Context

Every interpreter in a target keeps its own collector, its own rings and its own cumulative
counters. The trace side has said so since [ADR-0015](0015-gc-loss-spans-on-their-own-track.md):
records go on a thread track per `(pid, iid)`, loss spans on a `GC Loss` track per `(pid, iid)`,
and [ADR-0003](0003-gc-metrics-group-track.md)'s counter group is per `(pid, iid)` as well. A
reader who opens a trace of a process running three interpreters sees three rows.

The statistics side said nothing of the kind. Sampled durations were accumulated per pid, and
loss was accumulated per `(pid, gen)` — the interpreter never entered the key at all, so two
interpreters' gaps landed in one slot and could not be told apart afterwards. Lifetime totals
were the one quantity keyed per `(pid, iid, gen)`, and their only reader summed the interpreter
away before printing.

Three consequences reached the operator. A `GC Pause(0)` row reported one blended distribution
for interpreters that may run entirely different workloads, so its `P50` through `P99` described
none of them. `Cov` divided a pid-wide sampled count by a pid-wide lost count, so an interpreter
gcmon read almost nothing of was masked by a busy one beside it, and the mid-run advisory that
fires below 90% stayed silent on captures worth discarding. The footer's lifetime note read
"Since interpreter start" in the singular over a sum across interpreters that started at
different moments — and, when a pid was reused, across processes.

The fold also could not be undone. Coverage averaged before it was stored is not recoverable by
any wording change downstream, which is what decided the shape of the fix rather than the shape
of the output.

## Decision

**The ring is the unit statistics are keyed on and reported for.** `gcmon.stats` keys sampled
metrics, loss and lifetime totals on `(pid, iid, gen)`, the same key `gcmon.loss` already uses
for its accumulators and the same one the exporters already draw. Folding happens when a figure
is read, not when it is recorded, so both a ring's number and a roll-up over rings stay
available.

**The `--stats` table prints two levels: the run, and the ring.** The `Total` block stays, as
the one answer to what a run cost. The per-process block goes. An intermediate fold over the
interpreters of one process answers a question nobody asked of a capture that already
distinguishes them, and it was the level at which the blending did its damage.

**The first column is headed `PID:IID`, and every ring row carries both parts** — `12345:0` on
an ordinary single-interpreter run as much as on a tree. A header naming two fields whose cells
sometimes hold one is worse than a `:0` that says what it says.

**`Total` keeps its percentiles.** They are quantiles of a mixture and the footer says so. The
alternative, blanking them, takes the quick reading of a run away to prevent a misreading the
note already covers; a reader who needs a distribution reads a ring row, which is now there to
be read.

**The active-ring bound is 256**, replacing a bound of 64 processes. A run holds full sampled
state for that many rings and evicts least-recently-used ones to a materialized form, keeping
their counts, sums and settled percentiles. At the same footprint per entry as before, 256 buys
the previous 64 processes four interpreters each.

**Eviction stops discarding history.** A ring seen again after eviction resumes its own entry
rather than starting blank: `Count` and `Sum` stay exact across the round trip, and only the
percentile buffer restarts, so the quantiles that follow describe the records read since. The
previous behaviour created a fresh entry and shadowed the materialized one, which then never
printed — one of the ways `Total` could exceed the rows beneath it.

**The coverage advisory tests rings.** It names the interpreter alongside the pid and
generation, and it still fires once per run, latched in `gcmon.monitor`: the remedy it suggests
is `--rate`, which no ring owns.

**The lifetime note folds, and says what it folded.** One line per generation whatever the size
of the tree, stating the interpreter and process counts it summed over, so no reader takes it
for one interpreter's history:

```
2. Since each interpreter started, monitored window included, summed over 3 interpreters
   in 2 processes: Gen0 4820 in 6231.400 ms.
```

**Process-wide quantities stay keyed per process.** `heap_size` has no generation and no thread
affinity ([ADR-0004](0004-toplevel-shared-counters.md)), so its high-water mark is still taken
per pid. The end-of-run summary and the coverage footnote likewise stay run-wide, which is the
scope they were written for and the scope `Total` reports.

## Consequences

- **Ordinary output changes.** A single-interpreter run's rows go from `12345` to `12345:0`, so
  every example, every document showing the table and every row-level assertion in the suite
  moves. This is user-visible and belongs in the CHANGELOG.
- **The advisory fires on runs that are silent today.** A starved interpreter beside a busy one
  now trips the 90% floor on its own figure. That is the defect being fixed, and it makes the
  warning noisier on trees that were always this incomplete.
- **A percentile printed on a ring row describes one distribution.** `Total`'s does not, and is
  the only place in the table where that is still true.
- **Footprint scales with interpreters, not processes.** A process creating many interpreters
  consumes ring slots that a process-keyed bound gave it for free. The bound is what stops it,
  and a tree wide enough to exhaust it degrades by evicting rather than by growing.
- **`Total` is still a separate accumulator**, fed once per record, so it stays right when rings
  are evicted and stays independent of how many the table can hold.
- **Reading a capture back from JSONL gives the same numbers as reading it live**, because the
  replay path keys loss per ring too. It could not have done so while the key lacked an iid.
- **A reused pid still corrupts the fold.** The new key does not fix it: a successor process's
  much smaller cumulative counters overwrite its predecessor's, so a folded lifetime total can
  decrease mid-run. ADR-0015 already accepted the related hazard on the monitor side; closing
  this one needs an epoch on the pid, and is specified separately.
- **A benchmark's metadata keeps its released key names.** They are flat, run-wide scalars,
  which is the scope a benchmark wants and the scope `Total` reports; per-ring keys would embed
  pids that differ every run and so compare across none of them.

## Alternatives considered

- **Keep the process rows and add ring rows beneath them**, printed only where a process ran
  more than one interpreter. Rejected: it leaves ordinary output untouched, which is its whole
  appeal, but it keeps the blended process row as the thing a reader's eye lands on and makes
  the table's depth depend on the target's shape.
- **A separate `IID` column beside `PID`.** Machine-readable and uniform, and it widens every
  table by a column that reads `0` on nearly every run. The compound cell carries the same two
  fields in the width already there.
- **Leave the arithmetic alone and fix only the wording.** Rejected on the point that decided
  the whole change: coverage folded at record time cannot be unfolded, so no footnote can
  recover a per-interpreter figure that was never stored.
- **Key only loss and lifetime totals per ring, leaving the sampled buffers per process.** The
  cheap two-thirds: `Count`, `Sum`, `Cov` and `F` become per-ring while percentiles stay
  blended. Rejected because the blended percentile is the number most likely to be quoted out of
  the table, and the split would need explaining in every document that mentions a column.
- **Blank the distribution columns on any row folding more than one ring.** Consistent, and it
  costs `Total` its `Avg` and its `P99` — the two figures a run is usually summarized by. The
  footnote is a cheaper guard against the same misreading.
- **Drop `Total` as well, printing rings only.** Rejected: a roll-up over everything answers a
  question with one answer, where a roll-up over an arbitrary middle layer does not.
- **Bound interpreters per process rather than rings overall**, admitting every interpreter of
  an admitted process. Rejected: the footprint then has no bound in the dimension that grows.

## Implementation

- `src/gcmon/stats.py` keys sampled metrics, loss and lifetime totals on the ring, bounds the
  active set, and answers both a ring's totals and a fold over them.
- `src/gcmon/stats_output.py` builds the table's two levels and the footer notes, and owns the
  `PID:IID` spelling.
- `src/gcmon/monitor.py` passes the iid it already has in hand when recording loss, and holds
  the advisory's once-per-run latch.
- `src/gcmon/pyperf/hook.py` keys loss per ring when replaying a capture from JSONL, so the
  offline path reconstructs what the live path recorded.
- `tests/stats/test_stats_output.py` pins the table's two levels and the footer wording;
  `tests/stats/test_stats.py` pins the per-ring arithmetic and the eviction round trip;
  `tests/test_loss_replay.py` pins that a replayed capture agrees with the live one.
