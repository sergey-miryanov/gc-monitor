# ADR-0008: Split exporters into a buffering base class and a pluggable `EventEncoder`

- **Status:** Accepted
- **Date:** 2026-06-14

## Context

`TraceExporter` (Chrome) and `PerfettoExporter` each independently implemented the same
lifecycle: a two-lock model (one for state, one for I/O), `flush_threshold`-based
buffering, the `add_event` / `add_instant_event` / `close` sequence, and per-pid/iid
deduplication of `ProcessMeta` / `ThreadMeta`.

Only the byte production differed: the Chrome side's `[\n … \n]\n` bracket
dance versus the Perfetto side's `"wb"`-then-`"ab"` file-mode toggle. The two exporters
duplicated everything around it.

Both copies also carried the same bug. The meta-dedup did its "have I seen this pid?"
check and its emit in separate critical sections, so two threads adding events for a
brand-new pid could both pass the check and both emit a `process_name` event, putting
a duplicate process descriptor in the output.

## Decision

**`BufferedTraceExporter`** (`_buffered_exporter.py`) owns the lifecycle: the two locks,
the buffer and flush threshold, `add_event` / `add_instant_event` / `close`, and building
a pid's meta events.

**`EventEncoder`** (a `Protocol` in `encoder.py`) owns format-specific byte production
through three methods: `open(path)`, `write_events(events)`, `close()`. Two implementations
exist: `JsonEventEncoder` for Chrome Trace Event JSON and `ProtobufEventEncoder` for
Perfetto binary.

`TraceExporter` and `PerfettoExporter` become thin subclasses that construct the right
encoder. Their public constructor signatures are unchanged.

**Meta building is atomic.** The check and the emit happen inside a single critical
section under the state lock, closing the race. This is the property the two previous
implementations were reaching for and missing.

The split settled three further questions:

- **The seen-pid set lives in the base, not the encoder.** The base is the single source
  of truth for "have we seen this pid/iid?". The encoder can therefore rely on exactly one
  `ProcessMeta` per pid arriving, which means it registers a cmdline at most once per pid,
  and the previous double-checked-locking dance around that registration is gone.
- **Cmdline registration runs under the I/O lock.** The old design ran the slow
  `psutil.Process(pid).cmdline()` call outside any lock to avoid serializing threads. It
  now runs inside `write_events`, which the base serializes. Accepted: the cost is paid at
  most once per pid, because of the point above.
- **`JsonEventEncoder` writes `[]\n` on close only if nothing was ever written.** If any
  `write_events` succeeded it writes `\n]\n` instead. Both paths produce a valid JSON array.

## Consequences

- A new output format is an `EventEncoder` implementation. No lifecycle, locking or dedup
  code to copy. [ADR-0012](0012-trace-output-formats.md)'s `combine --output-format perfetto`
  reuses `ProtobufEventEncoder` directly, outside any exporter, because the protocol has no
  dependency on the base class.
- Output bytes were unchanged by the refactor, verified by the existing structural tests,
  which decode the output and assert on each meaningful field.
- `close()` is idempotent.
- **`add_event` after `close()` silently drops the event.** This matches the pre-refactor
  behaviour, and the alternative is raising from a monitoring callback during shutdown.
- A cmdline provider failure costs the descriptor its cmdline and nothing else. A `psutil`
  error never costs you the trace.

## Alternatives considered

- **A common base class with abstract encode methods instead of a separate protocol
  object.** Rejected: composition lets the encoder run without an exporter, which is what
  `combine` needs.
- **Keep cmdline registration outside the lock.** Rejected: it required the double-checked
  locking that atomic meta building makes unnecessary, and the call now happens once per
  pid, so the serialization is not worth the complexity.
- **Fold `JsonlExporter` / `StdoutExporter` into the same base.** Not done: they consume
  raw `TGCStatsInfo`, not `TraceEvent`, so the data shapes differ. This remains open work.

## Implementation

- `src/gcmon/exporters/_buffered_exporter.py` builds a pid's meta events with the
  check-and-emit inside one critical section under the state lock.
- `src/gcmon/exporters/encoder.py` declares the `EventEncoder` protocol, both
  implementations, and the cmdline registration each one runs.
- Tests: `tests/exporters/test_exporter_thread_safety.py` fires two threads at a brand-new
  pid to pin the dedup race; the structural tests in
  `tests/exporters/test_chrome_trace_exporter.py` and
  `tests/exporters/test_perfetto_exporter.py` decode the output; the stress suite runs at
  `--count 20`.
