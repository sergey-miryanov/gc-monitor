# 0030: Close six small correctness hazards in the exporter package

- **Status:** Not started
- **Kind:** feature (cleanup)
- **Effort:** S
- **Origin:** post-v0.2.0 code review (old spec 18, REQ-2, 7, 9, 10, 11, 12, 14)
- **Respects:** [ADR-0001](../docs/adr/0001-hand-rolled-perfetto-protobuf-encoder.md) (wire constants mirror the `.proto`; policy does not), [ADR-0005](../docs/adr/0005-counter-y-axis-share-key.md), [ADR-0011](../docs/adr/0011-process-lifetime-and-ordering.md) (`_io_lock` serializes encoder state)

## 1. Problem statement

Six independent one-file changes, batched because each is too small to schedule alone and all
six live in the same package. None is operator-visible today; each is a way for a future change
to go wrong quietly. They are listed in section 4 in the order they should land, cheapest first. Take
them together or drop any one; nothing here depends on anything else here.

## 2. Solution

No operator-visible change, with one exception noted in section 4.5: the message for an unsupported
`combine` format combination gains an `Error combining files:` prefix. Exit code, log level and
the sentence itself are unchanged.

## 3. User stories

1. As a maintainer adding a counter metric, I want one place to add it and a compiler that
   objects if I misspell it, so that a typo does not silently rank the track at 0.
2. As a maintainer adding a `TraceEvent` type, I want the Chrome encoder to say out loud which
   types carry a timestamp, so that a new type without `ts` is a type error rather than a
   silently unconverted field.
3. As a maintainer touching `PerfettoTrackState`, I want its threading contract written down,
   so that I do not add a call from outside `_io_lock` and corrupt uuid allocation.
4. As a maintainer of `gcmon combine`, I want format validation in one place, so that the two
   copies cannot disagree about what is supported.
5. As anyone reading `build_track_event`, I want its parameters not to shadow builtins, so that
   `type()` means `type()` inside the function.

## 4. Implementation decisions

**4.1: `_COUNTER_RANKS` becomes an enum.** The dict in `perfetto_format` maps eleven metric
names to `sibling_order_rank` values. A misspelled key silently falls through
`_COUNTER_RANKS.get(metric, 0)` to rank 0, which puts the track at the top of the group and
looks deliberate. Replace it with a `CounterMetric(IntEnum)` whose member names are the metric
names uppercased and whose values are the current ranks, **preserved exactly**, including
`rss = 1`, which shifted every rank below it when RSS sampling landed
([ADR-0013](../docs/adr/0013-rss-sampling.md)). Lookup becomes a helper that catches `KeyError`
and returns 0, keeping today's tolerance for an unknown metric. It stays in `perfetto_format`
beside `_TOPLEVEL_COUNTER_METRICS` and does **not** move to `perfetto_proto`: that module
mirrors Perfetto's `.proto` field numbers (ADR-0001) and ranks are our own layout policy.

**4.2: `JsonEventEncoder.write_events` stops probing with `getattr`.**
`ts_ns = getattr(e, "ts", None)` asks at runtime what the type system already knows: `ts` is
present on `BeginEvent`, `EndEvent`, `CounterEvent` and `InstantEvent` and absent on
`ProcessMeta` / `ThreadMeta`. Replace it with an `isinstance` check over the four, so a new
event type with a timestamp is a visible omission rather than a field that quietly fails to be
converted to microseconds. Rewrite the neighbouring `e.ph == "C"` branch as
`isinstance(e, CounterEvent)` for the same reason.

**4.3: `PerfettoTrackState` states its threading contract.** It is not internally thread-safe
and does not need to be: every call reaches it through
`ProtobufEventEncoder.write_events` under `BufferedTraceExporter._io_lock`, and
`PerfettoExporter.add_process_liveness` takes that same lock explicitly for exactly this reason
(ADR-0011). Add that to the class docstring. No locking, no behaviour change.

