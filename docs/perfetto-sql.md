# Trace Analysis with Perfetto SQL

Perfetto's SQL panel opens gcmon's `.pftrace`. PerfettoSQL is SQLite with
extensions.

[Output formats](formats.md#perfetto-output) lists what a capture holds. The
trace processor puts gcmon's slice args under `debug.`: a per-generation loss
count is `debug.gen0.lost_count`.

## Accessing the SQL Interface

1. Open your `.pftrace` file in [Perfetto UI](https://ui.perfetto.dev)
2. Press `Ctrl+Space` (or `Cmd+Space` on Mac) to open the SQL query panel
3. Enter your SQL query and press `Run`

## Understanding the Schema

gcmon traces use the standard Perfetto schema:

- **`slice`**: GC pauses and sub-steps
  - `name` (`"GC Pause(0)"`), `ts` and `dur` in nanoseconds, `arg_set_id`
- **`counter`**: counter samples
  - `track_id`, `ts`, `value`
- **`counter_track`**: one row per counter track
  - `id`, `name` (`"G0 collected"`, `"Thread 0 heap_size"`)
- **`process_track`** / **`thread_track`**: process and thread rows
  - `id`, `pid` / `tid`, and `source_arg_set_id` for the track's own args
- **`args`**: key/value arguments for slices and tracks
  - `arg_set_id`, `string_value` / `int_value`
  - `key` / `flat_key`: bare for a track arg (`description`), prefixed for a
    slice annotation (`debug.cmdline`)

> **Note:** `process.cmdline` always returns `NULL`, since the trace processor
> does not surface `ProcessDescriptor.cmdline`. Query the process track's
> `description` or the `debug.cmdline` annotation, both below.

## Example: Replicating the Stats Table

SQL reproduces the [`--stats` table](statistics.md):

```sql
-- GC pause statistics
SELECT
name,
    COUNT(dur) AS count,
    ROUND(SUM(dur) / 1e6, 2) as dur_ms,
    ROUND(AVG(dur) /1e6, 4) AS avg_ms,
    -- Calculate P50, P90, and P99 in milliseconds
    ROUND(PERCENTILE(dur, 50) / 1e6, 4) AS P50_dur_ms,
    ROUND(PERCENTILE(dur, 90) / 1e6, 4) AS P90_dur_ms,
    ROUND(PERCENTILE(dur, 95) / 1e6, 4) AS P95_dur_ms,
    ROUND(PERCENTILE(dur, 99) / 1e6, 4) AS P99_dur_ms
FROM slice
WHERE category IS NOT NULL
GROUP BY name
ORDER BY IF(parent_id IS NULL, 0, 1), name
```

## Example: Querying RSS Values

Under `--rss`, samples land in the `counter` table on a track named `rss`:

```sql
-- RSS values per PID (requires --rss)
SELECT
    p.pid,
    (c.ts - p.start_ts) / 1e9 AS sec_from_start,
    ROUND(c.value / 1e6, 2) AS rss_mb
FROM counter c
JOIN counter_track ct ON c.track_id = ct.id
JOIN process_counter_track pt on ct.id = pt.id
JOIN process p ON pt.upid = p.upid
WHERE ct.name like '%rss%'
ORDER BY p.start_ts, c.ts
```

## Example: Querying Process Command Lines

Requires the [`[cmdline]` extra](rss.md#the-cmdline-extra). gcmon writes the
command line to [three places](formats.md#process-command-lines); two of them
are reachable from SQL.

The process track's `description` holds the space-joined command line:

```sql
-- Command line per PID, from the process track description
SELECT p.pid, a.string_value AS cmdline
FROM args a
JOIN process_track pt ON a.arg_set_id = pt.source_arg_set_id
JOIN process p ON p.upid = pt.upid
WHERE a.key = 'description'
ORDER BY p.pid
```

A `cmdline` debug annotation carries the same string on each slice of the
`Processes` lifetime track, which pairs it with that process's start and end
times. Where a PID was handed on, the annotation is per process and the
description above is the first process's:

```sql
-- Command line alongside each process's lifetime
SELECT
    s.name,
    s.ts,
    s.dur,
    EXTRACT_ARG(s.arg_set_id, 'debug.cmdline') AS cmdline,
    EXTRACT_ARG(s.arg_set_id, 'debug.real_end_ts')
        - EXTRACT_ARG(s.arg_set_id, 'debug.real_start_ts') AS observed_dur
FROM slice s
JOIN track t ON s.track_id = t.id
WHERE t.name = 'Processes'
ORDER BY s.ts
```

Both return nothing when the extra is missing or gcmon could not read the
command line. The rest of the trace still queries.

**Do not read `dur` on this track as an observed duration.** Crossing spans
cut each other short, sometimes to a microsecond. `s.dur` is what Perfetto
could draw; `real_start_ts` and `real_end_ts` are what gcmon observed, and
every slice carries them whether it was cut or not.

Every monitored process gets exactly one slice, a process that never collected
included. A PID the operating system handed out twice has one slice per
process, so join to `p.pid` many-to-one and read the `pid_epoch` annotation to
tell them apart; the name carries it too, as `Process 12345#2` from the second
process on. A `dur = 0` slice is one observed at a single instant, or cut down
to nothing.

Processes still alive when monitoring stops share an end timestamp and nest,
and the trace processor closes at most **512** nested slices. Past that they
return `dur = -1` with no diagnostic, so filter on `s.dur >= 0` if more than
512 processes may have been running at the end. Compare spans only across
traces captured the same way: `gcmon combine` spans cover GC activity alone.

```sql
-- Processes whose drawn duration is shorter than what gcmon observed
SELECT
    s.name,
    s.dur AS drawn_dur,
    EXTRACT_ARG(s.arg_set_id, 'debug.real_end_ts')
        - EXTRACT_ARG(s.arg_set_id, 'debug.real_start_ts') AS observed_dur
FROM slice s
JOIN track t ON s.track_id = t.id
WHERE t.name = 'Processes'
  AND observed_dur > s.dur
ORDER BY observed_dur - s.dur DESC
```

## Tips for Writing Queries

- Timestamps are nanoseconds. Divide by `1e6` for milliseconds, `1e9` for
  seconds.
- `EXTRACT_ARG` reads a slice annotation:
  `EXTRACT_ARG(arg_set_id, 'heap_size')`.

## Further Reading

- [Perfetto SQL Getting Started](https://perfetto.dev/docs/analysis/perfetto-sql-getting-started)
- [Perfetto SQL documentation](https://perfetto.dev/docs/analysis/perfetto-sql-syntax)
