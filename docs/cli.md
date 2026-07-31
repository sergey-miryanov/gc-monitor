# CLI Usage

The `gcmon` command uses subcommands (`monitor`, `run`, `combine`). If no subcommand
is given, `monitor` is used by default.

## What you'll see

By default gcmon stays quiet — the trace is written to a file and gcmon
exits when the target ends or you press `Ctrl+C`. Use `-v` to follow
progress:

```bash
$ gcmon 12345 -v
[INFO] monitoring PID 12345 (chrome trace → gcmon.json)
[INFO] collected 42 GC events so far
...
[INFO] stopping (Ctrl+C)
[INFO] wrote 42 events to gcmon.json
```

Open the output file in [Perfetto UI](https://ui.perfetto.dev) — the
built-in SQL panel lets you query the trace directly; see
[Trace Analysis with Perfetto SQL](perfetto-sql.md).

## monitor

Monitor a running process by PID.

```bash
# Monitor a process until interrupted (Chrome format)
gcmon 12345
# or:
gcmon monitor 12345

# Monitor with custom output file
gcmon 12345 -o gc_trace.json

# Monitor for a specific duration with verbose output
gcmon 12345 -d 30 -v

# High-frequency monitoring
gcmon 12345 --output trace.json --rate 0.01
```

## run

Run a Python script or module with GC monitoring enabled.

**Important:** All options and arguments after `-s`/`--script` or `-m`/`--module` are passed verbatim to the target — they are **not** interpreted by gcmon. Place gcmon options before the target.

```bash
# Run a script
gcmon run -s my_script.py

# Run a module (like python -m)
gcmon run --stats --table-format md -m test test_gc -v

# Pass arguments to the script; everything after -s goes to the target
gcmon run -s benchmark.py --iterations 1000 --verbose

# Run a module with GC monitoring options
gcmon run --format jsonl -o trace.jsonl --stats -m http.server 8000
```

You must specify exactly one of `-s`/`--script` or `-m`/`--module`.

## Options for `monitor` and `run`

| Option | Applies to | Description | Default |
|--------|------------|-------------|---------|
| `pid` (required) | `monitor` | Process ID to monitor | - |
| `-s, --script <path>` | `run` | Python script path to run | - |
| `-m, --module <name>` | `run` | Module name to run (like `python -m`) | - |
| `-o, --output` | both | Output file path for trace data | `gcmon.json` (chrome), `gcmon.pftrace` (perfetto), `gcmon.jsonl` (JSONL) |
| `-r, --rate` | both | Polling rate in seconds | `0.1` |
| `-d, --duration` | both | Monitoring duration in seconds | Until interrupted / script exits |
| `-v, --verbose` | both | Enable verbose output (`-v` for INFO, `-vv` for DEBUG) | `0` |
| `--format` | both | Output format: `chrome` (Chrome Trace Event), `perfetto` (Perfetto binary protobuf), `jsonl` (JSONL to file), or `stdout` (JSONL to stdout) (see [Output formats](formats.md)) | `chrome` |
| `--flush-threshold` | both | Number of events to buffer before flushing | `100` |
| `--stats` | both | Show statistics table at end of monitoring (see [Statistics](statistics.md)) | `False` |
| `--table-format` | both | Table format: `plain` or `markdown`/`md` | `plain` |
| `--rss` | both | Track RSS (Resident Set Size) of monitored process (`chrome` and `perfetto` formats; requires `[cmdline]` extra — see [RSS Tracking](rss.md)) | `False` |
| `--rss-interval` | both | RSS sampling interval in seconds | `1.0` |

## Environment Variables

All CLI options can be overridden via environment variables. CLI flags take precedence.

| Variable | Equivalent flag | Description | Default |
|----------|----------------|-------------|---------|
| `GCMON_OUTPUT` | `-o, --output` | Output file path for trace data | `gcmon.json` (chrome), `gcmon.pftrace` (perfetto), `gcmon.jsonl` (JSONL) |
| `GCMON_RATE` | `-r, --rate` | Polling rate in seconds | `0.1` |
| `GCMON_DURATION` | `-d, --duration` | Monitoring duration in seconds | Until interrupted / script exits |
| `GCMON_VERBOSE` | `-v, --verbose` | Verbose level (integer or truthy value) | `0` |
| `GCMON_FORMAT` | `--format` | Output format: `chrome`, `perfetto`, `jsonl`, or `stdout` | `chrome` |
| `GCMON_FLUSH_THRESHOLD` | `--flush-threshold` | Number of events to buffer before flushing | `100` |
| `GCMON_STATS` | `--stats` | Enable statistics table (`1`, `true`, `yes`, `on`) | `False` |
| `GCMON_TABLE_FORMAT` | `--table-format` | Table format: `plain`, `md`, or `markdown` | `plain` |
| `GCMON_RSS` | `--rss` | Enable RSS tracking (`1`, `true`, `yes`, `on`) | `False` |
| `GCMON_RSS_INTERVAL` | `--rss-interval` | RSS sampling interval in seconds | `1.0` |

## combine

Combine multiple trace files into a single trace, with optional per-PID timestamp normalization.

```bash
# Combine Chrome Trace files
gcmon combine trace1.json trace2.json -o combined.json

# Combine with timestamp normalization (each process starts at t=0)
gcmon combine trace1.json trace2.json -o combined.json -n

# Convert JSONL to Perfetto
gcmon combine trace1.jsonl --input-format jsonl --output-format perfetto -o combined.pftrace
```

| Option | Description | Default |
|--------|-------------|---------|
| `inputs` (required) | One or more input trace files | - |
| `-o, --output` (required) | Output file path for the combined trace | - |
| `--input-format` | Input format: `chrome` or `jsonl` | `chrome` |
| `--output-format` | Output format: `chrome`, `jsonl`, or `perfetto` | `chrome` |
| `-n, --normalize` | Normalize timestamps per PID so each process timeline starts at 0 | `False` |
