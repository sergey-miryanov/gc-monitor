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
- GC sub-step slices (`Finalize Garbage`, `Delete Garbage`, `Clear Weakrefs`) now include their respective count in slice args
- Counter events no longer include `alive_size`, `finalized_garbage_count`, `deleted_garbage_count`, `clear_weakrefs_count` (these remain in pause and sub-step args)
- `heap_size` is now emitted as a single counter track per `(pid, tid)`, updated by all generations; it is no longer split into `G0 heap_size` / `G1 heap_size` / `G2 heap_size` counter tracks
- All other counter tracks are grouped under `GC Counters` group
- `increment_size` is no longer emitted as a per-generation counter track (`G0 increment_size` / `G1 increment_size`); it remains queryable from the `GC Pause (gen=N)` slice's args and from the `Fill increment (gen=N)` sub-step args
- `Deduce Unreachable (gen=N)` slice args now include `candidates` (the per-pause candidate count)
- `uncollectable` is no longer emitted as a per-generation counter track (`G{gen} uncollectable`) when its value is 0; it remains queryable from the `GC Pause (gen=N)` slice's args
- Added `duration` as a shared counter track (one per `(pid, iid)`, double value, seconds) in a renamed `GC Metrics` group, positioned at the top via `sibling_order_rank=0`; the per-gen counter tracks moved into the renamed group and their ranks were renumbered (1+) so the new layout is `GC Metrics` → `duration` (rank 0) → `G{gen} collected` (rank 2) → `G{gen} uncollectable` (rank 3, when present) → `G{gen} candidates` (rank 4); `heap_size` stays as a top-level counter (rank 1, ignored by trace processor due to OS-scoped parent)
- `duration` is now emitted as a per-generation counter track (`G0 duration`, `G1 duration`, `G2 duration`) instead of a shared track; a shared `duration` track "can't be shown" properly when all three generations contribute at the same timestamp. The `GC Metrics` group now contains only per-gen counters: `collected` (rank 1), `uncollectable` (rank 2, conditional), `candidates` (rank 3), `duration` (rank 4). `heap_size` stays as a top-level counter (rank 0, ignored by trace processor due to OS-scoped parent)
- `gcmon monitor` and `gcmon run` now support `--format chrome+perfetto` for simultaneous Chrome JSON and Perfetto binary outputs (paths derived from `-o`: `<base>.json` and `<base>.pftrace`); also fixes a pre-existing bug where `GCMON_FORMAT=perfetto` was silently falling back to `chrome`

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

