# Specs

One file per unit of *forward-looking* work: identified, understood and specified, but not yet
built. A spec states the problem, the evidence, the proposed change, and the seam you will test
it through. It does not record a decision.

It complements [`docs/adr/`](../docs/adr/README.md), which records decisions already taken and is
the authority on why the design looks as it does. If a spec here contradicts an ADR, assume the
spec is wrong.

This file holds the open set and the order to take it in. The other two:

- [CONVENTIONS.md](CONVENTIONS.md): how to write a spec, the templates, how to retire one.
- [RETIRED.md](RETIRED.md): every number that no longer has a file, and what became of it.

## Open specs

| Spec | Kind | Effort | Summary |
|------|------|--------|---------|
| [0020](0020-process-metadata-in-perfetto-traces.md) | Feature — enhancement | M | A trace does not say which Python ran it or what GC thresholds it used; gcmon logs both to stderr and loses them |
| [0024](0024-cpython-report-remote-readable-gc-stats.md) | Report — upstream | S | Five findings on `_remote_debugging.get_gc_stats` to file upstream with CPython; no gcmon change |
| [0025](0025-control-server-accept-loop-survives-transient-errors.md) | Bug — **availability** | XS | One transient accept error and the control server refuses every later connection, saying nothing |
| [0026](0026-one-process-name-across-live-and-offline-paths.md) | Bug — correctness | XS | A live capture names a process `Process 12345`; combined from JSONL, the same process comes out `12345` |
| [0027](0027-thread-descriptor-tid-for-interpreter-zero.md) | Bug — reporting | XS | The main interpreter's `thread.tid` is the pid, so a SQL query has to special-case interpreter zero to read ids |
| [0028](0028-combined-exporter-reaches-into-sub-exporter-privates.md) | Feature — cleanup | XS | `chrome+perfetto` works only by reading a private attribute, with the type checkers told not to look |
| [0030](0030-exporter-hygiene-batch.md) | Feature — cleanup | S | Six one-file hazards in the exporter package: rank dict, `getattr` probe, builtin shadow, two undocumented threading contracts, duplicated validation |
| [0031](0031-readme-output-example-is-labelled-chrome-only.md) | Bug — cosmetic | XS | The README heads its only trace example "Chrome Trace Output" and captions it as the Perfetto UI |
| [0033](0033-loss-counter-track.md) | Feature — enhancement | S | The loss row shows where gcmon went blind but not how much it missed; a bar losing 1 record looks like one losing 40 |
| [0035](0035-derive-every-gc-sub-phase-from-one-table.md) | Feature — cleanup | L | gcmon writes CPython's eight optional GC sub-phases out by hand in six places; adding the ninth means six edits, and nothing fails if you miss one |
| [0036](0036-one-exporter-method-per-record-kind.md) | Feature — cleanup | M | `EventsExporter` has grown one method per record kind, three of them no-ops, and the CLI keeps a hand-maintained list of which formats handle RSS at all |
| [0037](0037-one-meta-emission-path-for-live-and-combined-traces.md) | Feature — cleanup | M | Two implementations of "emit this pid's process and thread meta"; 0026 exists because they already drifted once |
| [0039](0039-split-the-record-model-and-stats-by-concern.md) | Feature — cleanup | S | The record model and the stats module carry three jobs each; `tests/stats/` already splits along a seam the source does not have |
| [0040](0040-derive-the-monitoring-options-from-one-table.md) | Feature — cleanup | M | gcmon declares every monitoring option three times, and echoes a rejected configuration to the log as though it had accepted it |
| [0041](0041-give-the-package-explicit-layers.md) | Feature — cleanup | L | The package's five layers are invisible and unchecked; the dependency direction is clean today and nothing keeps it that way |
| [0042](0042-name-the-process-session-for-its-role.md) | Feature — cleanup | S | The monitored-process seam carries the name of a role it does not fill, and its two adapters do not have the same shape |
| [0043](0043-report-one-version-from-one-source.md) | Bug — reporting | XS | `gcmon.__version__` says `0.1.0` against a `0.5.0` distribution; nothing reads it, nothing checks it, and there is no `--version` to ask |
| [0044](0044-torn-reads-and-reordered-publishes.md) | Bug — correctness | S | **Blocked on upstream.** A pause slice can read one inter-collection interval too long, and a hole inside one poll's records reaches no loss window; both are races in the target that every filter gcmon has passes |
| [0045](0045-print-the-statistics-table-at-two-widths.md) | Feature — ergonomics | S | `--stats` prints one table with no way to ask for less; on a single-interpreter run half of it is a copy of the other half |
| [0046](0046-settle-a-departed-fan-out-in-one-pass.md) | Bug — performance | S | Settling a departed pid rescans every running ring, so a fan-out that exits together costs a tick tens of milliseconds and may draw loss on its surviving siblings |
| [0049](0049-a-recycled-pid-can-be-read-through-a-stale-attachment.md) | Bug — correctness | M | A pid recycled between two polls with no failed read in between is still read through the attachment gcmon holds, so an unrelated process's memory reaches the trace as records that pass every filter |

