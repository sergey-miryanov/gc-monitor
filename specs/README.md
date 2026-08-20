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
| [0020](0020-process-metadata-in-perfetto-traces.md) | Feature (enhancement) | M | A trace does not say which Python ran it or what GC thresholds it used; gcmon logs both to stderr and loses them |
| [0024](0024-cpython-report-remote-readable-gc-stats.md) | Report (upstream) | S | Five findings on `_remote_debugging.get_gc_stats` to file upstream with CPython; no gcmon change |
| [0025](0025-control-server-accept-loop-survives-transient-errors.md) | Bug (**availability**) | XS | One transient accept error and the control server refuses every later connection, saying nothing |
| [0026](0026-one-process-name-across-live-and-offline-paths.md) | Bug (correctness) | XS | A live capture names a process `Process 12345`; combined from JSONL, the same process comes out `12345` |
| [0027](0027-thread-descriptor-tid-for-interpreter-zero.md) | Bug (reporting) | XS | The main interpreter's `thread.tid` is the pid, so a SQL query has to special-case interpreter zero to read ids |
| [0028](0028-combined-exporter-reaches-into-sub-exporter-privates.md) | Feature (cleanup) | XS | `chrome+perfetto` works only by reading a private attribute, with the type checkers told not to look |
| [0030](0030-exporter-hygiene-batch.md) | Feature (cleanup) | S | Six one-file hazards in the exporter package: rank dict, `getattr` probe, builtin shadow, two undocumented threading contracts, duplicated validation |
| [0031](0031-readme-output-example-is-labelled-chrome-only.md) | Bug (cosmetic) | XS | The README heads its only trace example "Chrome Trace Output" and captions it as the Perfetto UI |
| [0033](0033-loss-counter-track.md) | Feature (enhancement) | S | The loss row shows where gcmon went blind but not how much it missed; a bar losing 1 record looks like one losing 40 |
| [0035](0035-derive-every-gc-sub-phase-from-one-table.md) | Feature (cleanup) | L | gcmon writes CPython's eight optional GC sub-phases out by hand in six places; adding the ninth means six edits, and nothing fails if you miss one |
| [0036](0036-one-exporter-method-per-record-kind.md) | Feature (cleanup) | M | `EventsExporter` has grown one method per record kind, three of them no-ops, and the CLI keeps a hand-maintained list of which formats handle RSS at all |
| [0037](0037-one-meta-emission-path-for-live-and-combined-traces.md) | Feature (cleanup) | M | Two implementations of "emit this pid's process and thread meta"; 0026 exists because they already drifted once |
| [0039](0039-split-the-record-model-and-stats-by-concern.md) | Feature (cleanup) | S | The record model and the stats module carry three jobs each; `tests/stats/` already splits along a seam the source does not have |
| [0040](0040-derive-the-monitoring-options-from-one-table.md) | Feature (cleanup) | M | gcmon declares every monitoring option three times, and echoes a rejected configuration to the log as though it had accepted it |
| [0041](0041-give-the-package-explicit-layers.md) | Feature (cleanup) | L | The package's five layers are invisible and unchecked; the dependency direction is clean today and nothing keeps it that way |
| [0042](0042-name-the-process-session-for-its-role.md) | Feature (cleanup) | S | The monitored-process seam carries the name of a role it does not fill, and its two adapters do not have the same shape |
| [0044](0044-torn-reads-and-reordered-publishes.md) | Bug (correctness) | S | **Blocked on upstream.** A pause slice can read one inter-collection interval too long, and a hole inside one poll's records reaches no loss window; both are races in the target that every filter gcmon has passes |
| [0047](0047-the-no-subcommand-form-has-never-worked.md) | Bug (reporting) | XS | `gcmon 12345`, the form the README opens with, exits 2; the branch in `main` that would dispatch it is unreachable |
| [0050](0050-name-the-poll-interval-for-what-it-is.md) | Feature (ergonomics) | S | `--rate` is a duration in seconds under a name that means a frequency, and gcmon echoes `Rate: 0.1s` back |
| [0051](0051-key-the-running-rings-by-pid.md) | Feature (efficiency) | S | Asking `StreamingStats` about one process walks every process's rings; `low_coverage` does it once per polled pid per tick, and on a healthy run it never stops |
| [0052](0052-a-recycled-pid-can-be-read-through-a-stale-attachment.md) | Bug (correctness) | S | A pid the OS reissues between two ticks is read through the attachment gcmon still holds, so an unrelated process's memory reaches the trace as plausible records; only Linux is exposed |
| [0054](0054-macos-attachment-leaks-a-mach-task-port.md) | Bug (availability) | S | On macOS every attachment costs gcmon a Mach port name that nothing gives back; CPython's cleanup has a Windows arm and a Linux arm and no Apple one |

Every row here has a file. A missing number either retired or never became one;
[RETIRED.md](RETIRED.md) says which.

## Suggested order

| Spec | Why here |
|------|----------|
| 0025 | The only outage, and the fix is one word |
| 0026 | Smallest user-visible wrongness |
| 0050 | Unblocked: 0049 landed, and taking this next edits the help text and the advisory once |
| 0028 | XS, and it shrinks 0036 |
| 0027 | Needs an answer from trace-processor before anyone can settle it either way |
| 0031 | |
| 0047 | XS, and the command that fails is the one the README opens with |
| 0052 | Silent, and what it produces is indistinguishable from a real measurement |
| 0030 | |
| 0035 | Constrained: before 0039 |
| 0037 | Constrained: after 0026 |
| 0036 | Constrained: after 0028 |
| 0039 | Constrained: after 0035, before 0041 |
| 0040 | Constrained: after 0050. Rewrites the option declarations 0045 edited |
| 0042 | |
| 0020 | |
| 0051 | Constrained: after 0039 |
| 0041 | Last on purpose: its section 7 argues against doing it between two changes that move code |

Rows run in order, top to bottom. "Constrained" means the table below forces the position. A blank
cell means no recorded reason, so that row can move.

**Not in the run**, which is every other row in the index:

- **0024** is the owner's to file, and depends on nothing here.
- **0033** wants a real capture in front of you before anyone can judge it worth a fourth row.
- **0044** waits on CPython synchronizing the ring. Its section 4 states the one measurement that would
  put it back in play sooner.
- **0054** was found in CPython's source and not in a run. Nobody should size it until the ports have
  been counted on a Mac.

**The only ordering constraints:**

| First | Then | Why |
|-------|------|-----|
| 0026 | 0037 | 0037 assumes 0026's shared naming helper |
| 0028 | 0036 | 0028 shrinks 0036 |
| 0035 | 0039 | 0039 would otherwise move nine classes 0035 deletes |
| 0039 | 0041 | Or the same files move twice |
| 0039 | 0051 | 0039 moves `StreamingStats`; taken first, 0051 edits the module in its final home rather than one about to move |
| 0050 | 0040 | 0040 derives the option declarations from one table and would otherwise have to carry the alias 0050 introduces through a rewrite of the structure holding it |

0042 depends on nothing else here; take it at any time.
