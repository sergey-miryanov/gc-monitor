# 0045 — Print the statistics table at two widths

- **Status:** Not started
- **Kind:** feature — ergonomics
- **Effort:** S
- **Origin:** operator request, 2026-08-17: "we need to be able print full stats table and the
  shorter one"
- **Respects:** [ADR-0016](../docs/adr/0016-the-ring-is-the-statistics-unit.md),
  [ADR-0018](../docs/adr/0018-the-stats-flag-requires-a-view.md)

## 1. Problem statement

`--stats` prints one table and there is no way to ask for less of it.

The table carries two levels, the run and the ring, and each level carries up to nine metrics
over three generations. A target that records every GC sub-phase therefore prints 27 rows per
block. An ordinary single-interpreter run has two blocks — `Total` and `12345:0` — and on that
run the two are **byte-identical**, because folding one ring into a roll-up changes nothing.
Half the table is a copy of the other half, and the operator scrolls past it to reach the
footer.

The operator who wants the one thing the table is usually opened for — what this run cost, per
generation — reads three rows and skips the rest. There is no flag that gives them those three.

Nothing here is wrong. It is a table sized for the widest case, printed for every case.

## 2. Solution

`--stats` takes a value naming which blocks to print.

```
--stats=total    the run-wide `Total` block, `Read Time`, the footer
--stats=full     that, plus one block per ring — the table as it prints today
```

`--stats=total` is the table minus the per-ring blocks. Same columns, same header, same metric
rows, same footer. On a single-interpreter run it costs the operator nothing at all, since the
block it removes was a copy. On a target running interpreters or a tree of processes it is the
summary, and `--stats=full` is still there when the operator wants to know which interpreter
carried the cost.

`GCMON_STATS` takes the same two words.

Bare `--stats` is gone. It becomes a parse error naming the two values, and so does any other
value, including `all` — see [ADR-0018](../docs/adr/0018-the-stats-flag-requires-a-view.md) for
why no alias is kept.

## 3. User stories

1. As a developer profiling my own script under `gcmon run`, I want the three `GC Pause` rows
   and the coverage footer without the ring block repeating them, so that the answer to "what
   did GC cost" fits on screen.
2. As an operator attached to a production process running sub-interpreters, I want
   `--stats=full`, so that I can still see which interpreter carried the pause time.
3. As an operator who typed `--stats=total` on a single-interpreter target, I want to know I
   lost nothing by it, so that I do not re-run with `--stats=full` to check.
4. As a CI job that pins gcmon's output, I want `--stats=full` to print exactly what `--stats`
   printed before, so that upgrading changes my log by the flag I type and nothing else.
5. As an operator who typed `--stats`, `--stats=all` or `--stats=brief`, I want an error naming
   the two values, so that the fix is the next thing I type.
6. As an operator who set `GCMON_STATS=1` from an older release, I want the run to stop and say
   so, so that I do not discover at the end of a long capture that no table is coming.
7. As an operator reading `--stats=total` on a target too wide for the table, I want the footer
   not to tell me that some rings got no row, so that I am not warned about the absence of rows
   I did not ask for.
8. As a gcmon maintainer, I want the two views to differ by which blocks are emitted and by
   nothing else, so that a column change lands in both without a second edit.

## 4. Implementation decisions

### The view is a value, and the value is the CLI spelling

`gcmon.stats_output` gains `StatsView`, beside the `TableFormat` it already exports and shaped
the same way — the member's value is the word the operator types.

```python
class StatsView(Enum):
    TOTAL = "total"
    FULL = "full"
```

`view` rather than `scope`: `StreamingStats.cumulative_scope` already owns that word for the
`(interpreters, processes)` pair the lifetime note folds over, and `_build_rows` uses it in
prose for a third thing again.

`print_stats` takes it alongside the format it already takes, and defaults to neither — the
caller has one and passes it.

### The option carries the view, not a flag

`MonitoringOptions.show_stats: bool` becomes `stats_view: StatsView | None`, `None` meaning no
table. The only reader that wants a boolean is `summary_lines`, whose `show_stats` parameter
decides whether to append the pointer to the breakdown; it keeps its boolean and takes
`stats_view is not None`. That pointer's text changes to name `--stats=total`, the cheaper of
the two views and the one that already contains the per-generation breakdown the sentence
offers.

`add_monitoring_options` declares `--stats` with `choices` and a required value. Not
`nargs="?"` with `const`: `monitor` has a required positional `pid`, and under `nargs="?"`
argparse consumes it as the flag's value, so `gcmon monitor --stats 12345` — which works today
— starts failing. Requiring the value fails that same form, but fails every spelling of it the
same way, with no ordering that silently means something else.

### The environment variable is validated where a rejected configuration already fails

`get_env_stats` returns the raw string rather than a `StatsView`, and
`get_monitoring_options` maps it, rejecting an unknown value with `logger.error` and `None` —
the path `rate`, `duration`, `flush_threshold` and `rss_interval` already take.

The reason it is not validated in `gcmon._env` is timing: every `get_env_*` is evaluated as an
argparse `default=` while the parser is being built, which is before `_setup_logging` has run.
An exception there is a bare traceback and a warning there prints through `logging.lastResort`
without the prefix every other gcmon message carries. `get_monitoring_options` runs after
logging is configured and already knows how to refuse a configuration.

