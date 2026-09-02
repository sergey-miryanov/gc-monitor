# ADR-0027: The monitor tower owns the interpreter floor

- **Status:** Accepted, unbuilt (spec 0068)
- **Date:** 2026-09-02

## Context

`requires-python` read `>=3.15`, and the reason is one import.
`monitoring/events_reader.py` and `monitoring/monitor.py` take `GCMonitor` and
`get_child_pids` from `_remote_debugging`, a private stdlib module that 3.15
provides and no earlier release does. gcmon also has to run the same
interpreter as the process it reads, because the GC layout it walks is that
interpreter's ([ADR-0020](0020-attach-to-a-process-once.md)).

Nothing else in the package needs any of it. Compiling `model`, `exporters`,
`stats` and `support` succeeds on 3.12, 3.13 and 3.14. On 3.11 four modules
fail, all of them on PEP 695 `type` statements, which puts the syntax floor at
3.12. `msgspec` declares `>=3.10`.

`requires-python` is per-distribution metadata, and an optional dependency
group cannot relax it. A single floor of `>=3.15` therefore made the analysis
tower installable only on an interpreter that was in beta when this was
written, to run work that requires nothing of it: reading a file. The
consumers that exist run 3.13, and their scientific stack has no 3.15 wheels.

[ADR-0001](0001-hand-rolled-perfetto-protobuf-encoder.md) argued the runtime
dependency tree from gcmon being "installable next to the process it watches".
That is true of the pyperf hook, which runs inside the target
([ADR-0023](0023-the-pyperf-hook-annotates-and-does-not-drive.md)). It is not
true of `monitor` or `run`, which read the target from outside and share
nothing with it but the interpreter version.

## Decision

- `requires-python` is `>=3.13`. It states the analysis tower's floor, because
  that is the floor of the distribution as a whole.
- 3.15 is a runtime requirement of the monitor tower
  ([ADR-0026](0026-two-towers-over-a-shared-base.md)), enforced where it is
  real: importing `monitoring` on an older interpreter fails, because
  `_remote_debugging` is not there.
- `cli/main.py` registers the monitor tower's subcommands through an
  `ImportError` guard, and registers a stub for each when the import fails.
- **The help text is identical on every interpreter.** `monitor` and `run`
  carry `(requires Python 3.15+)` in their descriptions on 3.15 as well as on
  3.13. Running one where the tower is absent exits non-zero with a message
  naming the requirement and the interpreter in hand.
- The floor is 3.13 rather than the 3.12 the syntax allows, because 3.13 is
  what the analysis consumers run. Lowering a floor later is not a breaking
  change; raising one is.
- CI runs the base and the analysis tower on the floor, so that a 3.15-only
  construct in either fails on the commit that introduces it.

## Consequences

- Every dependency the analysis tower takes has to support the floor, the
  `analysis` extra's `perfetto` included.
- `gcmon.__init__` cannot re-export the monitoring layer, since importing the
  package has to work on the floor.
- The two towers' commands fail differently and deliberately. A missing
  optional dependency leaves the command present and every other command
  working (spec 0061); a missing interpreter feature leaves the command
  present and failing with the version in the message. Neither removes a
  subcommand from the help.
- ADR-0001's context is amended by this record: its dependency argument holds
  for the hook and for nothing else gcmon ships.

## Alternatives considered

**Keep `>=3.15` and move the consumers up.** Rejected. It makes the analysis
tower less usable than the code it replaces, for as long as its dependencies
have no wheels for the interpreter, and that interval is not gcmon's to
shorten.

**Set the floor at 3.12, the syntax floor.** Rejected. A floor is a promise
kept in CI for every dependency the tower ever takes, and 3.12 has no consumer
behind it. Dropping to it later costs a patch release and breaks nobody.

**Two distributions with a floor each.** Rejected in
[ADR-0026](0026-two-towers-over-a-shared-base.md).

**Leave `monitor` and `run` out of the parser below 3.15.** Rejected. argparse
answers an unregistered subcommand with `invalid choice`, which reads as a
broken install rather than an old interpreter, and a help text that differs
between machines can be documented truthfully for only one of them.

## Implementation

- `pyproject.toml`: `requires-python`, and the `analysis` extra.
- `cli/main.py`: the guard and the stubs.
- `monitoring/events_reader.py` and `monitoring/monitor.py`: the imports that
  are the requirement.
- The CI workflow's floor job.
- Spec 0068.
