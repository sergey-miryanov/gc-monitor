# gcmon documentation

The [README](../README.md) covers what gcmon is, how it compares to the
alternatives, what it cannot do, and how to get a first trace. These pages cover
everything after that decision: how to drive the CLI, what comes out of it, and
how to read the result.

## Using gcmon

| Page | What it covers |
|---|---|
| [monitoring.md](monitoring.md) | How gcmon reads a process, why it misses collections, and what it recovers — the basis for the pages below |
| [cli.md](cli.md) | Subcommands (`monitor`, `run`, `combine`), every option, and the environment variables that back them |
| [formats.md](formats.md) | The four `--format` values, what a Chrome or Perfetto trace contains, and the JSONL event schema |
| [statistics.md](statistics.md) | The `--stats` table, how to read it, and the `[stats]` extra |
| [rss.md](rss.md) | RSS tracking, its sampling behaviour, and the `[cmdline]` extra |
| [pyperf.md](pyperf.md) | The pyperf hook, the metrics it emits, and its environment variables |
| [control-plane.md](control-plane.md) | Starting, stopping, and annotating monitoring from inside your application |
| [perfetto-sql.md](perfetto-sql.md) | The trace schema and example PerfettoSQL queries |

## Developing gcmon

| Page | What it covers |
|---|---|
| [adr/README.md](adr/README.md) | Architecture decision records — why the design looks the way it does |
| [../specs/README.md](../specs/README.md) | Open work — what has been specified but not yet built, and the conventions a spec follows |
| [agents/](agents/issue-tracker.md) | How agent skills work in this repo — issue tracker, triage labels, domain docs |
| [RELEASE.md](RELEASE.md) | The release process |