This makes `GCMON_STATS` the only environment variable that can fail a run; the other eleven
fall back to their default on an unreadable value, `GCMON_TABLE_FORMAT` included. That
inconsistency is real and is left alone here — §6.

### `total` is `full` minus the ring blocks, and minus one footer note

`print_stats` emits the `Total` block, then the ring blocks **only under `FULL`**, then
`Read Time`, then the footer. Nothing else branches: same header, same column widths computed
over whatever rows exist, same `_SEP_GROUP` and `_SEP_PHASE` separators, same
`_dual`/`_coverage_cell`/`_factor_cell` cells.

The first column keeps its `PID:IID` header under `TOTAL`, where the only value it holds is
`Total`. The header is already imprecise in the shipping table for that same row, and matching
headers keep the two views diffable side by side. Renaming it per view would mean two table
shapes to document and to test.

`_print_footer` drops its third note — the count of rings that got no row because
`MAX_ACTIVE_RINGS` interpreters were already running — under `TOTAL`. That note exists to
reconcile ring rows against the run, and `TOTAL` prints no ring rows, so the discrepancy it
explains cannot be found; its closing reassurance, that those records are counted in `Total`,
describes the only block on screen. The information is not lost: `StreamingStats` already logs
a warning naming the pid and the iid the first time it declines a ring, and that fires whatever
view is asked for.

Notes 1 and 2 — coverage per generation, and the lifetime totals — are run-wide already and
read identically in both views.

## 5. Seams and testing decisions

- **Seam:** `gcmon.stats_output.print_stats` under `capsys`, plus `get_monitoring_options` for
  the option and the environment variable. Both are the highest seam that can observe the
  change: the view is a printing decision and nothing downstream of `print_stats` sees it, and
  the rejection of a bad `GCMON_STATS` is a decision `get_monitoring_options` makes alone.
- **New seam needed:** none.
- **What makes a good test here:** assert the emitted text, not the row-building helpers. The
  regression guard matters more than the new capability — `--stats=full` must print what
  `--stats` printed, so the strongest test is that a `FULL` capture of a multi-ring session
  contains a `TOTAL` capture of the same session as a prefix, up to the `Read Time` row and the
  footer.
- **Prior art:** `tests/stats/test_stats_output.py` for the table, `tests/monitoring/
  test_monitoring_options.py` for the option, `tests/test_env.py` for the variable.
- **Cases:**
  1. `TOTAL` on a multi-ring session prints the `Total` block, `Read Time` and the footer, and
     no row headed `pid:iid`.
  2. `FULL` on that session is byte-identical to what `print_stats` produced before this
     change.
  3. `TOTAL` and `FULL` on a **single-ring** session differ by exactly one block, and the block
     `FULL` adds repeats the `Total` block's cells.
  4. A session with a declined ring prints the untracked-rings note under `FULL` and not under
     `TOTAL`, and prints the coverage and lifetime notes under both.
  5. `--stats` with no value, and `--stats=all`, exit non-zero naming `total` and `full`.
  6. `GCMON_STATS=total` and `=full` select the view; `GCMON_STATS=1` fails the run through
     `get_monitoring_options` with a message naming the two values.
  7. `gcmon monitor 12345 --stats=total` parses, and the pid is still `12345`.

## 6. Out of scope

- **Choosing which metrics print.** The other reduction available here is dropping the eight
  sub-phase rows and keeping `GC Pause`, which is a *column-wise* cut across both blocks rather
  than a choice of blocks. It is a plausible future `--stats-metrics`, it is orthogonal to this
  one, and folding both into one flag would make a value named for a block also mean a set of
  metrics.
- **A deprecation window for bare `--stats`.** [ADR-0018](../docs/adr/0018-the-stats-flag-requires-a-view.md).
- **The eleven other environment variables that swallow an unreadable value.** Making them all
  fail loudly is one change with twelve call sites and its own compatibility question. It wants
  its own spec.
- **`gcmon 12345 --stats=…`, the form with no subcommand.** It does not work today and this
  does not fix it — see §7.
- **The rest of `--stats`.** No column, cell, percentile, coverage figure or footer wording
  changes. `FULL` is the shipping table.

## 7. Further notes

**The documented no-subcommand form has never worked.** `docs/statistics.md` heads its example
`gcmon 12345 --stats --table-format md` and `README.md` shows `gcmon 12345 -o trace.json
--stats`. Both exit 2: the top-level parser carries no options of its own and `12345` is not
one of its subcommand choices, so argparse rejects it before `gcmon.cli.main` reaches the
`args.command is None` branch that would have re-dispatched it as `monitor`. That branch is
unreachable.

This spec edits those two lines anyway, since every bare `--stats` in the repo has to become
`--stats=total` or `--stats=full`, and it fixes them to the subcommand form as it passes. It
does **not** decide whether the no-subcommand form should be made to work. That is a separate
question about the top-level parser — either `main` learns to detect a leading integer, or the
fallback branch and its documentation go. Whoever picks it up should file it; leaving the
examples as they are while touching them would be worse than either answer.

**Doc surfaces this touches:** `docs/statistics.md` (the flag, the example, and the sentence
that `--stats=total` loses nothing on a single-interpreter run), `docs/cli.md` (the options
table, the environment table, both `run` examples), `README.md`, and a line under the existing
**WIP → Breaking changes** in `CHANGELOG.md`.
