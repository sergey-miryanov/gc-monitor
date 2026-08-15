# 0036 — Report statistics per ring, so no printed figure blends two interpreters

- **Status:** Not started
- **Kind:** bug — correctness
- **Effort:** L
- **Origin:** grilling session, 2026-08-15 — "printing statistics which folded by iid doesn't
  look correct after last changes like #86"
- **Respects:** [ADR-0015](../docs/adr/0015-gc-loss-spans-on-their-own-track.md) (loss
  arithmetic, and the per-`(pid, iid)` tracks),
  [ADR-0004](../docs/adr/0004-toplevel-shared-counters.md) (`heap_size` is process-wide),
  [ADR-0003](../docs/adr/0003-gc-metrics-group-track.md) (per-`(pid, iid)` counter group).
  Settles into [ADR-0016](../docs/adr/0016-the-ring-is-the-statistics-unit.md).

## 1. Problem

An operator monitors a process running sub-interpreters, opens the trace, and finds three
interpreter rows with three visibly different GC profiles. They then run the same capture with
`--stats` and get one `GC Pause(0)` row per process. Its `P99` is a quantile over all three
interpreters' pauses at once, so it describes none of them. Its `Cov` divides a process-wide
sampled count by a process-wide lost count, so an interpreter gcmon read a tenth of is averaged
away against a busy one beside it, and the mid-run advisory, which fires below 90%, stays quiet
on a capture that is not worth keeping. Under the table, a footnote reads "Since interpreter
start" in the singular, over a figure summed across interpreters that started at different
moments.

In the operator's words, the folded figures "don't look correct". Nothing in the table can be
checked against what the trace shows, because the table has no dimension the trace shares.

## 2. Evidence

**The interpreter never enters the loss key.** `StreamingStats.record_loss` takes `(pid, gen)`
and its dict is typed `LossKey = tuple[int, int]`, documented as deliberate: *"`record_loss`
delivers increments, so two interpreters of one pid add into the same slot."* The increments sum
correctly; the attribution is gone, and no downstream wording recovers it. That is why the fix
cannot be a wording change.

**Sampled durations are keyed per process.** `StreamingStats.update` receives the whole record —
`TGCStatsInfo` carries `iid` — and keys its per-process metrics on the `pid` argument alone. The
percentile buffers underneath therefore mix rings.

**Lifetime totals keep the interpreter and then discard it.** `LifetimeKey = tuple[int, int,
int]` is `(pid, iid, gen)`, and `StreamingStats.lifetime_totals_by_gen`, its only reader, folds
over both `pid` and `iid` into a per-generation dict. `stats_output` prints that under a
singular label.

**The monitor has the iid in hand at the call site.** `EventsMonitor._ingest` groups by
`(iid, gen)` and passes `iid` to `record_lifetime` on the line above the `record_loss` call that
drops it.

**The replay path repeats the same key.** `pyperf.hook._replay` accumulates loss into
`lost[(pid, entry.gen)]` even though the loss record it is reading carries `iid` — `TLossMsg`
declares it, and `LossMsg` is constructed with it.

**Eviction loses history, and lets `Total` exceed the rows.** `StreamingStats.update` checks
only the active dict before creating a fresh entry for a process. A process evicted to the
materialized dict and then seen again gets a blank entry, and `get_pid_stats` prefers the active
one, so the materialized history is shadowed and never printed. `StreamingStats.metrics`, which
feeds the `Total` block, is a separate accumulator untouched by eviction, so in that case
`Total` exceeds the sum of the rows beneath it. The materialized dict is also never pruned.

**Why the suite didn't catch it.** Every statistics test drives `StreamingStats` with records
from one interpreter. `tests/stats/test_stats.py` and `tests/stats/test_stats_output.py` use a
single pid and the default `iid`, so no assertion distinguishes a per-process figure from a
per-ring one; the two are equal on every input the suite supplies. The trace-side suites, which
do exercise several interpreters, never look at `StreamingStats`.

## 3. Scope

**Affected:** the `--stats` table and both its footnotes, on every `--table-format`; the
mid-run coverage advisory; the statistics rebuilt from JSONL by the pyperf hook, and therefore
the values behind the released benchmark metric names.

**Not affected:** the trace itself, in any format. It has been per-`(pid, iid)` since ADR-0015,
and this change makes the table agree with it. Also unaffected: `gcmon combine`, exit codes, the
JSONL schema, the `Read Time` row (monitor-side, belonging to no ring), the end-of-run summary
(run-wide by design, the same scope as `Total`), and the high-water `heap_size` figure, which
stays per process because ADR-0004 makes it process-wide with no generation and no thread
affinity.

## 4. Proposed change

1. **Key the statistics on the ring.** Sampled metrics, loss and lifetime totals all key on
   `(pid, iid, gen)` — the key `gcmon.loss` already calls a ring, and the key the exporters
   already draw. `record_loss` gains an `iid` parameter; `EventsMonitor._ingest` passes the one
   it already holds. Folding moves to read time, so a ring's totals and a roll-up over rings are
   both derivable. The public readers move with the key: the per-process accessors become
   per-ring, `pause_totals` takes an `iid`, and `pause_totals_by_gen` keeps its name and its
   run-wide scope.