Every row here has a file. A missing number either retired or never became one;
[RETIRED.md](RETIRED.md) says which.

## Suggested order

| # | Spec | Why here |
|---|------|----------|
| 1 | 0025 | The only outage, and the fix is one word |
| 2 | 0026 | Smallest user-visible wrongness |
| 3 | 0043 | XS, and a release is when someone believes the wrong version |
| 4 | 0049 | The only one whose output is indistinguishable from a measurement, so nobody will report it |
| 5 | 0028 | XS, and it shrinks 0036 |
| 6 | 0027 | Needs an answer from trace-processor before anyone can settle it either way |
| 7 | 0031 | |
| 8 | 0030 | |
| 9 | 0035 | Constrained: before 0039 |
| 10 | 0037 | Constrained: after 0026 |
| 11 | 0036 | Constrained: after 0028 |
| 12 | 0046 | Constrained: before 0039 |
| 13 | 0039 | Constrained: after 0035 and 0046, before 0041 |
| 14 | 0040 | Rewrites the option declarations 0045 edits |
| 15 | 0042 | |
| 16 | 0045 | Breaks `--stats`, so it wants the same release as ADR-0016's reshaping of that table |
| 17 | 0020 | |
| 18 | 0041 | Last on purpose: its §7 argues against doing it between two changes that move code |

"Constrained" means the list below forces the position. A blank cell means no recorded reason, so
that row can move.

**Not in the run**, which is every other row in the index:

- **0024** is the owner's to file, and depends on nothing here.
- **0033** wants a real capture in front of you before anyone can judge it worth a fourth row.
- **0044** waits on CPython synchronizing the ring. Its §4 states the one measurement that would
  put it back in play sooner.

**The only ordering constraints:**

- 0026 before 0037, which assumes its shared naming helper.
- 0028 before 0036, which it shrinks.
- 0035 before 0039, which would otherwise move nine classes 0035 deletes.
- 0046 before 0039, which moves the structure 0046 changes. Reversed, 0039 would have to settle
  0046's open question about re-keying `_running_rings`, which is more than either spec asks.
- 0039 before 0041, or the same files move twice.

0040, 0042 and 0049 depend on nothing else here; take any of them at any time. 0049 is placed
fourth rather than left to float because it is silent and its output looks like data, not because
anything forces the position. 0033 and 0035 both came out of ADR-0015's work and neither blocks
the other, 0035 being the cheapest and standing alone.

## Where these came from

0035–0042 came out of a code-structure review of `src/gcmon` on 2026-08-15. Three of its findings
are missing from the table because specs already covered them: 0028, 0029 (since retired) and
0030 §4.5. 0043 came from installing the package into a clean 3.15 environment the next day, the
first thing in five releases to put the distribution's version next to the package's own.

0044 came out of the same session as ADR-0015 and stayed a working note until the answer settled:
gcmon waits for CPython to fix the target rather than guessing from the reader's side. It sits
here because the wait is open-ended and the analysis has to survive it.

0049 came out of building 0048, which is retired. Holding one attachment per pid instead of
rebuilding it every poll did not create that window — ADR-0017 was written about it — but it
changed what falls through, from a wrong number to fabricated records. It was filed once
[ADR-0019](../docs/adr/0019-attach-to-a-process-once.md) existed for it to cite.
