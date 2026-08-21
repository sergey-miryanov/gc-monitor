# 0041: Give the package explicit layers, and a test that keeps them

- **Status:** Not started
- **Kind:** feature (cleanup)
- **Effort:** L
- **Origin:** code structure review of `src/gcmon`, 2026-08-15
- **Respects:** every ADR that anchors on a module path; see section 4.4, which is most of the work

## 1. Problem statement

`src/gcmon` is nineteen modules in one flat namespace, and they belong to seven different
layers: the record model, output, stats, control, monitoring, the pyperf hook, and the
command line. A reader cannot tell from the listing that `trace_event` is a data model and
`rss_sampler` is a driver, or that `protocol` is at the bottom of everything and `commands` at
the top.

The layering itself is not broken; it is invisible. Walking the intra-package imports and
ordering them as section 4.1 does yields **zero** upward imports today: the dependency direction is
already clean. Nothing states that, nothing checks it, and the first edit that reaches upward
will land without comment. That is the cost: a property the codebase currently has, held by
nobody, documented nowhere, and one import away from being lost quietly.

## 2. Solution

No behaviour change of any kind. What changes is that the directory listing states the
architecture, and a test fails when an import crosses a layer the wrong way, so the property
the codebase has today is one it keeps.

## 3. User stories

1. As someone reading gcmon for the first time, I want the directory names to tell me what the
   layers are, so that I can find the monitoring code without opening nineteen files.
2. As a maintainer adding a module, I want the directory I put it in to determine what it may
   import, so that placement is a decision rather than a habit.
3. As a maintainer who accidentally imports the CLI from the record model, I want a test
   failure naming both modules, so that the mistake costs a minute and not a release.
4. As a reviewer, I want a dependency inversion to show up as a failing test rather than as
   something I have to notice, so that review attention goes to the change itself.
5. As a maintainer of the record model, I want it to import nothing else in gcmon, so that it
   stays the thing everything can depend on.
6. As a maintainer reading an ADR's implementation notes, I want the module paths it names to
   exist, so that a record stays checkable against the code.
7. As an operator, I want this to be invisible, so that nothing about my traces, my flags or my
   output changes.

## 4. Implementation decisions

### 4.1 Seven layers plus a support leaf, as a partial order rather than a ranking

The permitted edges, verified against the graph the package has today:

| Layer | Holds | May import |
|---|---|---|
| support | the signal, termination and exit helpers | nothing in gcmon |
| model | the record structs, the structural protocols, the phase table, units, the loss accumulator, the trace-event union, the poll status, the tick grid, the run report | support |
| exporters | the exporters, encoders and converters | model, support |
| stats | the stats accumulator, the streaming aggregation, the stats table | model, support |
| control | the parent-side server and child-side client | model, exporters, support |
| monitoring | the monitor, the loop, the events reader, the process handles, the wait and run policies, the RSS sampler | model, exporters, stats, control, support |
| pyperf | the pyperf hook | everything below |
| cli | the entry point, the subcommands, the environment defaults | everything below |

The layer is the directory, with one exception. `utils` becomes `support`, the only rename in
this change; `exporters`, `control`, `commands` and `pyperf` keep their names, which is why two
layers are named for a directory rather than for an abstraction over it; `model`, `stats` and
`monitoring` are new. The exception is `cli`: it is the package root, where `__init__.py` and
`__main__.py` have to live, plus `commands`. Making the root the top layer also makes the
re-export shims of 4.3 legal without an exclusion rule, since a shim imports downward.

Two edges are worth stating because they look wrong and are not: `monitoring` imports
`exporters` because the monitor writes through an exporter, and `control` imports `exporters`
because the control server turns a child's message into an instant event. Both are downward.

`exporters` and `stats` have no edge between them in either direction. They are siblings, not a
sequence, which is why this is a partial order: a numeric rank would invent an ordering the
code does not have and would make one of the two look subordinate.

Four permitted edges are unused. Nothing in `model`, `exporters`, `stats` or `control` imports
`support` today; only `monitoring`, `pyperf` and `cli` do. The column states what a layer
may import, not what it does, so the test passes either way. Narrowing the column to the edges
that exist would keep the termination and signal helpers out of the bottom four layers, and
costs a rule to relax the first time one of those layers needs one.

### 4.2 The test is the point; the directories are how it stays readable

Two pieces. A walk over `src/gcmon/**/*.py` reads each file with `ast` and returns the
intra-package import graph: the importing module, the imported module, the line. A check takes
that graph, the layer of each module by path, and the table of allowed edges, and returns the
imports that cross a layer the wrong way. One test feeds the real package to both and expects
nothing back.

The split is what makes the failure cases testable. The real tree holds no violation, so cases 2
and 3 of section 5 hand the check a graph written in the test rather than a tree of fake modules
on disk. It also keeps the `ast` work out of the assertion, where the rule should read as the
rule.

The walk parses rather than imports, so it is fast, has no import side effects, and needs no
optional dependency installed. The test carries a `layering` marker so it can be run alone,
and that marker must **never** join the deselect list in `addopts`: the three markers there
today (`stress`, `benchmark`, `fuzz`) are all deselected by default, and a deselected test
catches nothing ([ADR-0014](../docs/adr/0014-perfetto-integration-test-strategy.md) made that
mistake once). The failure message names the importing module, the imported module and the edge
that is not allowed.

