# 0026: Give a process the same name whether the trace was written live or combined

- **Status:** Not started
- **Kind:** bug (correctness)
- **Effort:** XS
- **Origin:** post-v0.2.0 code review (old spec 18, REQ-6)
- **Respects:**
  [ADR-0007](../docs/adr/0007-shared-trace-converter-pipeline.md) (one
  conversion pipeline),
  [ADR-0010](../docs/adr/0010-process-identity-cmdline-and-start-marker.md)
  (process identity)

## 1. Problem

Two call sites build the `ProcessMeta` for a pid and disagree on the name
string. That is real. What was claimed to follow from it was not: **no trace
ever carried either name.**

This spec was written against the Chrome output, where the `process_name`
metadata event's `args.name` reached the file. `--format chrome` is gone
([ADR-0021](../docs/adr/0021-chrome-trace-format-removal.md)), and the
Perfetto encoder builds `f"Process {pid}"` itself and has never read the name
a `ProcessMeta` carried. So the symptom the spec opens with -- one process
labelled `Process 12345` live and `12345` combined -- cannot occur in any
output gcmon writes today.

## 2. Evidence

The drift is there:

- `BufferedTraceExporter._build_meta` (live path):
  `process_meta(pid, f"Process {pid}")`
- `trace_converter.convert_to_trace_format` (offline `combine` path):
  `process_meta(pid, f"{pid}")`

ADR-0007 put both paths behind one converter so a `TraceEvent` means the same
thing wherever it came from. The meta events are the part that stayed behind:
`_build_meta` lives on the exporter base because it owns the per-`(pid, iid)`
dedup state, so it never went through the converter, and the two literals
drifted.

What did not follow is that anything downstream could tell.

## 3. Scope

**Affected:** nothing an operator can observe. Two literals disagree in the
source and neither is read.

The original spec claimed the process name in Perfetto for `gcmon combine`
output was affected. It is not, for the reason in §1. It also claimed
`ThreadMeta` was "not affected, since both paths name threads
`f"Thread {iid}"`" -- `convert_to_trace_format` names them `f"{pid}:{tid}"`, a
third spelling. Moot for the same reason: the encoder builds `f"Thread {iid}"`
itself and reads neither.

**Why the suite didn't catch it:** each path is tested against its own
expectation, and nothing compares the two. That is why the drift went
unnoticed; it is not why the drift was harmless.

## 4. Proposed change

None. [Spec 0065](0065-name-the-track-an-event-is-drawn-on.md) removes the
drift by deleting the events: an event names the `Track` it is drawn on, and
the encoder derives every descriptor from that, so there is no second place
for a name to be written and no name to disagree about.

## 5. Seams and testing decisions

Nothing to test. A test asserting that two unread literals agree would pin
source that is about to be deleted.

## 6. Out of scope

- Naming a process after its cmdline or executable rather than its pid. That
  is a real improvement and a separate decision: ADR-0010 already carries
  cmdline in Perfetto, and changing the display name would affect every
  existing trace comparison.
