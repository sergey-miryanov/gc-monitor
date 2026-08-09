# Changelog

## WIP

### Breaking changes

- Rename `PerfettoTrackState.pop_process_lifetimes()` to `get_process_lifetimes()`; it no longer drains
- `ProtobufEventEncoder.open()` now refuses a second call; construct a new encoder per file
- `PerfettoTrackState.update_process_lifetime()` loses its `extends_end` keyword; it is now a plain min/max
- Perfetto `Processes` slices now span observed liveness rather than observed GC activity
- `EventsMonitor.get_child_pids()` returns `None` instead of `[]` when the process tree cannot be read
- `Count` and `Sum` in the `--stats` table, and the pyperf `gc_pause_gen_N_sum` / `_count` / `gc_pause_count` metrics, now report every collection in the monitored window rather than the subset gcmon sampled; histories spanning this change are not comparable

### Features

- Add `EventsExporter.add_process_liveness()`: `MonitorLoop` reports the PIDs that answered each poll (Perfetto only)
- A process gcmon polled but that never collected now gets a `Processes` slice, and a run in which nothing collected now writes a trace instead of no file
- Detect GC records lost to ring-buffer wrap and draw each unobserved interval as a `GC Loss(N)` slice, one span per generation nested on one track per `(pid, iid)`
- Add `Cov` and `F` columns to the `--stats` table and a `gc_pause_gen_N_coverage` pyperf metric
- Show `Count` and `Sum` as `sampled/exact`, with a leading `~` where the second number is `F`-scaled
- Warn once per run when coverage falls below 90%, naming the read-cost floor that bounds `--rate`
- Report per-generation totals since the interpreter started, as `gc_pause_gen_N_lifetime_count` / `_lifetime_sum` and a `--stats` footer
- Add `EventsExporter.add_loss_event()`, the `LossMsg` record type, and `loss_tid()` / `loss_iid()` for the `tid = -2 - iid` sentinel
- Carry loss records through Chrome, Perfetto, JSONL and stdout; `gcmon combine` reproduces the spans from a JSONL capture
- Name the collections a `GC Loss` span is missing: `collections_from` / `collections_to` on the slice, `lost_from` on the record. Every collection between the first and last observed on a ring is now either a drawn `GC Pause` slice or inside exactly one loss span's range
- Number the notes printed under the `--stats` table, since which of them appear depends on the run

### Bugfixes

- Fix GC events discarded by the poll loop. The cursor now tracks the target's `collections` counter per process, interpreter and generation
- Drop poll state when the wait policy gives up on a PID or the PID leaves the process tree, so a reused PID does not inherit its predecessor's counter
- Draw no `GC Loss` span for a window whose `ts_stop` does not follow its `ts_start`, which says the lost records had nowhere to run and can only mean the target's record ordering did not reach gcmon. The collections still count toward `Count`, `Sum`, `Cov` and `F`, and the `--stats` footer names how many spans were held back
- Fix wrong durations on the Perfetto `Processes` track when process lifetimes overlap without nesting
- Every `Processes` slice now records the span gcmon observed in `real_start_ts` / `real_end_ts` annotations

### Documentation

- Add [ADR-0015](docs/adr/0015-gc-loss-spans-on-their-own-track.md) on the `GC Loss` track, the per-generation spans and the order they nest in, and what gcmon trusts the target for
- Rewrite `docs/statistics.md` around the three intervals a cell can report, `Cov`, `F`, and why percentiles read high
- Document the three notes under the `--stats` table in `docs/statistics.md`, including the spans held back for bounds that describe no interval
- Document the `GC Loss` track and the JSONL loss record in `docs/formats.md`; a slice's width is the interval the records were lost in, not the pause they took
- Document the changed pyperf metrics, and that the lifetime metrics are not benchmark-scoped
- Add a README limitation for the ring-buffer bound and the read-cost floor

## Version 0.4.0 (2026-07-31)

### Breaking changes

