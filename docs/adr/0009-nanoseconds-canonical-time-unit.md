# ADR-0009: Store `TraceEvent.ts` in nanoseconds; convert at the encoder

- **Status:** Accepted
- **Date:** 2026-06-25

## Context

`TGCStatsInfo` records timestamps in nanoseconds, reading them from `time.time_ns()`,
which is Python's canonical clock unit. The `TraceEvent` model stored microseconds, so the
converter called `ts_to_us()` on each timestamp field on the way in.

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

`TraceEvent.ts` is nanoseconds. The four factories (`begin_event`, `end_event`,
`instant_event`, `counter_event`) take `ts_ns` and assign it verbatim. The converter no
longer calls `ts_to_us`; `TGCStatsInfo` values flow through unchanged.

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
- `write_trace_events` was deleted; `combine_files` uses `JsonEventEncoder` so there is
  exactly one code path that knows about the ns→µs division.

## Alternatives considered

- **Keep microseconds internally and multiply by 1000 in the Perfetto encoder.** Rejected:
  it would restore the precision `TGCStatsInfo` already carries only by inventing zeros in
  the low three digits, and it keeps the model's unit different from every source it reads.
- **Store both units on the event.** Rejected: two fields that must agree will disagree
  eventually, and it doubles the struct.
- **A nanosecond mode for the Chrome output.** Out of scope. The Chrome Trace Event format
  is a public spec with microsecond timestamps; deviating would break the viewers.

## Implementation

- `src/gcmon/trace_event.py:102,114-119,131-134,145`, the four factories taking `ts_ns`.
- `src/gcmon/data.py:53-55`, `ts_to_us`, now called only by the JSON encoder.
- `src/gcmon/exporters/encoder.py:72-74`, the ns→µs conversion in
  `JsonEventEncoder.write_events`.
- `src/gcmon/exporters/perfetto_format.py`, `convert_trace_events_to_perfetto`, where every
  branch passes `event.ts` straight to `timestamp=`.
- Tests: `tests/test_time.py:5-12`;
  `tests/exporters/test_chrome_trace_format.py:183`
  (`test_preserves_timestamps_in_nanoseconds`);
  the direct duration comparison in `tests/test_convert_cmd_perfetto.py`.
