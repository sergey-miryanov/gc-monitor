# 0035 — Say what the capture is worth in the summary every run prints

- **Status:** Not started
- **Kind:** bug — reporting
- **Effort:** S
- **Origin:** grilling session, 2026-08-08; surfaced while specifying the loss-span redesign
- **Respects:** [ADR-0015](../docs/adr/0015-gc-loss-spans-on-their-own-track.md) (what loss does
  to the numbers), [ADR-0013](../docs/adr/0013-rss-sampling.md) (graceful degradation without
  optional dependencies)
- **Depends on:** nothing; the discarded-window count in §4.3 shipped with ADR-0015's redesign

## 1. Problem

A run ends like this, whatever flags were passed:

```
Monitoring complete.
Total events: 1234
Trace saved to: trace.pftrace
```

Nothing there says 1234 is what gcmon *sampled* rather than what the target *collected*. On the
capture ADR-0015 was built against, gen 0 collected about 87 times per 100 ms tick against 11
ring slots, so the honest version of that line is closer to "1234 of roughly 9800". An operator
reads 1234, opens the trace, and calibrates everything they see against a number that is off by
most of an order of magnitude.

The figures that would say so exist and are exact — `Cov`, the per-generation lost counts, the
lifetime totals — and every one of them is behind `--stats`, which is opt-in. The one thing that
does fire unprompted is `StreamingStats`'s coverage advisory, and it fires once, mid-run, on a
threshold, so a run at 91% coverage says nothing at all and a run at 12% says it once, hundreds
of lines before the summary.

The redesign added a third figure with the same problem: a count of loss windows too degenerate
to draw, whose only surface is the `--stats` footer. That one matters more than the others, because
it is the only client-side evidence gcmon will ever have that the target's record ordering did
not reach the reader.

## 2. Evidence

Two blocks of output, gated differently, in `commands.monitoring_base`:

- Unconditional, three `logger.info` calls — `"Monitoring complete."`, `"Total events: %s"` with
  `stats.count()`, and `"Trace saved to: %s"`.
- `print_stats(stats, ...)`, guarded by `if options.show_stats`.

`stats.count()` is `StreamingStats._count`, incremented once per record in `update()`. It counts
what the poll loop emitted. Nothing scales it, and nothing beside it names what it excludes.

`stats_output._print_footer` is where the honest reading lives, and its docstring ties it to the
table it follows: *"What the table's two number kinds mean, and the third it cannot show."* It is
built to annotate columns, not to stand alone, and it returns early when `covered` and `lifetime`
are both empty.

`StreamingStats.record_loss` warns via `logger.warning` when `coverage(pid, gen)` first drops
below `COVERAGE_ADVISORY = 0.9`, behind a `_coverage_warned` once-flag. That is a mid-run alarm
for a bad case, not an end-of-run statement of what the capture contains.

## 3. Scope

**Affected:** the end-of-run output of `gcmon monitor` and `gcmon run`, on every format including
`stdout`.

**Not affected:** the `--stats` table and its footer, which stay exactly as they are — this spec
adds a floor, it does not move the ceiling. Trace content, exit codes, the JSONL record, the
pyperf metrics and `gcmon combine` are all untouched. `StreamingStats`'s arithmetic gains no new
quantity: `StreamingStats.undrawable_count` already exists.

**Why the suite didn't catch it:** `tests/stats/test_stats_output.py` tests the table and the
footer, and `tests/monitoring/test_monitoring_base.py` tests the run's control flow. The
unconditional summary is three `logger.info` calls inside a command function, which no test
asserts on and which nothing calls a summary — so there was no unit whose contract could be
wrong.

## 4. Proposed change

1. **Give the summary a name and a home.** Extract the three log lines into a function in
   `stats_output` beside `print_stats`, taking `StreamingStats` and returning the lines. Today
   there is no seam at all: the text lives inside `monitoring_base`'s success path, reachable
   only by running a monitor loop. This is most of the effort, and everything below is a line in
   the function it creates.

2. **Say sampled against exact.** `Total events` becomes the sampled count against the exact one
   gcmon reconstructed, with the coverage figure — the same `sampled/exact` convention the table
   already uses for `Count` and `Sum`, so a reader who later passes `--stats` sees the same two
   numbers rather than a third framing. Print the plain count when nothing was lost; a run that
   saw everything has nothing to qualify.

3. **Report windows that could not be drawn.** `StreamingStats.undrawable_count`,
   stated as the target's record ordering failing to reach the reader rather than as gcmon losing
   something — ADR-0015 puts that fix upstream, and the two readings send an operator to different
   places. Omit the line at zero, which is the normal case.

4. **Keep it to a few lines.** This is the floor, not a table without borders. Per-generation
   breakdowns, lifetime totals and percentile caveats stay in `--stats`; if the summary needs more
   than a handful of lines the answer is to point at `--stats`, and it should, once, whenever it
   qualified anything.

