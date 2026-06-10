# Changelog

## WIP

## Version 0.2.0 (2026-05-22)

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

