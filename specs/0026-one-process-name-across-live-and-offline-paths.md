# 0026: Give a process the same name whether the trace was written live or combined

- **Status:** Not started
- **Kind:** bug (correctness)
- **Effort:** XS
- **Origin:** post-v0.2.0 code review (old spec 18, REQ-6)
- **Respects:** [ADR-0007](../docs/adr/0007-shared-trace-converter-pipeline.md) (one conversion pipeline), [ADR-0010](../docs/adr/0010-process-identity-cmdline-and-start-marker.md) (process identity)

## 1. Problem

The same process is labelled two different ways depending on how the trace was produced. Run
`gcmon 12345 -o trace.json` and the process shows up as **`Process 12345`**. Record JSONL and
run `gcmon combine run.jsonl -o trace.json` and the same process shows up as **`12345`**. An
operator comparing a live capture against a combined one, or grouping by process name in a
PerfettoSQL query, gets two names for one thing.

## 2. Evidence

Two call sites build the `ProcessMeta`, and they disagree on the name string:

- `BufferedTraceExporter._build_meta` (live path): `process_meta(pid, f"Process {pid}")`
- `trace_converter.convert_to_trace_format` (offline `combine` path):
  `process_meta(pid, f"{pid}")`

ADR-0007 put both paths behind one converter so a `TraceEvent` means the same thing wherever
it came from. The meta events are the part that stayed behind: `_build_meta` lives on the
exporter base because it owns the per-`(pid, iid)` dedup state, so it never went through the
converter, and the two literals drifted.

## 3. Scope

**Affected:** the `process_name` metadata event in Chrome JSON, and the process name in
Perfetto, for `gcmon combine` output. Live captures are already correct.

**Not affected:** `ThreadMeta`, since both paths name threads `f"Thread {iid}"`. The Perfetto
`cmdline` annotation (ADR-0010) is a different field and unaffected. Event content, timestamps
and track layout are untouched.

**Why the suite didn't catch it:** each path is tested against its own expectation.
`tests/exporters/test_chrome_trace_exporter.py` asserts `args={"name": "Process 12345"}` and
the combine tests assert their own value. Nothing compares the two paths, so both "passed"
while disagreeing.

## 4. Proposed change

1. Adopt `f"Process {pid}"`, the live form. It is what most existing fixtures and helpers
   already carry (`tests/exporters/perfetto_helpers.py` builds
   `process_meta(pid, f"Process {pid}")`), it is the form users have seen since v0.1.0, and a
   bare integer reads as a track index rather than a name in the Perfetto UI.
2. Change the offline literal in `convert_to_trace_format` to match. Update the combine
   fixtures that pin the bare-pid form.
3. Give the string one home so it cannot drift again: a `process_display_name(pid)` helper in
   `trace_event.py`, next to `process_meta`, called by both sites.

## 5. Seams and testing decisions

- **Seam:** `tests/test_convert_cmd_perfetto.py`, which already loads combine output into the
  trace processor and queries the `process` table. The name is a column there, so the
  assertion is on what the trace means rather than on our own literal.
- **New seam needed:** none.
- **What makes a good test here:** one test that asserts *equality between the two paths* for
  the same pid, not two tests each asserting a literal; a literal test is what let the drift
  happen. Build the live events and the offline events for the same pid and compare the
  `ProcessMeta` produced.
- **Prior art:** the chrome↔perfetto content-equivalence test in
  `tests/test_convert_cmd_perfetto.py` ([ADR-0014](../docs/adr/0014-perfetto-integration-test-strategy.md)),
  which is the same shape of assertion across two encoders.
- **Cases:**
  1. Live and offline `ProcessMeta` for `pid=12345` carry the same name.
  2. Regression guard: existing Chrome-format assertions on `args={"name": "Process 12345"}`
     stay byte-identical, since the live form is the one being kept.

## 6. Out of scope

- Naming a process after its cmdline or executable rather than its pid. That is a real
  improvement and a separate decision: ADR-0010 already carries cmdline in Perfetto, and
  changing the display name would affect every existing trace comparison.
- Moving `_build_meta` itself into `trace_converter`. It holds the exporter's dedup state; the
  shared helper closes this defect without that move.
