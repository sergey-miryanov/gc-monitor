# 0043 — Report one version, from one source

- **Status:** Not started
- **Kind:** bug — reporting
- **Effort:** XS
- **Origin:** noticed while installing gcmon into a 3.15 venv, 2026-08-16
- **Respects:** [ADR-0001](../docs/adr/0001-hand-rolled-perfetto-protobuf-encoder.md) (gcmon
  adds a dependency only when it has to) — no ADR covers versioning

## 1. Problem

`gcmon.__version__` reports `0.1.0`. The distribution is `0.5.0`: the literal was last correct
at `0.1.0`, and gcmon has released five times since — `0.2.0`, `0.3.0`, `0.3.1`, `0.4.0`,
`0.5.0`. Anyone who imports gcmon to record which version produced a capture, or who quotes
`gcmon.__version__` in a bug report, gets a number that was last correct before any of the
current trace format existed — the loss track, the `Processes` track, RSS sampling and the
Perfetto backend all landed after `0.1.0`. `importlib.metadata.version("gcmon")` on the same
installed package answers `0.5.0`, so the two disagree inside one interpreter.

There is no way for an operator to notice from the command line, because there is no way for an
operator to *ask*: `gcmon` has no `--version` flag.

## 2. Evidence

Two version strings, one maintained and one not:

- `gcmon.__version__` — a literal in the package's `__init__`, exported in its `__all__`,
  `"0.1.0"`.
- `[tool.poetry] version` in `pyproject.toml` — `"0.5.0"`, which is what the wheel carries and
  what `importlib.metadata` reports.

Nothing reads `__version__`. A search across `src/`, `tests/` and `.github/` returns its
definition and its `__all__` entry, and nothing else — no CLI flag, no trace annotation, no
pyperf metadata key, no test. That is why five releases went by without anyone noticing.

Nothing checks it either. `.github/scripts/extract_changelog.py` reads
`pyproject.toml["tool"]["poetry"]["version"]` and requires a matching `## Version X.Y.Z` header
in `CHANGELOG.md`, so the release does verify that two of the three strings agree. `__init__` is
not one of them.

The release checklist in [docs/RELEASE.md](../docs/RELEASE.md) says "Bump `version` in
`pyproject.toml`" — one instruction, naming one of the two places a version lives. The checklist
is not a contributing factor to the drift; it *is* the drift, written down.

## 3. Scope

**Affected:** `gcmon.__version__`, on every install and every platform, since `0.2.0`.

**Not affected:** the built distribution's metadata, PyPI, the git tags, the GitHub Release
bodies, and the changelog — all four derive from `pyproject.toml` and are correct. Nothing in a
trace carries a gcmon version today, so no capture is mislabelled. `pip show gcmon` is right.

**Why the suite didn't catch it:** no test imports `__version__`. There was nothing to catch it
with, and no consumer whose output would have looked wrong.

## 4. Proposed change

1. **Delete the literal and derive `__version__` from the installed metadata**, so the
   duplication is gone rather than guarded:

   ```python
   try:
       __version__ = importlib.metadata.version("gcmon")
   except importlib.metadata.PackageNotFoundError:
       __version__ = "0.0.0+unknown"   # a source tree with no install
   ```

   `pyproject.toml` becomes the single source. It is the right one: the versioning policy in
   RELEASE.md already makes it authoritative by requiring the tag to match it exactly.

2. **Add `gcmon --version`**, printing the same string. This is the reason the defect survived
   five releases — the number was unobservable from outside — and it is what lets the fix be
   tested at the CLI rather than by asserting a module attribute against itself.

3. **Update [docs/RELEASE.md](../docs/RELEASE.md).** The checklist's first item stays one step,
   and gains a clause saying `gcmon.__version__` follows from it with nothing to bump. Add a
   line to the Versioning policy section stating that `pyproject.toml` is the single source and
   that the package attribute is derived — the fact a future contributor needs before they
   consider adding a second literal back.

4. **Bump nothing.** This ships in whatever release it lands in; `0.1.0` was never a version of
   this code and there is no history to preserve.

**Rejected: keep the literal and have the release workflow check it matches**, alongside the
changelog check it already runs. It is cheaper and it is the shape the repo already has, but it
leaves two strings and adds a third thing to the release path to keep them equal. Deriving one
from the other removes the failure mode instead of detecting it.

**Rejected: make `pyproject.toml` read the version out of the package.** Poetry needs a plugin
for a dynamic version, which is a build-time dependency for a two-line problem.

**Accepted cost — a stale editable install reports the old number.** Bump `pyproject.toml` in a
dev tree without reinstalling and `__version__` reports what the metadata still says. That is
the same answer `pip show` gives and it is honest about what is installed; the alternative is a
literal that is wrong for everyone rather than briefly stale for one developer. Say so in
RELEASE.md.

**Accepted cost — the `0.0.0+unknown` fallback.** RELEASE.md's versioning policy forbids
`+local` suffixes on a *release*; this is the string for something that is not a release and
cannot be one. It only appears when gcmon is imported from a checkout with no install at all.

## 5. Seams and testing decisions

- **Seam:** `tests/test_cli.py`, through `gcmon --version` — the highest seam available once
  step 2 exists, and the reason to do step 2. Without it the only seam is the module attribute,
  and a test that compares `__version__` to `importlib.metadata.version("gcmon")` is comparing
  the implementation to itself.
- **New seam needed:** none. `--version` is a new flag on an existing parser, covered by the
  existing CLI tests.
- **What makes a good test here:** assert the CLI's output equals the *installed distribution's*
  version, read independently. Do **not** hardcode `0.5.0` — the test would then need editing
  at every release, which is one more thing to forget and the same class of mistake as the
  original. Do not assert against `gcmon.__version__` either; that is the value under test.
- **Prior art:** `tests/test_cli.py` for driving `main()` and capturing output;
  `tests/test_extract_changelog.py`, which is the existing test for the other half of the
  version machinery and reads `pyproject.toml` rather than hardcoding a number.
- **Cases:**
  1. `gcmon --version` prints the installed distribution's version and exits 0.
  2. `gcmon.__version__` equals it.
  3. The fallback does not fire under a normal install — `__version__` is not
     `0.0.0+unknown` when the tests run.
  4. Regression guard: `python .github/scripts/extract_changelog.py` still resolves the version
     from `pyproject.toml` and finds its changelog section. This change must not touch the
     release path's own version resolution.

## 6. Out of scope

- Recording gcmon's own version in a trace. It is the natural next consumer and
  [0020](0020-process-metadata-in-perfetto-traces.md) owns the mechanism — that spec adds the
  *target's* Python version and GC thresholds as process-track annotations, and gcmon's version
  belongs beside them, as one more annotation and one more decision about what a trace claims.
  Fixing the number first is what makes that worth doing.
- Reporting a version in the pyperf hook's metadata.
- Any change to the release workflow, the tag format, or the changelog format.
- The versioning policy itself. SemVer stays SemVer; this spec documents where the number lives,
  not what it means.
- Whether `0.1.0` should have been `0.5.0` in any published artifact. Nothing published is
  wrong — the drift never left the source tree.

## 7. Further notes

Worth doing early despite being XS: every spec in the 0035–0042 set moves code, and a release
cut in the middle of that work is the first time in five releases that someone is likely to look
at `__version__` and believe it.
