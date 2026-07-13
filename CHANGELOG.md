# Changelog

## WIP

### Breaking changes

- Remove `MonitorThread` (#63); use `MonitorLoop` instead
- `TYPE_SLICE_BEGIN`, `TYPE_SLICE_END`, `TYPE_INSTANT`, `TYPE_COUNTER` removed from `gcmon.exporters.perfetto_format.__all__` in favor of `TrackEventType` enum

### Features

- Add `TrackEventType` enum (`SLICE_BEGIN`, `SLICE_END`, `INSTANT`, `COUNTER`) to `gcmon.exporters.perfetto_format`
- Track RSS (Resident Set Size) of monitored processes in Perfetto traces (#55)
- Add `--rss` / `--rss-interval` CLI flags and `GCMON_RSS` / `GCMON_RSS_INTERVAL` env vars (#55)

### Bugfixes

- Wait for process termination before reading return code in `run_monitoring_loop()` (#65)
- Fix type annotations in source code and test suite
- Remove `proto_decoder` and rely on the `perfetto` package for testing the `Perfetto` binary format.

### CI / Infrastructure

- Update pyrefly type checker to 1.1.1
- Add pre-commit and editor configs, clean up tests, add CodSpeed benchmarks, bump actions/checkout, bump perfetto version and add protobuf dependency

## Version 0.3.1 (2026-06-29)

- Fix PyPI classifiers

## Version 0.3.0 (2026-06-29)

- Simplify error handling from `_remote_debugging` (#32)
- Remove `PollStatus.INVALID_PYTHON` (merged into `INVALID_PROCESS`) (#32)
- Add input validation for `Stats.percentile()` (must be in [0, 100])
- Fix `ControlServer` closing if not started, don't leak `Listener` on failure
- Increase `ControlServer` listener backlog to 128
- Move pyperf hook logging setup to entry point factory
- Add per PID wait policy
- Chrome trace exporter now emits duration events ("B"/"E") instead of complete events ("X")
- Drop PauseData and CounterData
- Replace TypedDict with msgspec.Struct for TraceEvents
- Unify Chrome trace and Perfetto exporters (#38)
- `gcmon combine` now supports `--output-format perfetto` for binary protobuf output (chrome and jsonl inputs)
- `TraceEvent.ts` is now stored in nanoseconds (was microseconds); fixes a 1000x compression bug in `ui.perfetto.dev`
- Per-gen `G{gen}` counter now carries `collected`, `candidates`, `duration`, and `uncollectable` (when non-zero). `heap_size` is a single shared counter per `(pid, tid)`, grouped under `GC Metrics` for per-gen and top-level for `heap_size`.
- Several metrics moved from counter events to GC slice args: `increment_size` (on `GC Pause` / `Fill increment`), `candidates` (on `Deduce Unreachable`), and the sub-step counts `finalized_garbage_count` / `deleted_garbage_count` / `clear_weakrefs_count` on their respective sub-step slices. `alive_size` is no longer on counter events.
- `gcmon monitor` / `run` now support `--format chrome+perfetto` (writes both `<base>.json` and `<base>.pftrace`); also fixes `GCMON_FORMAT=perfetto` falling back to `chrome`
- Perfetto cmdline `description` on the process track's `TrackDescriptor` is now always visible in the Perfetto UI: a single synthetic dur=0 "Start Process" `TYPE_INSTANT` event is emitted on the process track itself, lazily on the first non-meta event for each pid, so the process track is no longer hidden when the caller did not emit any `InstantEvent`. `ProcessDescriptor.cmdline` (the repeated string) is unchanged.
- Added a shared top-level Perfetto track named `Processes` that holds one `TYPE_SLICE_BEGIN` / `TYPE_SLICE_END` pair per pid, spanning the first-to-last non-counter non-meta event timestamps for that pid. The slice is named `Process <pid>`, and its BEGIN carries a `cmdline` debug annotation (argv joined with single spaces) when the cmdline provider returned a value for the pid. Provides a single visual row showing the lifetime of every monitored process. Perfetto-only; no Chrome JSON / JSONL representation is produced.
- Fix `Processes` track slice END position: the slice END is now emitted exactly once at the encoder's `close()` (via `finalize_perfetto_packets`).
- Perfetto output now orders process tracks by first event timestamp. Requires Perfetto trace processor 0.57+ and the "canary" UI channel for the ordering to be honored.
- Perfetto counter tracks with the same metric name now share a Y-axis in the UI.

## Version 0.2.0 (2026-06-10)

- Perfetto binary protobuf export (#25)
- Control plane IPC for start/stop from child process (#14, #16, #21)
- Extra GC counters and runtime data (#22, #23)
- Timestamp normalization per PID (#24)


## Version 0.1.0 (2026-05-22)

- Real-time GC monitoring via CPython `_remote_debugging` extension (3.15+)
- Chrome Trace Event format export (https://ui.perfetto.dev)
- JSONL export to file and stdout
- CLI with `monitor` (attach to PID), `run` (spawn + monitor), `combine` (merge traces)
- Streaming statistics with optional `DDSketch` percentile accuracy
- Pyperf hook integration for benchmark profiling
