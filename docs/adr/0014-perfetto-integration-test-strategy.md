# ADR-0014: Validate traces against the real trace processor; deselect slow suites by marker

- **Status:** Accepted
- **Date:** 2026-06-12 (stress marker) / 2026-06-18 (trace-processor tests) / 2026-08-02
  (fuzz marker)

## Context

[ADR-0001](0001-hand-rolled-perfetto-protobuf-encoder.md) explains why gcmon hand-rolls its
Perfetto encoder, and what that costs: field-number drift and message-layout mistakes fail
*silently*. The trace still parses; it renders wrong. Three such bugs shipped, and each time
someone found them by opening a trace in the UI.

Unit tests could not have caught them. A round-trip test reads a value back through the same
constant it wrote with, so it is equally happy with a correct and an incorrect field number.
Only the trace processor can settle whether a trace means what you think it means, and it is
the same binary the Perfetto UI runs in the browser.

Separately, `ControlClient` is the child-side IPC surface used by each monitored process.
Its send and `close()` paths are locked, and the *server* side had a thread-safety suite,
but the client side had no regression guard.

## Decision

**Traces are validated by loading them into the real trace processor and asserting on the
SQL tables** it exposes (`slice`, `args`, `track`, `counter_track`, `process`, `thread`).
The `perfetto` package is a **dev-group dependency**, so it never enters the runtime tree,
and it is used only on the read side, via `perfetto.trace_processor.TraceProcessor`.
gcmon's own encoder remains hand-rolled per ADR-0001.

**Trace-processor tests run in the default suite.** They sit behind no marker and are not
skipped when the package is missing: `perfetto` is a required dev dependency, so a
developer running `pytest` has it, and the tests import it at module level. The
first run downloads the trace-processor binary; later runs use the cache.

**Only the optional suites are gated by marker**, registered in `pyproject.toml` and
deselected by `addopts = "-m 'not stress and not benchmark and not fuzz'"`:

- `stress`, for probabilistic concurrency tests. CI runs them in a separate `stress-test`
  job (`-m stress --count 20`, plus `-k "control" --count 40`), which does not block the
  always-on suite.
- `benchmark`, for CodSpeed performance benchmarks, run by their own workflow.
- `fuzz`, for randomized differential tests that load a trace per trial. CI runs them in a
  separate `fuzz-test` job on Linux only. Unlike `stress` these are **not** probabilistic:
  seeds are fixed, so a failure reproduces and repeating a trial re-runs the same trace.
  That is why the job passes no `--count`; widen coverage by raising the trial count in the
  test. They earn a marker for cost, not flakiness; the trace processor starts once per
  trial, which is seconds rather than the milliseconds the default suite budgets for.

**Tests are parametrized over Chrome JSON and Perfetto binary.** The trace processor
normalizes both into the same SQL tables, so one set of queries validates both exporters,
and a regression affecting one shows up as a parametrization failure.

**Trace processor instances are per-test, not session-scoped.** An instance loads one trace
and cannot easily be reused for another. Per-test instances are sub-second on a warm binary
cache and keep tests isolated.

**Test data is synthetic and minimal.** One `GCStatsInfo` with each known argument
populated exercises the whole structure and each field-number code path. Real captured
batches are slower and add noise without reaching new code.

**Stress tests use `threading.Barrier` for thread release** and `join(timeout=…)` only as a
watchdog, never `time.sleep` for synchronization. Worker threads capture exceptions into a
list asserted empty after the join, and no worker asserts on shared state. The contract is
"no deadlock, no uncaught exception". The OS scheduler decides the interleaving, so the
tests do not assert on it.

## Consequences

- CI catches field-number and schema-drift bugs on each run, rather than leaving them for
  a user to hit.
- The cost is that `pytest` needs the trace-processor binary. An earlier design put these
  tests behind an `integration` marker with `pytest.importorskip`, so they were skipped by
  default, which meant they seldom ran at all. Tests that never run catch nothing, and the
  download is a smaller price.
- A wire-format regression test and a trace-processor test both cover the same
  behaviour, on purpose: the wire test is fast, dependency-free, and asserts the exact
  byte-level invariant, while the trace-processor test is the end-to-end net. Neither
  subsumes the other.
- These tests replaced the manual "open it in ui.perfetto.dev and look" acceptance step.
  Running the trace processor covers strictly more, since that is what the UI runs.
- **Some behaviour is not SQL-observable at all.** `sibling_order_rank`
  ([ADR-0011](0011-process-lifetime-and-ordering.md)) and `y_axis_share_key`
  ([ADR-0005](0005-counter-y-axis-share-key.md)) are UI rendering hints that the trace
  processor does not surface as columns. Tests touching them are schema-validity guards or
  permanent `xfail(strict=False)`; asserting on a column that does not exist is not
  available as a fix.
- Stress tests are the only probabilistic tests in the suite, so they are the only ones
  whose failures can depend on how loaded the runner is.

## Alternatives considered

- **Add `perfetto` to runtime dependencies.** Rejected: ADR-0001 exists to keep it
  out of the runtime tree. Dev-group membership gives the tests what they need and ships
  nothing extra.
- **Keep the trace-processor tests behind an `integration` marker with `importorskip`.**
  Superseded. Deselecting them by default meant they seldom ran, and nothing else in the
  suite catches the bugs they exist for.
- **Session-scoped trace processor fixture.** Rejected: one instance holds one trace;
  sharing it means either reloading anyway or coupling every test to a single fixture trace.
- **Real captured GC data as test input.** Rejected: slower, noisier, and it exercises no
  code path that one fully-populated synthetic item does not.
- **Stress tests for `ControlClient`'s lazy-reconnect and failure-recovery contracts.**
  Rejected as redundant: the unit tests in `tests/control/test_control_client.py` already
  assert that `close()` followed by a send reconnects silently, and that `BrokenPipeError`
  clears the connection for the next call. Those assertions are stricter than "no exception
  under contention."

## Implementation

- `pyproject.toml` holds `perfetto` in `[tool.poetry.group.dev.dependencies]`, the `stress`,
  `benchmark` and `fuzz` marker registrations, and the `addopts` deselection.
- `.github/workflows/ci.yml`, the always-on `test` job, plus the separate `stress-test` and
  `fuzz-test` jobs.
- `tests/exporters/test_perfetto_emission_order_fuzz.py`, the only `fuzz`-marked file: it
  pins ADR-0011's emission-order claims, positive case and negative control both.
- `tests/exporters/test_perfetto_exporter_integration.py` holds the trace-processor fixture,
  the trace-writing helper, and the chrome/perfetto parametrization.
- `tests/test_convert_cmd_perfetto.py`, the same approach applied to the `combine` paths,
  including the chrome↔perfetto content-equivalence test.
- `tests/control/test_control_client_thread_safety.py` covers concurrent sends and the
  send/close race, both marked `stress`.
- `tests/exporters/test_exporter_thread_safety.py` covers the meta-dedup race from
  [ADR-0008](0008-buffered-exporter-and-encoder-protocol.md).