- Remove `MonitorThread` (#63); use `MonitorLoop` instead
- Replace `gcmon.data.dur_to_us(ts_start_ns, ts_stop_ns)` with `gcmon.data.dur_to_ms(dur_ns)`
- `TYPE_SLICE_BEGIN`, `TYPE_SLICE_END`, `TYPE_INSTANT`, `TYPE_COUNTER` removed from `gcmon.exporters.perfetto_format.__all__` in favor of `TrackEventType` enum

### Features

- Add `TrackEventType` enum (`SLICE_BEGIN`, `SLICE_END`, `INSTANT`, `COUNTER`) to `gcmon.exporters.perfetto_format`
- Add a `Read Time` row to the `--stats` table: the time each poll spends reading GC stats from the target process
- Track RSS (Resident Set Size) of monitored processes in Perfetto traces (#55)
- Add `--rss` / `--rss-interval` CLI flags and `GCMON_RSS` / `GCMON_RSS_INTERVAL` env vars (#55)

### Bugfixes

- Fix under-reported GC activity for child processes
- Fix doubled `Count` and `Sum` in the `--stats` table's `GC Pause` rows
- Keep GC phase durations in nanoseconds internally and convert to milliseconds only for display
- Fix `--rss` samples discarded with `--format chrome+perfetto`
- Warn that `--rss` has no effect with `jsonl` or `stdout`
- Wait for process termination before reading return code in `run_monitoring_loop()` (#65)
- Fix type annotations in source code and test suite
- Remove `proto_decoder` and rely on the `perfetto` package for testing the `Perfetto` binary format

### Documentation

- Correct the documented units for the JSONL `duration` field (seconds, not milliseconds) and for the pyperf `gc_pause_*` metrics (milliseconds, not microseconds)
- Clarify that the pyperf `gc_heap_size_p99` metric is a percentile over per-process peak live object counts, not over all samples
- Document the pyperf `gc_pause_count` metric, which was emitted but missing from the metric list
- Add architecture decision records under `docs/adr/`
- Split the README into per-topic guides under `docs/`; the README now covers evaluation and links out for usage
- Add `docs/README.md` as the documentation index
- Fix the screenshot URLs so they render on the PyPI project page
- Add a `Documentation` project URL pointing at `docs/README.md`
- Document where a Perfetto trace carries process command lines, with SQL examples for the two forms the trace processor exposes

### CI / Infrastructure

- Skip CI and CodSpeed runs for documentation-only pull requests
- Add `codecov.yml` with coverage targets and a three-upload wait for the OS matrix
- Update pyrefly type checker to 1.1.1
- Add pre-commit and editor configs, clean up tests, add CodSpeed benchmarks, bump actions/checkout, bump perfetto version and add protobuf dependency

## Version 0.3.1 (2026-06-29)

### Bugfixes

- Fix PyPI classifiers

## Version 0.3.0 (2026-06-29)

### Breaking changes

- Remove `PollStatus.INVALID_PYTHON` (merged into `INVALID_PROCESS`) (#32)
- Drop `PauseData` and `CounterData`
- Replace `TypedDict` with `msgspec.Struct` for `TraceEvent`
- `TraceEvent.ts` is now stored in nanoseconds (was microseconds); fixes a 1000x compression bug in `ui.perfetto.dev`
- Chrome trace exporter now emits duration events ("B"/"E") instead of complete events ("X")
- Per-gen `G{gen}` counter now carries `collected`, `candidates`, `duration`, and `uncollectable` (when non-zero); `heap_size` is a single shared counter per `(pid, tid)`, grouped under `GC Metrics` for per-gen and top-level for `heap_size`
- Several metrics moved from counter events to GC slice args: `increment_size` (on `GC Pause` / `Fill increment`), `candidates` (on `Deduce Unreachable`), and the sub-step counts `finalized_garbage_count` / `deleted_garbage_count` / `clear_weakrefs_count` on their respective sub-step slices; `alive_size` is no longer on counter events

### Features

- Add per PID wait policy
- Add input validation for `Stats.percentile()` (must be in [0, 100])
- `gcmon combine` now supports `--output-format perfetto` for binary protobuf output (chrome and jsonl inputs)
- `gcmon monitor` / `run` now support `--format chrome+perfetto` (writes both `<base>.json` and `<base>.pftrace`)
- Add a shared top-level Perfetto track named `Processes` holding one slice per pid, spanning that pid's first-to-last event, so a single row shows the lifetime of every monitored process; the slice is named `Process <pid>` and carries a `cmdline` debug annotation. Perfetto-only; no Chrome JSON / JSONL representation is produced.
- Emit a synthetic dur=0 `Start Process` instant event on each process track at its first non-meta event, so the track's cmdline `description` is always visible in the Perfetto UI even when the caller emitted no other instant event. `ProcessDescriptor.cmdline` is unchanged.
- Perfetto output now orders process tracks by first event timestamp. Requires Perfetto trace processor 0.57+ and the "canary" UI channel for the ordering to be honored.
- Perfetto counter tracks with the same metric name now share a Y-axis in the UI

### Bugfixes

- Fix `ControlServer` closing if not started, don't leak `Listener` on failure
- Fix `GCMON_FORMAT=perfetto` falling back to `chrome`
- Fix `Processes` track slice END position: the END is now emitted exactly once at the encoder's `close()` (via `finalize_perfetto_packets`)

### Internal

- Simplify error handling from `_remote_debugging` (#32)
- Unify Chrome trace and Perfetto exporters (#38)
- Increase `ControlServer` listener backlog to 128
- Move pyperf hook logging setup to entry point factory

## Version 0.2.0 (2026-06-10)

### Features

- Perfetto binary protobuf export (#25)
- Control plane IPC for start/stop from child process (#14, #16, #21)
- Extra GC counters and runtime data (#22, #23)
- Timestamp normalization per PID (#24)


## Version 0.1.0 (2026-05-22)

### Features

- Real-time GC monitoring via CPython `_remote_debugging` extension (3.15+)
- Chrome Trace Event format export (https://ui.perfetto.dev)
- JSONL export to file and stdout
- CLI with `monitor` (attach to PID), `run` (spawn + monitor), `combine` (merge traces)
- Streaming statistics with optional `DDSketch` percentile accuracy
- Pyperf hook integration for benchmark profiling
