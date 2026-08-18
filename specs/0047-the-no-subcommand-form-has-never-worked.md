# 0047: Decide the no-subcommand form, `gcmon 12345`

- **Status:** Not started
- **Kind:** bug (reporting)
- **Effort:** XS
- **Origin:** spec 0045 section 7, 2026-08-17, which fixed the two documented examples it had to touch
  and deliberately left the question open
- **Respects:** none

## 1. Problem

`README.md` and `docs/cli.md` tell an operator that `gcmon` without a subcommand monitors
("`gcmon` takes three subcommands: `monitor`, `run` and `combine`. Without one it monitors"), and
show `gcmon 12345`, `gcmon 12345 -v`, `gcmon 12345 --output trace.json --rate 0.01`. Every one of
them exits 2 with a usage message. The operator's first command, copied from the top of the
README, fails.

## 2. Evidence

`gcmon.cli._create_parser` builds a parser carrying no options of its own and three subcommand
choices, so `12345` is rejected as an invalid choice before any of gcmon's code runs.
`gcmon.cli.main` has a branch for `args.command is None` that re-dispatches to `monitor`, and it
is unreachable: `parse_args` has already exited by the time it could be read.

Nothing in the suite covers the form. `tests/test_cli.py::TestCliHelp` asks each subcommand for
its help, and every other CLI test names a subcommand.

## 3. Scope

**Affected:** the documented `gcmon <pid> …` form in `README.md` (Quick Start, and the RSS and
`combine` neighbours around it) and `docs/cli.md` (the "What you'll see" and "monitor" sections), and the
unreachable fallback branch in `gcmon.cli.main`.

**Not affected:** `gcmon monitor <pid>`, `gcmon run`, `gcmon combine`. Every documented
invocation that names its subcommand works.

## 4. Proposed change

One of two, and this spec does not choose:

1. **Make it work.** `main` detects a leading token that is not a subcommand choice (an
   all-digit pid) and inserts `monitor` before it, so the fallback branch becomes reachable and
   the documentation is true. Costs a parser that guesses at its first argument.
2. **Delete it.** Drop the fallback branch and rewrite the examples to name `monitor`. Costs the
   shorter form the docs have promised since the first release.

Whichever lands, the examples and `gcmon.cli.main` have to agree, which they do not today.

## 5. Seams and testing decisions

- **Seam:** `gcmon.cli.main` with an argv list, which is where the dispatch decision is made and
  the only place the fallback branch can be observed.
- **New seam needed:** none.
- **What makes a good test here:** run the argv a documented example prints. Under option 1 that
  is `main(["12345", "-d", "0.1"])` reaching the monitor path; under option 2 it is the absence
  of the branch and a doc that no longer offers it.
- **Prior art:** `tests/test_cli.py::test_main_combine_command` for dispatch through `main`.
- **Cases:**
  1. The form the README's Quick Start prints today.
  2. `gcmon combine …` and `gcmon run …` still dispatch as they do now, whichever answer lands.

## 6. Out of scope

- **`--stats` and its two views.** Spec 0045 landed those and rewrote the two examples that
  carried a bare `--stats` into the subcommand form on the way past. The rest of the
  no-subcommand examples were left exactly as they are, which is why this spec exists.