**4.4: `BufferedTraceExporter._build_meta` states its atomicity guarantee.** The check-and-emit
is already atomic under `_lock`, and `TestMetaDedupRaceClosed` in
`tests/exporters/test_exporter_thread_safety.py` pins it. The docstring does not say so, which
makes "hold the lock across both the check and the emit" look like an implementation detail
rather than the contract it is. One paragraph.

**4.5: One `combine` format check, not two.** `cmd_combine` rejects
`--input-format chrome --output-format jsonl` with a log line and `return 1`; `combine_files`
rejects the same pair with a `ValueError`. Delete the CLI copy. `cmd_combine` already catches
`ValueError` from `combine_files` and turns it into the same `return 1`, so the operator still
gets exit 1 and the same sentence, now prefixed `Error combining files:` and printed after the
"Combining N file(s)…" preamble. The argparse `choices=` lists stay: they are the first line of
defence and reject unknown formats before either check runs.

**4.6: `build_track_event` stops shadowing `type`.** Rename its first parameter to
`event_type`. It is re-exported from `perfetto_format` and called from `perfetto_format`,
`perfetto_builders` and `perfetto_process_lifetime`, and it is called by name in the tests, so
grep `src/` and `tests/` together. Mechanical, but it is a keyword-argument rename and will
fail loudly rather than silently if a call site is missed.

**Also considered and deliberately not batched here:** making
`combine._normalize_trace_timestamps` return a new list instead of mutating in place.
The mutation is currently harmless (its only caller passes a list it just built from
`_parse_events` or `convert_to_trace_format`, so nothing else holds a reference) and a
non-mutating rewrite means constructing fresh structs for every event in a combine run. It
becomes worth doing the moment a caller shares the list; until then the honest fix is a
docstring saying it mutates, which 4.3 and 4.4's reasoning already argues for. Fold it into 4.3.

**Not adopted at all:** importing `psutil` at the top of `encoder.py` and making it a hard
dependency. Graceful degradation without `psutil` is a documented, tested property: the
`[cmdline]` extra in [docs/rss.md](../docs/rss.md), the fallback in
`ProtobufEventEncoder._default_cmdline_provider`, and the same pattern in `rss_sampler`. The
lazy import is what makes it work.

## 5. Seams and testing decisions

- **Seam:** `tests/exporters/`, at each module's public function. Nothing here changes what a
  trace means, so the trace-processor seam has nothing to observe; the existing integration
  suite serves as the regression guard rather than the assertion.
- **New seam needed:** none.
- **What makes a good test here:** for 4.1, assert the enum's values against the eleven current
  ranks *by name*, so the test fails if a member is renumbered. A test that reads the ranks out
  of the enum and compares them to itself proves nothing. For 4.5, patch `combine_files` to
  raise a sentinel and assert `cmd_combine` reaches it, proving the CLI no longer short-circuits.
  4.2, 4.3, 4.4 and 4.6 are covered by the existing suite continuing to pass; do not add tests
  that assert a docstring exists.
- **Prior art:** `tests/exporters/test_perfetto_format.py` for the rank assertions;
  `tests/test_convert_cmd.py` for the CLI-level sentinel pattern.
- **Cases:**
  1. Each of the eleven metrics keeps its exact current rank; an unrecognized metric still
     ranks 0.
  2. `cmd_combine` with `--input-format chrome --output-format jsonl` still exits 1 and still
     logs the sentence.
  3. Regression guard: the full Perfetto integration suite passes unchanged: no track moves,
     no field number changes.

## 6. Out of scope

- Anything that changes trace bytes. Every item here is internal.
- `psutil` as a hard dependency (see section 4, not adopted).
- Splitting `perfetto_format`, which is large enough to deserve it but not as part of a
  hygiene pass.
- The `EventEncoder` `Protocol` → `ABC` question. Unrelated; ADR-0008 chose the protocol.
