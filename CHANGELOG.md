# Changelog

## WIP

### Breaking changes

- The modules moved into layers, so every deep import path changed and the old ones are gone. `from gcmon import ...` still gives the same names
- `--format chrome`, `--format trace` and `--format chrome+perfetto` are parse errors: the flag takes `perfetto`, `jsonl` or `stdout`. A `.json` capture from an earlier release still opens in the Perfetto UI
- The default output is `gcmon.pftrace`, where it was `gcmon.json`. `--format jsonl` still defaults to `gcmon.jsonl`
- `GCMON_FORMAT` refuses a word `--format` would refuse and stops the run, where it used to fall back to the default without saying so
- A run that read no records writes no file, where it used to write an empty `gcmon.json`
- The pyperf hook spawns no monitor and publishes no GC metrics. Running the suite under `gcmon run` with `--inherit-environ=GCMON_CONTROL_ADDRESS` is required now, and the first worker fails the run when no monitor is listening
- The pyperf hook's metadata keys `gc_pause_*` and `gc_heap_size_p99` are gone
- The pyperf hook's `GCMON_PYPERF_HOOK_OUTPUT` and `GCMON_PYPERF_HOOK_TEMP_DIR` environment variables are gone
- `gcmon combine` reads JSONL only: `--input-format` is gone, `--output-format` takes `perfetto` or `jsonl` and defaults to `perfetto`. Handed a `.json` from an earlier release, it names the Chrome format instead of reporting malformed JSON

### Features

- A Perfetto trace is compressed: the same events in a file six to nine times smaller. It opens the same way, and there is nothing to run first
- `ControlClient.instant_msg` takes a `ts`, so an instant captured in a hot path can be sent after it and still land where it happened
- The pyperf hook marks where each benchmark ran: `gcmon:`-prefixed begin and end marks per measured region

### Bugfixes

- An instant sent close to the end of a run reaches the trace, where the last one a client sent could be dropped without a word

### Internal

- `gcmon.TraceExporter` is gone from the public surface
- Stability, correctness and performance improvements


## Version 0.6.0 (2026-08-21)

### Breaking changes

- `GC Loss` slice args drop the `missing_` prefix for the `lost_` one
- Bare `--stats` is a parse error: the flag requires a value, and `GCMON_STATS` takes the same words, so `GCMON_STATS=1` stops the run
- The `--stats` table reports one block per interpreter, not one per process: `PID:IID` heads every row, `12345:0` included. `Total` is the only blended row
- The low-coverage warning measures each interpreter on its own and names the least covered. It fires where a busy interpreter used to lift the whole PID over the 90% floor
- `--rate` and `GCMON_RATE` take a plain decimal number of seconds, `0.001` or more: `1e-3` and `0.0005` are refused where they used to be accepted

### Features

- `--stats` takes the view to print: `total` for the run-wide block, `full` for that plus one block per interpreter, `no`/`off`/`false`/`0` for no table
- The low-coverage warning drops the ring-buffer explanation and suggests a smaller `--rate`
- The end-of-run summary counts the events gcmon reconstructed and the share it observed: `Total events: 1234 (+8566 reconstructed, 12.6% observed)`
- The end-of-run summary reports the ticks that ran against the ticks scheduled, `Ticks: 188 of 600 scheduled`, and says whether polling more often can help a lossy run
- The lifetime note under the `--stats` table says what it covers: `summed over 3 interpreters in 2 processes`
- An interpreter's statistics settle when its process exits: its percentiles cover its whole life
- Each process that held a reused PID gets its own `--stats` block, the second headed `12345:0#2`
- Warn when an interpreter gets no row because 256 were already running, and count the ones left out in a footer note. Their records still reach `Total`
- `gcmon --version` prints the installed version

### Bugfixes

- `--rate` is the interval between poll starts, not the wait after each poll: a wide process tree used to stretch every interval by what its reads took
- `gcmon.__version__` reports the installed version; it had said `0.1.0` since `0.2.0`
- Stop a reused PID inheriting its predecessor's `--stats` row, which put two processes' records under one heading
- Stop a reused PID's lifetime totals overwriting its predecessor's, which made the note under the table drop mid-run

### Internal

- Stability, correctness and performance improvements

## Version 0.5.0 (2026-08-14)

### Breaking changes

- Slice names drop the `gen=` prefix: `GC Pause(0)`, `GC Loss(0)`, `Mark Alive(0)` and the rest. Categories are unchanged, so `gc.pause(gen=0)` still matches
- A `Processes` slice now spans how long the process was alive, not how long it was collecting (`real_start_ts` / `real_end_ts` annotations)
- `Count` and `Sum` now include the collections gcmon missed, in the `--stats` table and the pyperf `gc_pause_gen_N_count` / `_sum` / `gc_pause_count` metrics

### Features

- Detect GC records the target ran without gcmon reading them, and draw each blind poll interval as a `GC Loss` slice named for the generations that lost records (`GC Loss(0,2)`)
- `GC Loss` slices carry the interval's coverage, missing collection counts and missing pause time
- Add `Cov` and `F` columns to the `--stats` table and a `gc_pause_gen_N_coverage` pyperf metric
- Show `Count` and `Sum` as `sampled/exact`, with a leading `~` where the second number is `F`-scaled
- Warn once per run when coverage falls below 90%
- Report per-generation totals since the interpreter started, as `gc_pause_gen_N_lifetime_count` / `_lifetime_sum`
- Write a `Processes` slice for a process that never collected, and a trace for a run in which nothing collected

### Bugfixes

- Fix GC events discarded by the poll loop
- Fix a reused PID inheriting its predecessor's GC counts

### Documentation

- Add [ADR-0015](docs/adr/0015-gc-loss-spans-on-their-own-track.md) on the `GC Loss` track
- Add `docs/monitoring.md` on how gcmon collects the GC record stream and why records go missing
- Rewrite `docs/statistics.md` for `GC Loss` support
- Document the `GC Loss` track and spans in `docs/formats.md`
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
