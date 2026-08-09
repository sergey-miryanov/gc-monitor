# Trace Analysis with Perfetto SQL

When you export traces in Perfetto format (`.pftrace`), you can use Perfetto's SQL query interface to perform advanced analysis beyond what the UI provides. PerfettoSQL extends SQLite's SQL dialect — any query valid in SQLite also works in PerfettoSQL.

The trace data is stored in a structured schema that you can query directly.

## Accessing the SQL Interface

1. Open your `.pftrace` file in [Perfetto UI](https://ui.perfetto.dev)
2. Press `Ctrl+Space` (or `Cmd+Space` on Mac) to open the SQL query panel
3. Enter your SQL query and press `Run`

## Understanding the Schema

gcmon traces use the standard Perfetto schema:

- **`slice`** table: Contains slice events (GC pauses, sub-steps)
  - `name`: Event name (e.g., "GC Pause(0)")
  - `ts`: Start timestamp (nanoseconds)
  - `dur`: Duration (nanoseconds)
  - `arg_set_id`: Reference to arguments/annotations
- **`counter`** table: Contains counter values (live object count, collected objects, etc.)
  - `track_id`: Reference to the counter track
  - `ts`: Timestamp (nanoseconds)
  - `value`: Counter value
- **`counter_track`** table: Contains counter track metadata
  - `id`: Track ID
  - `name`: Track name (e.g., "G0 collected", "heap_size")
- **`process_track`** / **`thread_track`**: Contains process/thread information
  - `id`: Track ID
  - `pid` / `tid`: Process/thread ID
  - `source_arg_set_id`: Reference to the track's args, including the process command line
- **`args`** table: Key/value arguments for slices and tracks
  - `arg_set_id`: The set this argument belongs to
  - `key` / `flat_key`: Argument name — track args use bare names (`description`), slice debug annotations are prefixed (`debug.cmdline`)
  - `string_value` / `int_value`: The value

> **Note:** `process.cmdline` always returns `NULL`. The trace processor does not
> surface `ProcessDescriptor.cmdline`, so query the process track's `description`
> or the `debug.cmdline` annotation instead — see below.

## Example: Replicating the Stats Table

The [`--stats` CLI option](statistics.md) produces a summary table at runtime. You can replicate this analysis using SQL:

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

This query:
- Filters for GC pause slices
- Calculates count, sum, average, and percentiles (p50, p90, p95, p99)
- Groups results by metric name

## Example: Querying RSS Values

When `--rss` is enabled, RSS samples are stored in the `counter` table with track name `"rss"`. You can query memory usage over time per PID:

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
WHERE ct.name like 'rss%'
ORDER BY p.start_ts, c.ts
```

## Example: Querying Process Command Lines

Requires the [`[cmdline]` extra](rss.md#the-cmdline-extra). gcmon writes the
command line to [three places](formats.md#process-command-lines); two of them are
reachable from SQL.

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

The same string is attached to each `Process {pid}` slice on the `Processes`
lifetime track as a `cmdline` debug annotation, which pairs it with the process's
start and end times:

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

Both return no rows when the extra is missing or the command line could not be
collected; the rest of the trace is unaffected.

**Do not read `dur` on this track as an observed duration.** Every pid's slice
lives on the one `Processes` track, and slices on a single Perfetto track have to
nest, so where two pids' spans cross, the earlier one's end is pulled back to just
before the later one begins. `s.dur` is what could be drawn; `real_start_ts` and
`real_end_ts` are what gcmon observed. Both are on every slice, clipped or not, so
the query is the same either way — and the difference can be total, since a span
crossed by one starting a microsecond later is clipped to a microsecond.

Every monitored process gets exactly one slice, so you may join these to pids
one-to-one — including one gcmon polled but that never collected, which has no
process track and no `cmdline` annotation, only the span. Some slices have
`dur = 0`: a process observed at a single instant, or clipped down to nothing.
They are drawn anyway, precisely so that the annotations below can be read off
them.

Two caveats. Processes still alive when monitoring stops share an end timestamp
and so nest, and the trace processor closes at most **512** nested slices; past
that they come back with `dur = -1` and no diagnostic, so filter on `s.dur >= 0`
if a capture may have had more than 512 processes running at the end. And
`gcmon combine` spans cover GC activity only, so spans are comparable across
traces captured the same way.

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

- **Timestamps are in nanoseconds:** Divide by `1e6` for milliseconds, `1e9` for seconds
- **Use `EXTRACT_ARG`:** Access slice annotations (e.g., `EXTRACT_ARG(arg_set_id, 'heap_size')`)
- **Filter by name:** Use `LIKE` patterns to match specific event types
- **Join tracks:** Connect slices/counters to process/thread information via track IDs
- **Use window functions:** `LAG()`, `LEAD()`, `ROW_NUMBER()` for time-series analysis

## Further Reading

- [Perfetto SQL Getting Started](https://perfetto.dev/docs/analysis/perfetto-sql-getting-started)
- [Perfetto SQL documentation](https://perfetto.dev/docs/analysis/perfetto-sql-syntax)
