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
  - `name`: Event name (e.g., "GC Pause (gen=0)")
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

## Tips for Writing Queries

- **Timestamps are in nanoseconds:** Divide by `1e6` for milliseconds, `1e9` for seconds
- **Use `EXTRACT_ARG`:** Access slice annotations (e.g., `EXTRACT_ARG(arg_set_id, 'heap_size')`)
- **Filter by name:** Use `LIKE` patterns to match specific event types
- **Join tracks:** Connect slices/counters to process/thread information via track IDs
- **Use window functions:** `LAG()`, `LEAD()`, `ROW_NUMBER()` for time-series analysis

## Further Reading

- [Perfetto SQL Getting Started](https://perfetto.dev/docs/analysis/perfetto-sql-getting-started)
- [Perfetto SQL documentation](https://perfetto.dev/docs/analysis/perfetto-sql-syntax)
