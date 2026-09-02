# ADR-0026: Split the package into a monitor tower and an analysis tower

- **Status:** Accepted, unbuilt (spec 0068)
- **Date:** 2026-09-02

## Context

The package is one linear stack, and `tests/architecture/test_layering.py`
enforces it: `support`, then `model`, then `exporters` and `stats`, then
`control`, then `monitoring`, then `cli`.

Two kinds of work sit in that stack. One attaches to a live process and writes
a file. The other reads a file gcmon already wrote and reports on it. The
boundary between them is the file itself, and it is not a new idea here: a
capture is what `monitor` produces and what `combine` consumes.

A linear stack cannot say that. `cli` is permitted every layer beneath it, so
a command that only reads a tracefile may import `monitoring`, and nothing
objects. The permission is not hypothetical: specs 0061, 0062 and 0063 add
three commands that read files and nothing else, and they land in `cli` beside
`monitor` and `run`.

`exporters/` already holds both directions. `jsonl_io` and `combine` consume a
file; the Perfetto modules and `jsonl_exporter` produce one. The layer's name
describes half its contents.

## Decision

- The package is two towers over a shared base.
- The base is `support`, `model`, `stats` and `exporters`. Both towers import
  it.
- The monitor tower is `control`, `monitoring`, `pyperf` and `cli.monitor`.
- The analysis tower is `analysis` and `cli.analyze`.
- **Neither tower imports the other.** `cli` itself, meaning `main.py` and the
  two root modules, is the one place both are reachable, because it assembles
  the parser from both.
- `analysis` holds what consumes a file gcmon wrote: `combine`, `jsonl_io`,
  and the tracefile reader spec 0061 adds. `combine` writes a trace as its
  output, and belongs here regardless, because what it reads is a capture.
- `stats` stays in the base. The live statistics table and the offline one are
  the same accumulation, and spec 0061 exists in the shape it does so that
  they cannot drift apart.
- The layer table in `tests/architecture/test_layering.py` carries the towers,
  and `layer_of` answers `cli.monitor` or `cli.analyze` by subdirectory.

## Consequences

- A command's flags and its handler separate. A parser must be importable
  without the tower that serves it, which is what
  [ADR-0027](0027-the-monitor-tower-owns-the-interpreter-floor.md) then rests
  on. `monitoring_options` already had this shape and is the model for the
  rest.
- `gcmon.exporters` stops re-exporting `combine_files` and
  `convert_jsonl_to_trace_format`, and `gcmon` stops re-exporting the
  monitoring layer. Both are public names, and both go.
- The towers' code separates; their tests do not. A monitor-tower test reads
  back what `JsonlExporter` wrote by calling `read_jsonl`, which is
  analysis-tower code. Nothing forbids it, because the layer walk reads `src/`
  only, and one distribution ships both.
- A third tower is now cheap to argue for and expensive to add by accident.
  The table names two, and a directory that belongs to neither has to say
  which it is.

## Alternatives considered

**One stack with two tips**, adding `analysis` as another layer under `cli`.
Rejected because `cli`'s permission to import every layer is exactly the
import the split exists to prevent: an analysis command reaching into
`monitoring` would still pass.

**Two distributions**, one per tower. Rejected. gcmon is pure Python, so
splitting buys no per-platform wheel and no build simplification, and the
layer test is the stronger boundary of the two: it fails on the offending
import, at the line, in the commit that wrote it, where packaging fails at
install time on someone else's machine.

**Splitting `jsonl_io` between the towers**, keeping the write half in
`exporters`. Rejected because there is nothing to split. `JsonlExporter`
serializes through `model.protocol.to_mapping` and never calls `write_jsonl`,
whose only caller is `combine_files`. The module is analysis-side entire.

## Implementation

- `tests/architecture/test_layering.py` holds the table, `FOLDED` and
  `layer_of`.
- `src/gcmon/analysis/`, `src/gcmon/cli/monitor/`, `src/gcmon/cli/analyze/`.
- Spec 0068 is the move itself.
