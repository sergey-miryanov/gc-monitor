# Specs — open work

One file per unit of *forward-looking* work: something identified, understood and specified,
but not yet built. A spec states the problem, the evidence for it, the proposed change, and
the seam it will be tested through — it does not record a decision.

Complements the backward-looking half: [`docs/adr/`](../docs/adr/README.md), which records
decisions already taken and is the authority on why the design looks the way it does. If a
spec here contradicts an ADR, one of the two is wrong and it is usually the spec.

## Open specs

| Spec | Kind | Effort | Summary |
|------|------|--------|---------|
| [0020](0020-process-metadata-in-perfetto-traces.md) | Feature — enhancement | M | A trace does not say which Python ran it or what GC thresholds it used; both are logged to stderr and lost |
| [0024](0024-cpython-report-remote-readable-gc-stats.md) | Report — upstream | S | Five findings on `_remote_debugging.get_gc_stats`, to be filed with CPython; no gcmon change |
| [0025](0025-control-server-accept-loop-survives-transient-errors.md) | Bug — **availability** | XS | One transient accept error and the control server refuses every later connection, silently |
| [0026](0026-one-process-name-across-live-and-offline-paths.md) | Bug — correctness | XS | A live capture names a process `Process 12345`; the same process combined from JSONL is named `12345` |
| [0027](0027-thread-descriptor-tid-for-interpreter-zero.md) | Bug — reporting | XS | The main interpreter's `thread.tid` is the pid, so no SQL query reads interpreter ids uniformly |
| [0028](0028-combined-exporter-reaches-into-sub-exporter-privates.md) | Feature — cleanup | XS | `chrome+perfetto` works only by reading a private attribute, with the type checkers told not to look |
| [0029](0029-jsonl-and-stdout-duplicate-the-buffering.md) | Feature — cleanup | M | `JsonlExporter` carries three byte-identical copies of the buffer-and-flush logic; the next record type adds a fourth |
| [0030](0030-exporter-hygiene-batch.md) | Feature — cleanup | S | Six one-file hazards in the exporter package: rank dict, `getattr` probe, builtin shadow, two undocumented threading contracts, duplicated validation |
| [0031](0031-readme-output-example-is-labelled-chrome-only.md) | Bug — cosmetic | XS | The README's only trace example is headed "Chrome Trace Output" and captioned as the Perfetto UI |
| [0033](0033-loss-counter-track.md) | Feature — enhancement | S | The loss row shows where gcmon was blind but not how much was lost; a bar losing 1 record looks like one losing 40 |
| [0034](0034-separate-interpreter-confirmation-from-loss-arithmetic.md) | **Superseded** | S | Loss spans reached back across a collection gcmon watched start. ADR-0015's rewrite moved the edge to the poll instant, which is later still |
| [0036](0036-statistics-report-the-ring-not-the-process.md) | Bug — correctness | L | The trace draws a row per interpreter; `--stats` folds them into one, so a `P99` describes no interpreter and a starved one hides behind a busy one |

**Suggested order:** 0025 (the only outage, and it is one word) → 0026 (smallest user-visible
wrongness) → 0028 (XS, and it shrinks 0029) → 0027 (needs a trace-processor answer before it
can be settled either way) → 0031 → 0030 → 0029 → 0020. 0024 is the owner's to file and
depends on nothing here.

0036 is the largest open item and the only one that changes what an ordinary run prints. Take it
before 0033, which would add a fourth row to a track whose statistics still blend the
interpreters it separates.

0033 and 0035 came out of the work that landed as ADR-0015, and neither blocks the other.
0035 is the cheapest and stands alone. 0033 wants a real capture in front of you before it can
be judged worth a fourth row. 0034 came from the same session and is superseded: ADR-0015's
rewrite took the span's left edge from the poll clock, which is later than the bound 0034 set
out to restore. Its §4 still carries the argument for why a temporal bound differs from the
clipping ADR-0015 rejected, which is worth reading before anyone proposes narrowing a span.

## Templates

- [TEMPLATE-bugfix.md](TEMPLATE-bugfix.md) — something is broken.
- [TEMPLATE-feature.md](TEMPLATE-feature.md) — enhancements, ergonomics, cleanups: the change
  is *wanted* rather than *broken*. Adds a user-perspective solution statement and user
  stories.

Pick by whether the change fixes something or adds something, not by size. Both templates carry
the same §5 seams-and-testing section, because that is the part that decides whether the work
is actually finishable. 0024 fits neither — it produces an upstream issue rather than a change
to this repo — and says so in its header; that is the exception, not a third template.

## Conventions

**1. Anchor on symbols, never line numbers.** Cite `ControlServer._accept_loop`, not
`control_server.py:114`. Line numbers rot within one release: the spec set this folder replaced
cited `run_cmd.py:69`, `monitor_loop.py:46` and `jsonl_exporter.py:32-97`, and by the time
anyone came back to them every one pointed at something else. Re-verifying those references by
hand cost more than writing them did. Quote code only where the defect or decision **is** the
code, trimmed to the decision-rich part and labelled with the symbol it lives in. External
sources are the exception — a CPython line, a Perfetto field number — and they must be pinned to
a tag or version so the citation stays checkable.

