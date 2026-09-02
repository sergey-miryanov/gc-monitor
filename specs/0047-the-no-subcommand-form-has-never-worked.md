# 0047: Require a subcommand

- **Status:** Not started
- **Kind:** bug (reporting)
- **Effort:** XS
- **Origin:** spec 0045 section 7, 2026-08-17, which fixed the two documented
  examples it had to touch and deliberately left the question open
- **Respects:** none

## 1. Problem

`README.md` opens its Quick Start with `gcmon 12345`, and `docs/cli.md` says
that `gcmon` without a subcommand monitors. Every documented invocation of
that form exits 2 with a usage message, so the operator's first command,
copied from the top of the README, fails. `gcmon` on its own is worse: it
prints an `AttributeError` traceback and exits 1, which is what someone typing
the bare name to find out what the tool does gets back.

## 2. Evidence

`gcmon.cli.main._create_parser` builds a parser carrying `--version`, three
subcommand choices and no positional of its own, so `12345` is rejected as an
invalid choice before any of gcmon's code runs.

`gcmon.cli.main.main` has a branch for `args.command is None` that
re-dispatches to `monitor`, and it is unreachable by two separate routes. With
an argument, `parse_args` has already exited by the time it could be read.
With none, `parse_args` succeeds and `main` reaches
`_setup_logging(args.verbose)` first: the top-level parser defines no `-v`,
only the three subparsers do, so it raises
`AttributeError: 'Namespace' object has no attribute 'verbose'` before either
branch is read.

The branch below the fallback, which logs `Unknown command`, is unreachable
for the reason its own comment gives: argparse rejects a word that is not a
choice. All three subparsers call `set_defaults(func=...)`, so
`hasattr(args, "func")` is the whole of dispatch and everything after it is
dead.

Nothing in the suite covers either form. `tests/test_cli.py::TestCliHelp` asks
each subcommand for its help, and every other CLI test names a subcommand.

## 3. Scope

**Affected:** the subcommand group in `_create_parser`, the two unreachable
branches at the end of `main`, and the nine documented examples that omit the
subcommand, in `README.md` (Quick Start), `docs/cli.md` ("What you'll see" and
"monitor") and `docs/rss.md`.

**Not affected:** `gcmon monitor <pid>`, `gcmon run` and `gcmon combine`, and
`gcmon --version`, which acts during parsing and exits before the subcommand
requirement is checked. Every documented invocation that names its subcommand
works.

**Why the suite didn't catch it:** no test runs `main` without a subcommand,
in either spelling.

## 4. Proposed change

`gcmon` requires a subcommand, and the documentation stops offering a form
that has never run.

1. `_create_parser` passes `required=True` to `add_subparsers`. `gcmon` alone
   then exits 2 with `the following arguments are required: command`, and
   `gcmon 12345` keeps the invalid-choice message it prints today.
2. Delete both dead branches at the end of `main`: the `args.command is None`
   fallback and the `Unknown command` log below it. `hasattr(args, "func")` is
   left as the whole of dispatch, and `args.verbose` is always present because
   every subparser defines `-v`.
3. Rewrite the nine examples to name `monitor`, and delete "Without one it
   monitors" from `docs/cli.md`. That file prints `gcmon 12345` and
   `gcmon monitor 12345` on consecutive lines under "monitor"; the pair
   collapses to one.

**Rejected: make the bare form work.** `main` would detect a leading token
that is not a subcommand choice, an all-digit pid, and insert `monitor` before
it. It costs a parser that guesses at its first argument, and it buys a
shortcut for one subcommand out of three.
[ADR-0018](../docs/adr/0018-stats-requires-a-view-and-keeps-no-bare-alias.md)
is the precedent: `--stats` lost its bare spelling rather than gaining an
alias, so that the source and the docs carry one spelling each. **What would
reopen it:** an operator asking for the shortcut who is not reading this repo.

**Rejected: print help and exit 0 on a bare `gcmon`.** Friendlier, but it
needs an explicit branch, which is the shape of the code being deleted here,
and it answers a bare `gcmon` differently from `gcmon 12345`. Exit 2 with the
usage line keeps one answer to "you did not name a subcommand".

**No alias, unlike [0050](0050-name-the-poll-interval-for-what-it-is.md).**
That spec keeps `--rate` working because command lines in the wild carry it.
This form has never worked, so there is nothing in the wild to keep.

## 5. Seams and testing decisions

- **Seam:** `gcmon.cli.main.main` with an argv list, which is where the
  dispatch decision is made and the only place either branch can be observed.
- **New seam needed:** none.
- **What makes a good test here:** assert the exit status the operator sees,
  `pytest.raises(SystemExit)` with `code == 2`, for both spellings. Reading
  `main`'s source to assert the fallback is gone proves the code changed and
  not what it does.
- **Prior art:** `tests/test_cli.py::test_main_combine_command` for dispatch
  through `main`, and `TestCliHelp` in the same file for a parser that exits.
- **Cases:**
  1. `main([])` exits 2, and the message names the three subcommands.
  2. `main(["12345"])`, the form the README's Quick Start prints today,
     exits 2.
  3. Regression guard: `monitor`, `run` and `combine` dispatch as they do now,
     and `--version` still exits 0.

## 6. Out of scope

- **`--rate` in `docs/cli.md`.** One of the rewritten examples carries it.
  This spec puts `monitor` in front of it and leaves the option name to
  [0050](0050-name-the-poll-interval-for-what-it-is.md).
- **A deprecation cycle for the bare form.** There is nothing to deprecate: no
  release has ever accepted it.
- **`--stats` and its two views.** Spec 0045 landed those and rewrote the two
  examples that carried a bare `--stats` into the subcommand form on the way
  past. The rest of the no-subcommand examples were left exactly as they are,
  which is why this spec exists.

## 7. Further notes

The `CHANGELOG.md` entry belongs under bug fixes rather than breaking changes.
Nothing that worked stops working: the form being removed exits 2 today and
exits 2 after, and what changes for an operator is that `gcmon` alone prints a
usage line instead of a traceback.

Worth an ADR when it lands, shaped like ADR-0018, so that the next person to
propose a shortcut form finds the reasoning rather than the `add_subparsers`
call.
