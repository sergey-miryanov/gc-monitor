# 0037: Build a trace's process and thread meta in one place

- **Status:** Not started
- **Kind:** feature (cleanup)
- **Effort:** M
- **Origin:** code structure review of `src/gcmon`, 2026-08-15. Takes up what
  [0026](0026-one-process-name-across-live-and-offline-paths.md) explicitly left out of scope.
- **Respects:** [ADR-0007](../docs/adr/0007-shared-trace-converter-pipeline.md) (one conversion
  pipeline; `ProcessMeta` precedes `ThreadMeta`),
  [ADR-0008](../docs/adr/0008-buffered-exporter-and-encoder-protocol.md) (meta dedup lives in
  the exporter base and is atomic; `combine` uses an encoder without an exporter),
  [ADR-0012](../docs/adr/0012-trace-output-formats.md) (Perfetto output in `combine`)

## 1. Problem statement

gcmon has two implementations of "emit the process and thread meta for this pid", and they have
already drifted once: [0026](0026-one-process-name-across-live-and-offline-paths.md) exists
because a live capture names a process `Process 12345` and the same process combined from JSONL
names it `12345`. 0026 fixes the two literals and says so: it puts the shared implementation
out of scope deliberately, to stay XS. That leaves the mechanism that produced the drift in
place, ready to produce the next one: the two paths also differ in *when* they emit thread meta
(as each iid first appears, versus all of a pid's threads up front), and each will keep
acquiring behaviour independently.

`combine_files` compounds it by choosing its output encoder with its own `match` on the format
string, duplicating the dispatch `EventsExporterFactory` already owns. A format added to one is
not added to the other.

## 2. Solution

Nothing changes for an operator beyond what 0026 already promises. What changes is that
"what meta does this pid need, and has it been emitted yet" is answered once, by one piece of
code that both the live exporter and `gcmon combine` call, so a third divergence has nowhere to
start.

## 3. User stories

1. As an operator comparing a live capture against a combined one, I want identical process and
   thread metadata, so that a difference between the two traces is a real difference in the run.
2. As a maintainer changing how a track is named or described, I want one place to change it,
   so that the offline path cannot keep the old form.
3. As a maintainer adding an output format to `combine`, I want the format dispatch to be the
   one the live path already uses, so that a format cannot be supported live and missing
   offline.
4. As an operator running `gcmon combine` over a capture with several interpreters, I want the
   thread rows to carry the same names they carry live, so that a PerfettoSQL query written
   against one trace runs against the other.
5. As a maintainer, I want the meta dedup to stay atomic under the exporter's state lock, so
   that sharing the implementation does not reopen the race ADR-0008 closed.
6. As a maintainer, I want the deprecated re-export shim gone once nothing needs it, so that
   there are not two import paths for one function.

## 4. Implementation decisions

**4.1: Extract the meta *decision*, keep the dedup *state* where it is.** The two paths differ in
what they know: `BufferedTraceExporter._build_meta` owns per-`(pid, iid)` dedup state across a
long run, while `trace_converter.convert_to_trace_format` has every record in hand at once and
derives the thread set per pid. What they should not differ in is which meta events a given
`(pid, iid)` needs and how they are named. Extract that into one function in `trace_event.py`,
beside `process_meta` and `thread_meta`, the same home 0026 chose for
`process_display_name(pid)`, which this generalizes.

`_build_meta` keeps its `_seen_pids` / `_seen_tids` sets and its single critical section under
`_lock`. ADR-0008 closed the check-and-emit race there and `TestMetaDedupRaceClosed` pins it;
this must not move the emit outside that section. The batch path calls the same function with
its own already-computed set.

**4.2: One encoder dispatch, still without an exporter.** `combine_files` stops matching on
`output_format` itself and calls a shared `encoder_for(output_format)` used by both
`EventsExporterFactory` and `combine_files`. This deliberately does **not** route `combine`
through an `EventsExporter`: ADR-0008 chose composition precisely so an encoder can run without
one, and `combine` has every event in memory and needs neither the buffer nor the flush
threshold. Sharing the dispatch gets the benefit without overturning the record.

**Rejected: feed `combine` through `EventsExporterFactory` and delete
`convert_to_trace_format` outright.** It is the larger and more tempting change: it would make
the offline path exercise the live code end to end, which is the strongest possible guard
against divergence. It loses on two counts. It contradicts ADR-0008's consequence that the
encoder protocol has no dependency on the exporter base, which was a decision and not an
accident. And it gives `combine` a buffering lifecycle it has no use for, including an
`add_process_liveness` call it has no source for. The fact that would settle it differently: a
third consumer of the conversion pipeline appearing, at which point one shared path is worth
more than the encoder's independence.

**4.3: Delete `chrome_trace_format.py`.** It is a re-export shim kept, per ADR-0007's
consequences, "so existing importers keep working". Nothing inside `src/` imports it; the only
importer in `tests/` is `tests/exporters/test_chrome_trace_format.py`, which imports two names
from it and a third from `trace_converter` directly, in the same file. Point it at
`trace_converter` and delete the module. ADR-0007's consequence list gets a line
saying the shim served its purpose and went.

**4.4: Ordering is preserved, not unified.** The live path interleaves thread meta with events
as each iid first appears; the batch path emits a pid's thread meta before that pid's events.
Both satisfy ADR-0007's contract that `ProcessMeta` precedes `ThreadMeta` for a pid, both
produce the same trace once loaded, and changing either would move bytes for no operator
benefit. Out of scope, stated here so nobody "fixes" it as part of this.

## 5. Seams and testing decisions

- **Seam:** `tests/test_convert_cmd_perfetto.py`, which already loads combine output into the
  trace processor and queries the `process` and `thread` tables. Metadata is columns there, so
  the assertion is about what the trace means rather than about our own literals, the highest
  seam available, and the one 0026 picked for the same reason.
- **New seam needed:** none.
- **What makes a good test here:** assert *equality between the two paths* for the same pid and
  iid, not two tests each asserting a literal; a literal test per path is exactly what let the
  name drift happen and pass. Build the live events and the offline events for one pid with two
  interpreters and compare the meta each produces as a set.
- **Prior art:** the cross-path comparison 0026 specifies; the chrome↔perfetto
  content-equivalence test in `tests/test_convert_cmd_perfetto.py`
  ([ADR-0014](../docs/adr/0014-perfetto-integration-test-strategy.md));
  `TestMetaDedupRaceClosed` in `tests/exporters/test_exporter_thread_safety.py` for the race
  that must stay closed.
- **Cases:**
  1. Live and offline meta for one pid with interpreters 0 and 1 are equal as sets.
  2. `TestMetaDedupRaceClosed` still passes: two threads adding events for a brand-new pid
     produce one `ProcessMeta`.
  3. `combine --output-format perfetto` and `--output-format chrome` both still work, and adding
     a format to the shared dispatch makes it available to both `combine` and the live factory.
  4. Regression guard: the Perfetto integration suite passes with no track moved; Chrome output
     for a fixed capture is byte-identical apart from the process name 0026 changes.

## 6. Out of scope

- The process *name* itself. [0026](0026-one-process-name-across-live-and-offline-paths.md)
  settles it, is XS, and should land first; this spec assumes its `process_display_name`
  helper exists.
- Routing `combine` through an `EventsExporter` (see section 4.2, rejected with the condition that
  would reopen it).
- The two timestamp normalizers. `combine._normalize_trace_timestamps` works on `TraceEvent`
  and `jsonl_io.normalize_jsonl_timestamps` on records; they exist because there are two
  representations, not because of this duplication.
  [0035](0035-derive-every-gc-sub-phase-from-one-table.md) turns the second into a table walk,
  which is most of the cost of the second one.
- Naming a process after its cmdline. ADR-0010 territory, and 0026 already excluded it.
- Emission ordering (see section 4.4).

## 7. Further notes

0026, 0037 and [0036](0036-one-exporter-method-per-record-kind.md) all touch the exporter
package and are independent. Order by size: 0026 (XS, one literal), then
[0028](0028-combined-exporter-reaches-into-sub-exporter-privates.md) (XS), then this, then
0036, each leaving less for the next.
