# Changelog

## WIP

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
- `TraceEvent.ts` is now stored in nanoseconds (was microseconds). Fixes a bug where perfetto traces were displayed 1000x compressed in `ui.perfetto.dev`

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

