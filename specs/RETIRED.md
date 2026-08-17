# Retired specs

Every number this folder has used that is no longer open work, and what became of it. A spec
missing from [README.md](README.md) resolves here, so look before calling a reference broken.
[CONVENTIONS.md §Lifecycle](CONVENTIONS.md#lifecycle) governs these rows.

**0029 and 0034 still have files.** The index marked both superseded, but nobody deleted the
file, and each carries a §4 the superseding work summarizes rather than replaces. Either finish
retiring them, since git holds the argument either way, or record here that you keep them on
purpose.

## Retired

| Spec | Kind | Effort | Summary |
|------|------|--------|---------|
| [0029](0029-jsonl-and-stdout-duplicate-the-buffering.md) | **Superseded** by 0036 | M | Three byte-identical copies of the buffer-and-flush logic in `JsonlExporter`. 0036 removes them by collapsing the interface that produced them; §4's JSONL-schema argument still stands |
| [0034](0034-separate-interpreter-confirmation-from-loss-arithmetic.md) | **Superseded** by ADR-0015 | S | Loss spans reached back across a collection gcmon watched start. ADR-0015's rewrite moved the edge to the poll instant, which is later still |
| 0038 | **Landed** 2026-08-17 — cleanup | M | Per-pid state had two owners, each pruning it against the same set; had they disagreed, gcmon would have reported a loss window that never happened. One tick is one call on `EventsMonitor` now. [ADR-0017](../docs/adr/0017-monitor-owns-the-pid-lifecycle.md), and [ADR-0011](../docs/adr/0011-process-lifetime-and-ordering.md) for the liveness site it moved |
| 0046 | **Landed** 2026-08-17 — performance | S | Settling a departed pid rescanned every running ring, so a fan-out exiting together cost the tick that noticed it tens of milliseconds ahead of the polls its surviving siblings were timed against. `StreamingStats.retain` groups the departing keys in one traversal now, both paths through one settling body, under [ADR-0016](../docs/adr/0016-the-ring-is-the-statistics-unit.md). No ADR of its own: it changed how fast rings settle, not what a settled ring means. Its open question, whether `_running_rings` should be keyed structurally, is [0039](0039-split-the-record-model-and-stats-by-concern.md) §4.6's now |

The two §4s still worth reading:

- **0034 §4** argues why a temporal bound differs from the clipping ADR-0015 rejected. Read it
  before proposing a narrower span.
- **0029 §4** is the fullest statement of why the JSONL schema is load-bearing; 0036 summarizes
  it rather than replacing it.

## Gaps

A number in neither table: from 0033 on every number became a file, so a gap there is a lost row
and git has the text. Below that, **0022**, **0023** and **0032** never became files and nothing
records what they were. 0018, 0019 and 0021 sit in [Provenance](#provenance).

**Two numbers got used twice before anyone wrote the never-reuse rule down**, both reclaimed by
the batch in `4731e50` from specs that had landed days earlier. A reference from before
2026-08-15 resolves against the first column:

| Number | First held by | Reused for |
|---|---|---|
| 0035 | `end-of-run-summary-says-what-the-capture-is-worth`, deleted in `8431858` | `derive-every-gc-sub-phase-from-one-table` |
| 0036 | `statistics-report-the-ring-not-the-process`, deleted in `7f87d32` | `one-exporter-method-per-record-kind` |

They stay. Renumbering nine files to repair two would break the other half of the same rule, and
nothing outside this folder cites either number.

## Provenance

This folder takes its conventions from the sibling `gcscope` repo. Before 2026-08-05 it held six
files in three header formats, sat behind a `.gitignore` exclusion, and carried a template from
an unrelated web project (JWT examples, a "Definition of Done" checklist, an "AI Implementation
Plan" section telling an agent to execute step 1 and await confirmation). Git tracks it now, so
the retire-the-file rule has somewhere to delete to.

On 2026-08-05 all six went back against the code, and three retired. Their outcomes predate the
keep-the-row rule and run too long for a table cell:

| Old spec | Outcome |
|---|---|
| 18 — post-v0.2.0 review fixes (15 REQs) | Split. REQ-1 is **obsolete**: `EventsMonitor` has no `_last_ts` at all now, working from per-pid `_cursors` keyed on the `collections` counter ([ADR-0015](../docs/adr/0015-gc-loss-spans-on-their-own-track.md)). REQ-2 landed as `TestMetaDedupRaceClosed`. REQ-13 lost on the record: graceful degradation without `psutil` is a documented, tested property. The remaining twelve became 0025–0030. |
| 19 — README update for v0.2.0 | Landed but for one item. The `combine`, `ControlClient`, Perfetto-SQL and de-duplicated "How It Works" sections are all in the README; the `## Optional Dependencies` heading is moot, since `## Installation` covers both extras with links and the graceful-degradation note. The remainder became 0031. |
| 21 — monitor-reported process liveness | **Landed** 2026-08-02. `EventsMonitor` calls `add_process_liveness` once per tick, `PerfettoExporter` overrides it, and the provisional counter carve-out is gone. [ADR-0011](../docs/adr/0011-process-lifetime-and-ordering.md) records it; 0038 later moved the call from `MonitorLoop` into the monitor. |

20 and 24 survived and keep their numbers, both rewritten into the templates. 20 also settled a
decision it had left open: its §4 says why the monitoring process's own `gc.get_threshold()`
cannot serve as a source for the target's thresholds.

One correction, worth stating because the old spec asserted the opposite: REQ-4 proposed moving
`JsonlExporter` onto `BufferedTraceExporter` with a `JsonlEventEncoder`. That would have
rewritten the JSONL schema and broken the combine reader in the same commit, since the schema is
public, documented per-field in [docs/formats.md](../docs/formats.md#jsonl-output), and
`chrome_trace_io.read_jsonl` reads it back to drive `gcmon combine`. 0029 shared the buffering
instead; 0036 carries that constraint now.
