# 0050: Name the poll interval for what it is

- **Status:** Not started
- **Kind:** feature (ergonomics)
- **Effort:** S
- **Origin:** split out of spec 0049, 2026-08-17, so a compatibility decision would not hold up a
  correctness fix. 0049 landed the same day
- **Respects:** [ADR-0013](../docs/adr/0013-rss-sampling.md) (`--rss-interval` is decoupled from
  the poll interval and stays a separate option),
  [ADR-0019](../docs/adr/0019-schedule-tick-starts-on-a-fixed-grid.md) (the interval is between
  tick starts; this renames the number, not what the loop does with it)

## 1. Problem statement

`--rate 0.1` does not set a rate. A rate is a frequency, and this is a duration in seconds, the
interval between one poll and the next. An operator who reads the name literally sets `--rate 10`
expecting 10 Hz and gets one poll every ten seconds, which on a short run looks like gcmon
recording nothing.

gcmon's own output does not help. `monitoring_options` echoes `Rate: 0.1s`, a rate quoted in
seconds. ADR-0013 writes "the GC poll runs at 10 Hz by default" and "the 0.1 s GC poll rate" one
paragraph apart, meaning the same setting both times. The neighbouring option gets it right:
`--rss-interval` is also a duration in seconds and says so.

## 2. Solution

The option is `--interval`, and the environment variable is `GCMON_INTERVAL`. It reads the same
way as `--rss-interval` next to it, and the log line becomes `Interval: 0.1s`.

`--rate` and `GCMON_RATE` keep working, undocumented, so no existing command line, script or CI
job breaks. They are not mentioned in the help, the README or the docs; someone reading anything
current sees one name.

## 3. User stories

1. As an operator new to gcmon, I want the option that sets a duration to be named for a duration,
   so that I do not have to run an experiment to find out which direction the number goes.
2. As an operator with `--rate 0.05` in a shell script, I want it to keep working after upgrading,
   so that a naming fix is not a migration.
3. As an operator with `GCMON_RATE` set in a CI job's environment, I want the same, since an
   environment variable is harder to grep for than a flag and often lives in a system I do not own.
4. As an operator reading the coverage advisory, I want the option it names to be the option in my
   command line, so that the advice is actionable without translation.
5. As someone comparing `--interval` with `--rss-interval`, I want the two to be the same kind of
   number in the same unit, so that the relationship the two warnings describe is obvious.
6. As a gcmon maintainer, I want one name in the source and one name in the docs, so that a reader
   of the code and a reader of the README are talking about the same thing.

## 4. Implementation decisions

1. **`--interval` is the name.** Not `--period`, which is accurate but rarer in this kind of tool,
   and not `--poll-interval`, which is longer and gains nothing once `--rss-interval` establishes
   that a bare `--interval` is the poll one. The short form `-r` moves to `-i`; `-r` stays bound to
   the old spelling.
2. **The old names survive as hidden aliases**, `--rate` with `help=argparse.SUPPRESS` and
   `GCMON_RATE` consulted only when `GCMON_INTERVAL` is unset. Precedence is explicit: the new
   name wins wherever both appear, and passing both flags is not an error.

   **Settled:** a clean break with no alias is rejected even at 0.5.0. The cost of the alias is two
   lines in the option table and one branch in `_env`; the cost of the break falls on people who
   are not reading this repo's changelog.

   **What would settle removing the alias:** a 1.0 release. There is no deprecation policy today,
   so this spec does not invent one; it leaves the alias in place and names the event that lets
   someone drop it.
3. **The internal spelling changes with it.** `MonitoringOptions.rate` becomes `interval`, and
   `MonitorLoop`'s constructor parameter follows. Both are internal, so they move in one commit
   with no compatibility surface, and leaving them as `rate` would preserve exactly the confusion
   the spec exists to remove.
4. **Every reference moves, and the ADRs are amended rather than rewritten.** Roughly 45 mentions
   across `docs/cli.md`, `docs/monitoring.md`, `docs/formats.md`, `docs/rss.md`,
   `docs/statistics.md`, three ADRs, the specs that cite it, the advisory text in `EventsMonitor`,
   and the tests. `docs/adr/README.md` makes this explicit: an ADR anchors on the names the outside
   world sees, and renaming one of those is itself a decision, so the record moves with it.
5. **`CONTEXT.md`'s Rate entry is re-headed**, keeping the definition and adding the old spelling
   to its `_Avoid_` line. The concept does not change; only which word names it.

## 5. Seams and testing decisions

- **Seam:** `tests/test_cli.py`, which already parses argument vectors and asserts the resulting
  options, and `tests/monitoring/test_monitor_cmd.py` for the env-var path. Argument parsing is the
  highest seam that can observe a flag rename, and both suites exist.
- **New seam needed:** none.
- **What makes a good test here:** assert the value that reaches `MonitoringOptions`, from each of
  the four inputs (new flag, old flag, new variable, old variable) rather than asserting the
  parser's internal attribute names. The hazard in an alias is precedence, not parsing.
- **Prior art:** `tests/test_cli.py` for the flag cases; `tests/monitoring/test_monitor_cmd.py` for
  the env-var cases and for the pattern of setting `GCMON_*` around a parse.
- **Cases:**
  1. `--interval 0.05` sets the interval; `-i 0.05` does too.
  2. `--rate 0.05` still sets it, and `-r 0.05` with it.
  3. `GCMON_INTERVAL` sets it; `GCMON_RATE` sets it; with both set, `GCMON_INTERVAL` wins.
  4. A flag beats a variable, as it does today, in both spellings.
  5. `--rate` does not appear in `--help` output. This is the only assertion that the alias stayed
     hidden, and it is what stops the two names being documented as equals later.
  6. The regression guard: the log line, the validation errors and the advisory name the new
     option, and no message anywhere still says "rate".

## 6. Out of scope

- **How the interval is honoured.** 0049 owns the scheduling; this spec renames the number it
  schedules against and changes no behaviour. 0049 has landed, so the ordering it wanted is
  settled and what remains here is the prose it wrote under the old name.
- **`--rss-interval`.** Already correctly named. It keeps its own name and its independence from
  the poll interval (ADR-0013).
- **Accepting a frequency.** A `--rate 10` meaning 10 Hz, or a unit suffix like `100ms`. Both are
  real ergonomic ideas and both are a different feature: this spec makes one name honest, it does
  not add an input format. Adding a frequency later is easier once the duration has a duration's
  name.
- **A general deprecation policy.** Named in section 4 as the thing that does not exist. Inventing one to
  retire a single alias is the wrong order.
- **`GCMON_*` variables other than this one.** None of the rest is misnamed.

## 7. Further notes

The rename is plausibly upstream of the defect spec 0049 fixed. `sleep(rate)` after the work is a
natural thing to write if you are thinking "rate", and an obviously wrong thing to write if you are
thinking "the interval between poll starts". That was an argument for doing this soon rather than
for doing it first: 0049 made the timing contract precise, and this makes the name match the
contract.
