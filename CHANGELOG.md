# Changelog

## WIP

### Breaking changes

- Slice names drop the `gen=` prefix: `GC Pause(0)`, `GC Loss(0)`, `Mark Alive(0)` and the rest. Categories are unchanged, so `gc.pause(gen=0)` still matches
- A `Processes` slice now spans how long the process was alive, not how long it was collecting
- `Count` and `Sum` now include the collections gcmon missed, so both read higher than before. This covers the `--stats` table and the pyperf `gc_pause_gen_N_count` / `_sum` / `gc_pause_count` metrics, and numbers from earlier runs no longer compare
- `EventsMonitor.get_child_pids()` returns `None` instead of `[]` when the process tree cannot be read

### Features

- Detect GC records lost to ring-buffer wrap and draw each unobserved interval as a `GC Loss(N)` slice, one span per generation, nested on one track per `(pid, iid)`
- Loss reaches Chrome, Perfetto, JSONL and stdout; `gcmon combine` reproduces the spans from a JSONL capture
- `GC Loss` slices name what they are missing, as `missing_collections` (`413..431`, or `11` for one), `missing_count` and `missing_pause_total_ns`; the JSONL record carries `lost_from`
- Add `Cov` and `F` columns to the `--stats` table and a `gc_pause_gen_N_coverage` pyperf metric
- Show `Count` and `Sum` as `sampled/exact`, with a leading `~` where the second number is `F`-scaled
- Warn once per run when coverage falls below 90%
- Report per-generation totals since the interpreter started, as `gc_pause_gen_N_lifetime_count` / `_lifetime_sum` and a note under the `--stats` table
- Number the notes under the `--stats` table
- A process gcmon polled that never collected now gets a `Processes` slice, and a run in which nothing collected writes a trace instead of no file

### Bugfixes

- Fix GC events discarded by the poll loop; the cursor now tracks the target's `collections` counter per process, interpreter and generation
- Drop poll state when the wait policy gives up on a PID or the PID leaves the process tree, so a reused PID does not inherit its predecessor's counter
- Draw no `GC Loss` span when its bounds describe no interval; the collections still count toward `Count`, `Sum`, `Cov` and `F`, and the footer names how many were held back
- Sort `GC Loss` records into nesting order when converting; JSONL line order no longer matters
- Fix wrong durations on the Perfetto `Processes` track when process lifetimes overlap without nesting
- `Processes` slices record the observed span in `real_start_ts` / `real_end_ts` annotations

### Documentation

- Add [ADR-0015](docs/adr/0015-gc-loss-spans-on-their-own-track.md) on the `GC Loss` track
- Rewrite `docs/statistics.md` around `Cov`, `F`, the three intervals a cell can report, and the notes under the table
- Document the `GC Loss` track and the JSONL loss record in `docs/formats.md`
- Document the changed pyperf metrics, and that the lifetime metrics are not benchmark-scoped
- Add a README limitation for the ring-buffer bound and the read-cost floor

## Version 0.4.0 (2026-07-31)

### Breaking changes

- Remove `MonitorThread` (#63); use `MonitorLoop` instead
- Replace `gcmon.data.dur_to_us(ts_start_ns, ts_stop_ns)` with `gcmon.data.dur_to_ms(dur_ns)`

### Features

- Track RSS (Resident Set Size) of monitored processes in Perfetto traces (#55)
- Add `--rss` / `--rss-interval` CLI flags and `GCMON_RSS` / `GCMON_RSS_INTERVAL` env vars (#55)
- Add a `Read Time` row to the `--stats` table: the time each poll spends reading GC stats from the target

### Bugfixes

- Fix under-reported GC activity for child processes
- Fix doubled `Count` and `Sum` in the `--stats` table's `GC Pause` rows
- Fix `--rss` samples discarded with `--format chrome+perfetto`
- Warn that `--rss` has no effect with `jsonl` or `stdout`
- Wait for process termination before reading the return code (#65)

### Documentation

- Correct the JSONL `duration` units (seconds, not milliseconds) and the pyperf `gc_pause_*` units (milliseconds, not microseconds)
- Clarify that `gc_heap_size_p99` is a percentile over per-process peak live object counts, not over all samples
- Document the pyperf `gc_pause_count` metric
- Split the README into per-topic guides under `docs/`, indexed by `docs/README.md`, and add architecture decision records under `docs/adr/`
- Document where a Perfetto trace carries process command lines, with SQL for both forms
- Fix the screenshot URLs so they render on the PyPI project page

## Version 0.3.1 (2026-06-29)

### Bugfixes

- Fix PyPI classifiers

## Version 0.3.0 (2026-06-29)

### Breaking changes

- `TraceEvent.ts` is now stored in nanoseconds (was microseconds); fixes a 1000x compression bug in `ui.perfetto.dev`
- Chrome trace exporter now emits duration events (`B`/`E`) instead of complete events (`X`)
- Per-gen `G{gen}` counters now carry `collected`, `candidates`, `duration` and `uncollectable` (when non-zero), grouped under `GC Metrics`; `heap_size` is a single top-level counter per `(pid, tid)`
- Several metrics moved from counter events to slice args: `increment_size` on `GC Pause` / `Fill increment`, `candidates` on `Deduce Unreachable`, and `finalized_garbage_count` / `deleted_garbage_count` / `clear_weakrefs_count` on their own sub-step slices; `alive_size` is no longer a counter
- Remove `PollStatus.INVALID_PYTHON`, merged into `INVALID_PROCESS` (#32)

### Features

- `gcmon combine` supports `--output-format perfetto` for binary protobuf output, from chrome and jsonl inputs
- `gcmon monitor` / `run` support `--format chrome+perfetto`, writing both `<base>.json` and `<base>.pftrace`
- Add a top-level Perfetto `Processes` track holding one slice per pid, spanning its first-to-last event, named `Process <pid>` and carrying a `cmdline` annotation. Perfetto-only
- Emit a `Start Process` instant on each process track so its cmdline stays visible in the Perfetto UI
- Order Perfetto process tracks by first event timestamp. Needs trace processor 0.57+ and the canary UI channel
- Perfetto counter tracks sharing a metric name now share a Y-axis
- Add a per-PID wait policy

### Bugfixes

- Fix `GCMON_FORMAT=perfetto` falling back to `chrome`
- Fix `ControlServer` closing if not started, and leaking a `Listener` on failure
- Fix the `Processes` track slice END position; it is now emitted once at encoder close

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
