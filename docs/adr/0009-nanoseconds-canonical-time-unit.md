# ADR-0009: Store `TraceEvent.ts` in nanoseconds; convert at the encoder

- **Status:** Accepted
- **Date:** 2026-06-25

## Context

`TGCStatsInfo` records timestamps in nanoseconds, reading them from `time.time_ns()`,
which is Python's canonical clock unit. The `TraceEvent` model stored microseconds, so the
converter divided each timestamp field down on the way in.

That was fine while Chrome was the only backend: the Chrome Trace Event format is specified
in microseconds, so the model matched the output.

It broke when Perfetto arrived. `TracePacket.timestamp` is a **nanosecond** field, and
`ProtobufEventEncoder` wrote microseconds straight into it, compressing the whole timeline
of every `.pftrace` by a factor of 1000. The failure was invisible because the compression
was uniform: relative shape looked right, and the chrome↔perfetto equivalence test compared
`chrome_dur // 1000 == perfetto_dur`, encoding the discrepancy as if it were expected.

One model unit shared across backends with different wire units has no correct answer. The
question is where the conversion belongs.

## Decision

`TraceEvent.ts` is nanoseconds. The four event factories take `ts_ns` and assign it
verbatim. The converter no longer divides; `TGCStatsInfo` values flow through unchanged.

**Conversion happens at the encoder, once, per format:**

- `JsonEventEncoder` divides by 1000 when serializing, so the Chrome Trace Event output
  stays in microseconds. That format is a public spec and does not change.
- `ProtobufEventEncoder` writes `event.ts` directly. No conversion.

The in-memory model uses the source's unit, and each encoder owns the unit its own wire
format demands. If you are asking which unit a timestamp is in: nanoseconds, unless you are
looking at bytes on disk.

## Consequences

- Perfetto traces have correct timelines. The `chrome_dur // 1000` workaround in the
  equivalence test is gone; the comparison is direct, so the test guards the units instead
  of documenting a bug.
- **There is no migration path for existing `.pftrace` files.** Traces captured before this
  change are 1000× compressed and cannot be corrected after the fact, because the original
  precision is not recoverable from the file. Re-capture.
- Chrome output loses sub-microsecond precision to the integer division. The format's
  microsecond resolution is the source of that, not this change.
- When reading tests, watch the boundary: in-memory assertions are in nanoseconds,
  assertions against decoded JSON are in microseconds. The same literal means different
  things on either side of `JsonEventEncoder`.
- The standalone trace-writing helper was deleted; `combine` goes through
  `JsonEventEncoder` too, so exactly one code path knows about the ns→µs division.

## Alternatives considered

- **Keep microseconds internally and multiply by 1000 in the Perfetto encoder.** Rejected:
  it would restore the precision `TGCStatsInfo` already carries only by inventing zeros in
  the low three digits, and it keeps the model's unit different from every source it reads.
- **Store both units on the event.** Rejected: two fields that must agree will disagree
  eventually, and it doubles the struct.
- **A nanosecond mode for the Chrome output.** Out of scope. The Chrome Trace Event format
  is a public spec with microsecond timestamps; deviating would break the viewers.

## Implementation

- `src/gcmon/trace_event.py` holds the four factories, each taking `ts_ns`.
- `src/gcmon/data.py` holds the ns→µs conversion, now called only by the JSON encoder.
- `src/gcmon/exporters/encoder.py` applies it as it serializes Chrome output.
- `src/gcmon/exporters/perfetto_format.py` passes `event.ts` straight to the packet
  timestamp on every branch.
- Tests: `tests/test_time.py` for the conversion itself;
  `tests/exporters/test_chrome_trace_format.py` for timestamps preserved in nanoseconds
  through the model; the direct duration comparison in
  `tests/test_convert_cmd_perfetto.py`.
