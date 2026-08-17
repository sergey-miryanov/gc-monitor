# CLI Usage

`gcmon` takes three subcommands: `monitor`, `run` and `combine`. Without one it
monitors.

## What you'll see

gcmon stays quiet by default: it writes the trace to a file and exits when the
target ends or you press `Ctrl+C`. `-v` follows progress:

```bash
$ gcmon 12345 -v
[INFO] monitoring PID 12345 (chrome trace → gcmon.json)
[INFO] collected 42 GC events so far
...
[INFO] stopping (Ctrl+C)
[INFO] wrote 42 events to gcmon.json
```

Open the output in [Perfetto UI](https://ui.perfetto.dev), whose SQL panel
queries the trace directly; see
[Trace Analysis with Perfetto SQL](perfetto-sql.md).

## monitor

Monitor a running process by PID.

```bash
# Until interrupted, Chrome format
gcmon 12345
gcmon monitor 12345

gcmon 12345 -o gc_trace.json
gcmon 12345 -d 30 -v

# 100 polls a second
gcmon 12345 --output trace.json --rate 0.01
```

## run

Run a Python script or module with GC monitoring enabled.

**Important:** everything after `-s`/`--script` or `-m`/`--module` reaches the
target verbatim, so gcmon's own options go first.

```bash
gcmon run -s my_script.py

# A module, as `python -m` takes it
gcmon run --stats=full --table-format md -m test test_gc -v

# `--iterations 1000 --verbose` belong to benchmark.py, not to gcmon
gcmon run -s benchmark.py --iterations 1000 --verbose

gcmon run --format jsonl -o trace.jsonl --stats=total -m http.server 8000
```

Exactly one of `-s`/`--script` or `-m`/`--module`.

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
| `--format` | both | Output format: `chrome`, `perfetto`, `jsonl` or `stdout` (see [Output formats](formats.md)) | `chrome` |
| `--flush-threshold` | both | Number of events to buffer before flushing | `100` |
| `--stats <view>` | both | Show a statistics table at end of monitoring. The value is required: `total`, `full`, or one of `no`/`off`/`false`/`0` (see [`--stats`](#--stats)) | No table |
| `--table-format` | both | Table format: `plain` or `markdown`/`md` | `plain` |
| `--rss` | both | Track the target's Resident Set Size. `chrome` and `perfetto` only, and needs the `[cmdline]` extra (see [RSS Tracking](rss.md)) | `False` |
| `--rss-interval` | both | RSS sampling interval in seconds | `1.0` |

### `--stats`

The value is required, and it is one of these words:

| Value | Prints |
|-------|--------|
| `total` | the run-wide `Total` block, `Read Time` and the footer |
| `full` | that, plus one block per interpreter |
| `no`, `off`, `false`, `0` | no table, as an unset flag prints none |

Bare `--stats` is a parse error naming the words, and so is any word outside the
table, `all` and `1` included. The truthy opposites of the off words are absent
on purpose: "no table" is one outcome and "a table" is two, so `total` and
`full` are what you choose between. [Statistics](statistics.md) reads the two
views, and [ADR-0018](adr/0018-stats-requires-a-view-and-keeps-no-bare-alias.md)
records why neither keeps an alias.

`GCMON_STATS` takes the same words, so a variable already set to `0` still asks
for no table, and `--stats=no` declines a variable your shell profile or compose
file sets for every run. Blank reads as unset. Anything else stops the run at
startup, rather than letting a long capture finish and print nothing.

## Environment Variables

Each variable below sets a default for its flag, and a flag on the command line
beats it. A value a variable cannot read falls back to the default. The
exception is `GCMON_STATS`, which stops the run, since neither view is a safe
guess at what was meant.

| Variable | Equivalent flag | Description | Default |
|----------|----------------|-------------|---------|
| `GCMON_OUTPUT` | `-o, --output` | Output file path for trace data | `gcmon.json` (chrome), `gcmon.pftrace` (perfetto), `gcmon.jsonl` (JSONL) |
| `GCMON_RATE` | `-r, --rate` | Polling rate in seconds | `0.1` |
| `GCMON_DURATION` | `-d, --duration` | Monitoring duration in seconds | Until interrupted / script exits |
| `GCMON_VERBOSE` | `-v, --verbose` | Verbose level (integer or truthy value) | `0` |
| `GCMON_FORMAT` | `--format` | Output format: `chrome`, `perfetto`, `jsonl`, or `stdout` | `chrome` |
| `GCMON_FLUSH_THRESHOLD` | `--flush-threshold` | Number of events to buffer before flushing | `100` |
| `GCMON_STATS` | `--stats` | Statistics table view, in the words [`--stats`](#--stats) takes. Blank reads as unset; any other value stops the run | No table |
| `GCMON_TABLE_FORMAT` | `--table-format` | Table format: `plain`, `md`, or `markdown` | `plain` |
| `GCMON_RSS` | `--rss` | Enable RSS tracking (`1`, `true`, `yes`, `on`) | `False` |
| `GCMON_RSS_INTERVAL` | `--rss-interval` | RSS sampling interval in seconds | `1.0` |

## combine

Combine trace files into one, converting the format on the way if you ask.

```bash
gcmon combine trace1.json trace2.json -o combined.json

# `-n` starts every process at t=0
gcmon combine trace1.json trace2.json -o combined.json -n

gcmon combine trace1.jsonl --input-format jsonl --output-format perfetto -o combined.pftrace
```

| Option | Description | Default |
|--------|-------------|---------|
| `inputs` (required) | One or more input trace files | - |
| `-o, --output` (required) | Output file path for the combined trace | - |
| `--input-format` | Input format: `chrome` or `jsonl` | `chrome` |
| `--output-format` | Output format: `chrome`, `jsonl`, or `perfetto` | `chrome` |
| `-n, --normalize` | Normalize timestamps per PID so each process timeline starts at 0 | `False` |