2. **Print two levels: the run and the ring.** The `Total` block stays; the per-process block
   goes. The first column is headed `PID:IID` and every ring row carries both parts, `12345:0`
   included. `Total` keeps its percentiles, which the coverage footnote already qualifies. The
   alternatives are settled in ADR-0016 §Alternatives.

3. **Bound active rings at 256, and make eviction non-destructive.** The bound replaces one of
   64 processes. Before creating a fresh entry, look in the materialized dict and resume it, so
   `Count` and `Sum` survive an eviction round trip exactly and only the percentile buffer
   restarts. This makes `Stats.materialize` reversible: today `Stats.update` raises
   `RuntimeError` after it, and that guard has to relax.

4. **Test coverage per ring in the advisory.** The lookup returns the first ring under the
   floor as `(iid, gen, coverage)` and the warning names the interpreter. The once-per-run latch
   stays in `EventsMonitor`, for the reason its docstring already gives: the remedy is `--rate`,
   which no ring owns. Expect this to fire on captures that are silent today. That is the defect
   being fixed.

5. **Make the lifetime footnote name its fold.** One line per generation whatever the tree size,
   stating what it summed over: *"Since each interpreter started, monitored window included,
   summed over 3 interpreters in 2 processes: Gen0 4820 in 6231.400 ms."* The coverage footnote
   above it stays run-wide, mirroring `Total`'s `Cov`; only its prose in `docs/statistics.md`,
   which says the column is "gathered across every PID", changes to name rings.

6. **Key loss per ring in the replay path.** `pyperf.hook._replay` accumulates into
   `(pid, iid, gen)`. Not optional: `record_loss` takes an `iid` after step 1, so the offline
   path does not typecheck without it, and a capture read back from JSONL has to report what the
   live run reported.

**Left open, and what settles it:** nothing. Where a choice existed it is made above or in
ADR-0016.

## 5. Seams and testing decisions

- **Seam:** `stats_output`'s table and footer builders against a `StreamingStats` populated
  directly, which is the seam `tests/stats/test_stats_output.py` already uses. It is the highest
  seam that can observe the defect: the wrongness is in printed text, and no trace assertion
  reaches it.
- **New seam needed:** none for the table. The per-ring arithmetic is asserted through the
  public readers on `StreamingStats`, as `tests/stats/test_stats.py` already does.
- **What makes a good test here:** feed one `StreamingStats` records from two interpreters of
  one pid with deliberately different pause distributions and different loss, then assert the
  two ring rows differ from each other and that neither equals what a single blended row would
  have printed. A fixture using one interpreter passes against the old code and the new, which
  is why this shipped.
- **Prior art:** `tests/stats/test_stats_output.py`'s existing row-level assertions for the cell
  layout, and `tests/test_loss_replay.py` for the live-versus-replayed agreement.
- **Cases:**
  1. Two interpreters of one pid, different distributions and different loss: two ring rows,
     each with its own `P99`, `Cov` and `F`; no row blends them.
  2. A starved interpreter beside a fully-observed one: the advisory fires and names the starved
     interpreter's iid. Under the old key the blended figure is above the floor and nothing
     fires.
  3. An eviction round trip: a ring evicted and seen again reports `Count` and `Sum` covering
     both stretches, and `Total` equals the sum of the ring rows.
  4. Regression guard: a single-interpreter, single-process, lossless run prints the same table
     as today except that its row label gains `:0` and the header reads `PID:IID`. Every other
     cell stays byte-identical.
  5. A capture replayed from JSONL through the pyperf hook reports the same per-ring coverage as
     the live run that wrote it.

## 6. Out of scope

- **Pid reuse.** A successor process on a reused pid overwrites its predecessor's cumulative
  counters, so a folded lifetime total can decrease mid-run, and the two processes' samples
  merge into one ring row. The ring key does not close this; it needs an epoch on the pid so a
  reused pid is a different ring, which is a separate change to a path ADR-0015 already
  documents a related hazard on. Specified separately.
- **The benchmark metric names.** They stay exactly as released. They are flat run-wide scalars,
  which is the right scope for benchmark metadata and the scope `Total` reports; per-ring keys
  would embed pids that differ every run. Whether to add interpreter and process counts beside
  them, so a reader can tell a fold from a single interpreter's history, is a follow-up.
- **The `lifetime` naming sweep.** `lifetime` names both the `Processes`-track span
  ([ADR-0011](../docs/adr/0011-process-lifetime-and-ordering.md)) and the cumulative counters.
  This spec writes the rule down in `CONTEXT.md` and leaves the rename to its own change.
- **Reporting undrawable loss windows.** Belonged to spec 0035 and did not land with it; it
  rests on a quantity that does not exist in the source.

## 7. Further notes

#86, GC events loss detection, made the asymmetry visible without introducing it. It gave the
trace a per-`(pid, iid)` loss row and gave the table `Cov` and `F`, and the two disagree because
the statistics side kept a key that predates interpreters mattering.