The table of allowed edges lives in the test, not in the source. It is a statement about the
architecture and the test is where a statement about the architecture can fail.

### 4.3 Public imports keep working

`gcmon/__init__.py`'s `__all__` is the public surface and every name in it stays importable
from `gcmon` directly. Deeper paths that tests and downstream code use today (`gcmon.data`,
`gcmon.exporters.exporter`, `gcmon.protocol`) keep working through re-export shims for one
release, then go. That is the same treatment
[0039](0039-split-the-record-model-and-stats-by-concern.md) gives its moves.

### 4.4 The documentation is most of the work, and the effort estimate says so

The ADRs anchor on module paths deliberately: the ADR README requires it, and requires amending
a record when a name it anchors on moves. Eighteen ADRs carry **79** lines naming `src/gcmon/…`
paths, led by ADR-0011 with ten and ADR-0013 with seven, counted 2026-08-21. Every one of those
is an amendment in this change, not a follow-up. A reshuffle that lands with the ADRs still
pointing at the old paths has made the records worse, which is a larger loss than the layout is
a gain.

**Rejected: the test without the directories.** It would work: the layers can be a mapping from
module name to layer in the test file. It leaves the architecture legible only to someone who
opens the test, which is the situation this spec exists to end.

**Rejected: the directories without the test.** The layering already holds by accident; making
it visible without making it checkable protects it for exactly as long as everyone remembers.

**Rejected: a lint plugin (import-linter or similar).** One more dependency and one more
configuration format to hold a rule that is thirty lines of `ast` and expressible in the
project's own vocabulary. gcmon hand-rolled a protobuf encoder to avoid a dependency
([ADR-0001](../docs/adr/0001-hand-rolled-perfetto-protobuf-encoder.md)); a dependency for this
would be out of character.

**Rejected for now: a public surface per layer.** The version with real depth: each layer
directory gets an `__init__.py` naming what it exports, and the test asserts a crossing goes
through that rather than into a sibling's submodule. Seven directories become seven modules with
interfaces. It forces a public/private split on all seven in one change, and needs a second rule
and a second failure message against 4.2's one. Worth revisiting once the layers have held long
enough to show where each surface belongs.

**Open, to settle when picked up:** whether `pyperf` is a layer of its own or whether the hook
simply sits at the CLI level. It is the only member and it imports from five layers below, which
is the CLI's profile. Settled by whether a second integration ever appears.

## 5. Seams and testing decisions

- **Seam:** a new one, the module graph, read with `ast`. Nothing existing can observe an
  import direction: the suite exercises behaviour, and every arrangement of these modules
  produces the same behaviour. This is the case the conventions leave room for, and it is placed
  as high as it can be, at the package rather than at any module.
- **New seam needed:** yes, and this is the spec's one exception to "prefer an existing seam".
  It is a single test file with no fixtures, no imports of the package under test, and no
  optional dependencies. Nothing else in the repo grows a seam.
- **What makes a good test here:** assert the *edge*, not the file layout. A test that lists the
  expected directories would fail on any future reorganization for no reason; a test that
  asserts `model` never imports `cli` keeps meaning the same thing however the files move.
  Failure output must name both modules and the disallowed edge; a bare "layering violation"
  costs more to diagnose than the rule saves.
- **Prior art:** none in this repo for a structural test. The closest in spirit is
  [0028](0028-combined-exporter-reaches-into-sub-exporter-privates.md)'s walk over
  `EventsExporter.__subclasses__()` to assert every exporter answers `output_path`, a test
  about the shape of the code rather than about an output, discovered rather than listed.
- **Cases:**
  1. The current tree passes with zero violations. That is the day-one state and the test's
     first job is to record it.
  2. A deliberately added upward import (the record model importing the CLI) fails with both
     module names in the message.
  3. A sideways import between `exporters` and `stats` fails, since they have no edge.
  4. Regression guard: the full suite passes with only import lines changed, and `gcmon run`
     over a fixture produces byte-identical output on all five formats.

## 6. Out of scope

- Splitting any module. [0039](0039-split-the-record-model-and-stats-by-concern.md) splits the
  record model and the stats module by concern; this places the results. **0041 lands first**:
  the layering test then exists before 0039 splits anything, so its new modules are placed by a
  rule that fails when they land wrong. The cost is that the record model and the stats module
  move twice, and the ADR path lines naming them are amended twice.
- Changing what any module does. Every violation this could surface is hypothetical: there are
  none today.
- The `stats_output` dependency in the environment-defaults module, which imports it for the
  table-format enum. Under these edges it is legal (the CLI may import stats) so this
  spec's test would not flag it. Worth noting because it is the one place an option's type
  lives in a presentation module, and worth leaving alone until something makes it matter.
- Any public API change beyond the re-export shims in section 4.3.
- Enforcing anything else with the same test: no cycle detection, no fan-out limits, no
  file-size rules. One rule, one failure message.
- A new ADR. The layering is argued here and enforced by the test; `docs/adr` gets the path
  amendments of 4.4 and nothing else.

## 7. Further notes

The honest case for this spec is weaker than for the others in this set, and it should be
picked up last. It fixes nothing; it makes an existing property explicit and adds a guard.
Its cost is real and mostly documentary: 79 ADR lines, every import line in the package, and a
permanent discontinuity in `git blame`. Worth doing when the package is otherwise settled; not
worth doing between two of the changes that actually move code.
