# ADR-0018: Require a value on `--stats`, and keep no bare alias

- **Status:** Accepted
- **Date:** 2026-08-17

## Context

`--stats` printed one table. Spec 0045, retired on landing and summarized in
[specs/RETIRED.md](../../specs/RETIRED.md), gave it two widths, so the flag has to carry which
one. The question this record settles is
what happens to the spelling everyone already types.

The obvious answer is to keep bare `--stats` working as an alias for the wider view. argparse
offers exactly that shape, `nargs="?"` with a `const`, and it is what the change was first
asked for. It does not survive contact with the parser gcmon has.

`monitor` takes the pid as a **required positional**. argparse decides whether an optional with
`nargs="?"` consumes the next token by looking at whether that token starts with `-`, and a pid
does not:

```
$ gcmon monitor --stats 12345          # works today
usage: monitor [-h] [--stats [{total,full}]] [-d DURATION] pid
monitor: error: argument --stats: invalid choice: '12345' (choose from 'total', 'full')
```

So the alias does not buy backward compatibility; it buys it for three of the four orderings and
breaks the fourth. The forms that keep working are `--stats` before an option (`-d`, `-r`,
`-o`), `--stats` last, and `--stats=full`. The form that breaks is `--stats` immediately before
the pid. The `run` command is unaffected either way, since it splits its argv at the first `-m`
or `-s` and both start with `-`.

That left a choice between a partial alias, a rewriting `argparse.Action` that recognises an
all-digit value and hands it back to the positional, and no alias.

A second question came with it: what the wider view is called. `total` and `all` are English
synonyms, and asked which of `--stats=total` and `--stats=all` prints more, a reader has no
reason to answer correctly — "the grand total of everything" points the wrong way. `totals`,
which reads as a noun and so escapes that, is worse here for a different reason: `docs/statistics.md`
defines **lifetime totals** as one of the three intervals a cell can report, and the statistics
module names its per-`(ring, gen)` companion figures `PauseTotals` and `LossTotals`. Both are
things a reader could expect `--stats=totals` to print, and neither is a block.

## Decision

**`--stats` requires a value.** `--stats=total` and `--stats=full`, and nothing else. Bare
`--stats` is a parse error, and so is any other value.

**No alias is kept, for either half.** Not bare `--stats` for `full`, not `all` as a hidden
synonym for it.

**`GCMON_STATS` takes the same words**, and an unreadable value fails the run rather than
falling back. The flag and the variable are one vocabulary, refused in one voice.

**Four words ask for no table: `no`, `off`, `false` and `0`.** On the flag and in the variable
alike, they select what an unset flag selects. They are the falsy complements of the truthy set
(`1`, `true`, `yes`, `on`) the variable took while it was a switch. Their truthy opposites are
**not** re-admitted, and the asymmetry is the point: "no table" is one outcome and "a table" is
two, so `off` has a referent and `on` does not. A blank `GCMON_STATS` reads as unset on the same
grounds: it names no view, and asking for nothing is the only reading it has.

**The wider view is `full`, not `all`.** `total` names the block it prints, which is the string
in the table's first column; `full` is a size word and the only candidate that reads
unambiguously as the larger of a pair.

## Consequences

Every existing `--stats` invocation stops working, at parse time, with a message naming the two
values. There is no deprecation window. This is affordable because it lands in a release whose
breaking-changes list already reshapes this table — the same release moves it from one block per
process to one per interpreter — so an operator upgrading is re-reading the output anyway, and
re-typing the flag costs them one edit in the same pass.

The flag becomes self-describing. Nobody has to remember which view a bare `--stats` picked,
which matters more here than in most flags: the two views differ by how much they print and not
by what the numbers mean, so a wrong guess is quiet.

The off words halve the break in the variable. `GCMON_STATS=0` in a shell profile meant no table
before this change and means no table after it, so only the settings that asked *for* the table
stop the run — the ones whose author has a view to choose. They also give the flag something it
had no way to say: the variable sets a default for every run in the shell, and `--stats=no` is
how one run declines it. `GCMON_RSS` has the same shape and no such escape — `--rss` is a
`store_true` with no off spelling — which reads as a gap in that flag rather than an argument
against this one.

`GCMON_STATS` becomes the only gcmon environment variable that can fail a run. Every other
`get_env_*` returns its default on an unreadable value — `GCMON_FORMAT=bogus` yields `chrome`,
`GCMON_TABLE_FORMAT=bogus` yields plain. This record does not claim the other eleven are wrong,
only that a variable selecting between two named views has no default that is safely one of
them.

Reversing this is not an undo. Re-admitting bare `--stats` later means choosing which view it
means, which is a decision nobody has had to make yet and which the two names deliberately do
not imply.

The names constrain what can be added next. A future flag choosing which *metrics* print cuts
across both blocks rather than choosing between them, so it is a second flag; `total` and `full`
name a set of blocks and must not quietly grow to mean a set of rows as well.

## Alternatives considered

**Bare `--stats` as an alias for `full`, via `nargs="?"` and `const`.** Rejected: it breaks
`gcmon monitor --stats <pid>` while appearing to preserve compatibility, so the operator it was
meant to protect is the one who hits the error, and the error is about a flag they did not
change.

**The same, plus an action that recognises an all-digit value and re-emits it as the pid.**
Rejected: it buys back one undocumented ordering at the price of a parser that guesses. Every
example in `README.md`, `docs/cli.md` and `docs/statistics.md` puts the target before the
options, and `run` already instructs operators that gcmon's own options go first.

**`all` as the wider view, or as a hidden synonym for `full`.** Rejected: a synonym of `total`
cannot be its opposite, and a hidden spelling that works but is documented nowhere is a second
vocabulary.

**`totals` as the narrower view.** Rejected: it collides with *lifetime totals*, a different
interval that prints in the footer, and with the per-`(ring, gen)` totals the statistics module
is built on. `Total`, singular, has one referent here and it is the one meant.

**Keeping `--stats` as a boolean and adding a second flag for the view.** Rejected: two flags
for one idea, with the invalid combination — the view flag without the enabling flag — left to
be caught by hand.

**Taking the truthy words back alongside the falsy ones, with `1` meaning `full`.** Rejected: it
is the bare alias under another spelling, and it makes the same undecided choice — which view an
operator who wrote `1` wanted — on their behalf and silently. `GCMON_STATS=1` says a table was
wanted, not which, and that is the question worth stopping for.

**Silently ignoring an unreadable `GCMON_STATS`, as the other variables do.** Rejected for this
variable: consistency with the flag is worth more than consistency with the other variables,
because the failure mode is a long capture that prints no table at the end.

## Implementation

`--stats` and `GCMON_STATS` are declared and validated in `gcmon.commands.monitoring_options`;
the raw value is read in `gcmon._env`. Validation deliberately does not live with the reading —
every `get_env_*` is evaluated while the parser is being built, before logging is configured, so
a value is refused where `rate`, `duration` and `flush_threshold` are already refused, after
logging exists.

The view itself is `StatsView` in `gcmon.stats_output`, beside the `TableFormat` behind
`--table-format`; each member's value is the word the operator types.
[Statistics](../statistics.md) documents what the two views print, and
`tests/stats/test_stats_output.py` locks in that the narrower one carries the wider one's cells
unchanged — line for line where the ring labels fit under the `PID:IID` header, and cell for cell
where they do not and the first column pads one wider.