**Rejected: make `--stats` the default.** It would fix the visibility and take the table with it,
onto every CI log and every scripted run that currently gets three predictable lines. The problem
is not that the table is hidden; it is that the summary states a number without its qualifier.

**Rejected: raise the coverage advisory's threshold or repeat it at the end.** It is a warning
about a bad case and reads as one. What is missing is a neutral statement of what the capture
contains, which a warning cannot be — a 95%-coverage run should still say 95%, and warn about
nothing.

## 5. Seams and testing decisions

- **Seam:** the extracted summary function in `stats_output`, asserted on its returned lines
  against a `StreamingStats` built directly. Highest available seam: it is the unit that owns the
  wording, and `tests/stats/test_stats_output.py` already tests its neighbour the same way.
- **New seam needed:** yes — creating it is step 1, and its absence is why this was never caught.
- **What makes a good test here:** assert the summary against the stats object that produced it —
  the exact count it reports equals `sampled + lost` from the same `StreamingStats` — rather than
  against a literal string. A literal test passes equally with a summary that quotes the sampled
  count twice.
- **Prior art:** `tests/stats/test_stats_output.py`'s footer tests, which already cover the
  "only printed when something was lost" conditional this spec repeats.
- **Cases:**
  1. A run that lost nothing prints the plain count and no qualifier — the regression guard for
     every existing scripted run and CI log.
  2. A run that lost records prints sampled against exact, and the exact figure equals
     `sampled + lost`.
  3. A run with a discarded window names it; a run with none does not mention it.
  4. The `--stats` table and footer are byte-identical either way.

## 6. Out of scope

- **A machine-readable summary** — JSON on stdout, or an exit code that reflects coverage. Both
  are real asks and neither is this one; a scripted consumer should read the JSONL or the trace.
- **Changing the `--stats` table or footer.** Untouched by design, so this spec cannot regress
  anyone already using it.
- **Reporting loss per process on a multi-process run.** The summary is run-level; `--stats`
  already breaks down per pid.
- **Anything about how loss is drawn.** ADR-0015 owns the trace; this owns the text at the end.
- **The pyperf hook's publish gate.** Decided against changing, on the record — see §7.

## 7. Further notes

**The same question is asked wrongly in one more place, and is deliberately left alone.**
`GCMonitorHook.teardown` replays the capture into a `StreamingStats` and then publishes behind
`if ss.count():`. That counter is incremented once per record inside `update`, so it counts what
gcmon **sampled**; loss reaches `record_loss`, which never touches it. A session whose every
record was overwritten therefore publishes no `gc_*` metadata at all, and is indistinguishable
from a benchmark where no collection ran — the opposite finding.

The reconstruction itself is unaffected. A `StreamingStats` holding one loss record of 42
collections reports `lost_count = 42`, `coverage = 0.0` and `aggregate() == {"pause_count": 42}`,
and the coverage advisory fires on stderr. The gate discards a correct answer rather than failing
to compute one.

It stays as it is. A run that sampled nothing has no percentiles, so publishing `pause_count`
beside a `coverage` of zero is arguably noisier than silence, and the case is rare enough not to
earn a change to what lands in benchmark metadata. The behaviour is pinned by
`tests/pyperf/test_pyperf_hook.py::TestLossIsNeverReplayedAsACollection::test_a_loss_only_capture_publishes_nothing_rather_than_zeroes`,
so reversing it later means updating that test deliberately.

What is worth recording is the shape, because it is this spec's thesis in another file: `count()`
asks *"did we sample anything?"* while every metric behind the gate reports *"what
happened"*. That is the last call site still thinking in sampled terms after the branch moved to
exact. If the summary work here changes how `StreamingStats` exposes "is anything known", revisit
the gate then rather than on its own.

**Not to be confused with a larger question that was raised and closed.** Whether loss should
shape pyperf metadata *at all* — reverting `Count` and `Sum` to sampled and dropping
`gc_pause_gen_N_coverage` — was considered and rejected. Four of the eight published metrics are
loss-derived or loss-corrected today, the change is already recorded as breaking in
`CHANGELOG.md`, and coverage is the figure that tells a benchmark reader whether the other seven
mean anything: without it a 40%-sampled run and a 100%-sampled run publish indistinguishable pause
sums. Reopening that needs its own spec and a changelog reversal, not a note here.

Settle when picked up: whether the summary stays on `logger.info` or moves to `print`. The
`--stats` table uses `print`, the current three lines use `logger.info`, and they end up in
different streams under a `--log-level` that suppresses info — which would silently take the
qualifier away and leave the trace path behind, or the reverse. Whichever is chosen, the count and
its qualifier must not be separable by a log level.