**2. Sketch the seam before the solution.** Every spec says how it will be tested, at what
level, before anyone starts. Prefer an existing seam; use the highest one that can observe the
change; keep the total number of seams in this codebase as low as possible. The ladder here,
highest first: a trace-processor SQL assertion, a wire-format byte assertion, an exporter-level
unit test, a private attribute. Do not put a new suite behind a pytest marker unless it is slow
or probabilistic — [ADR-0014](../docs/adr/0014-perfetto-integration-test-strategy.md) already
made that mistake once and reversed it, because a deselected test catches nothing.

**3. State the problem from the operator's perspective.** What someone running gcmon sees, or
what someone opening the trace afterwards cannot tell — before any mention of the faulty
expression. Feature specs go further and carry user stories. A change with no operator-facing
consequence is a cleanup, not a bug, however wrong the code looks.

**4. Use the project's vocabulary, and respect the ADRs.** One entry read out of the target's
ring is a **record**; one thing written into a trace is an **event**. An interpreter is
identified by its **iid**, which is what gcmon publishes as a Perfetto `tid`. An interval whose
records were overwritten unread is a **loss window** or a **blind interval**, never "missing
data". A `Processes`-track slice is a **span**. Timestamps are nanoseconds everywhere inside
gcmon and converted at the encoder
([ADR-0009](../docs/adr/0009-nanoseconds-canonical-time-unit.md)). Link the ADRs a spec must not
contradict in its header, and if implementing it overturns one, amend the ADR rather than the
code alone.

**5. Say what is out of scope.** Explicitly, with reasons. This is what keeps a spec landable.
An alternative left open — "the implementer picks one" — is a decision the spec failed to make;
either make it, or name the fact that would settle it.

**6. Assert what the trace means, not that it parsed.** gcmon's characteristic bug is a wrong
protobuf field number or a wrongly nested message: the trace still parses, and it renders wrong.
Three such bugs shipped, and each was found by a human opening the file in the UI
([ADR-0001](../docs/adr/0001-hand-rolled-perfetto-protobuf-encoder.md),
[ADR-0014](../docs/adr/0014-perfetto-integration-test-strategy.md)). A round-trip test reads a
value back through the same constant it wrote with, so it is equally happy with a correct and an
incorrect field number. "We wrote something and it parsed" proves nothing about an encoder.

### Lifecycle

Delete a spec when it lands — this folder is the open set, not a history. Git keeps the record.
If implementing it settled a durable design question, that question graduates to an
[ADR](../docs/adr/README.md); if it merely fixed something, it graduates to nothing and the file
just goes.

Numbers are assigned in order and **never reused or renumbered**, the same rule the ADRs follow,
so a reference to spec 0026 keeps meaning one thing. Gaps are normal and mean a spec landed.

A spec whose current behavior is locked by a characterization test is marked **Pinned** in its
status line and names the test — fixing it means updating that test in the same change,
deliberately. [0025](0025-control-server-accept-loop-survives-transient-errors.md) is the
current example: a test asserts the buggy behaviour, because it was written to cover the branch
rather than to state what should happen.

## Provenance

These conventions are adopted from the sibling `gcscope` repo, whose `specs/` folder solves the
same problem for the same author. Before 2026-08-05 this folder held six files in three
different header formats, was excluded by `.gitignore`, and had a template inherited from an
unrelated web project (JWT and bcrypt examples, a "Definition of Done" checklist, and an
"AI Implementation Plan" section instructing an agent to execute step 1 and await confirmation).
It is now tracked, so the delete-when-it-lands rule above has somewhere to delete to.

Each of the six was re-verified against the code on 2026-08-05 rather than carried over. Three
were retired:

| Old spec | Outcome |
|---|---|
| 18 — post-v0.2.0 review fixes (15 REQs) | Split. REQ-1's per-pid `last_ts` is **obsolete**: `EventsMonitor` no longer has `_last_ts` at all, having been rebuilt around per-pid `_cursors` keyed on the `collections` counter ([ADR-0015](../docs/adr/0015-gc-loss-spans-on-their-own-track.md)), which subsumes the fix. REQ-2 landed as `TestMetaDedupRaceClosed`. REQ-13 was rejected on the record — graceful degradation without `psutil` is now a documented, tested property. The remaining twelve became 0025–0030. |
| 19 — README update for v0.2.0 | Landed but for one item. The `combine`, `ControlClient`, Perfetto-SQL and de-duplicated "How It Works" sections are all in the README; the `## Optional Dependencies` heading it also wanted is moot, since `## Installation` now covers both extras in prose with links and the graceful-degradation note. What was left became 0031. |
| 21 — monitor-reported process liveness | **Landed** 2026-08-02. `MonitorLoop` calls `add_process_liveness` once per tick, `PerfettoExporter` overrides it, and the provisional counter carve-out was removed. Recorded in [ADR-0011](../docs/adr/0011-process-lifetime-and-ordering.md), which was updated the same day. |

20 and 24 survived and keep their numbers; both were rewritten into the templates. 20 also had a
decision made that it had left open — see its §4 on why the monitoring process's own
`gc.get_threshold()` is not a usable source for the target's thresholds.

One correction the re-verification produced, worth stating because the old spec asserted the
opposite: old REQ-4 proposed moving `JsonlExporter` onto `BufferedTraceExporter` with a
`JsonlEventEncoder`. That would have rewritten the JSONL schema — which is public, documented
per-field in [docs/formats.md](../docs/formats.md#jsonl-output), and read back by
`chrome_trace_io.read_jsonl` to drive `gcmon combine` — and broken the combine reader in the
same commit. [0029](0029-jsonl-and-stdout-duplicate-the-buffering.md) shares the buffering
instead and leaves the format alone.
