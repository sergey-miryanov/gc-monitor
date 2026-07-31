# Statistics

Use `--stats` to display a statistics table at the end of monitoring. The table reports GC pause durations (p50, p90, p95, p99) and counts per generation, with one row per monitored process plus an overall Total row.

Read it as: **P99 is your tail latency** (1 in 100 pauses is at least this long), **Sum divided by the monitoring wall time gives the share of the run spent in GC**, and **Count and Avg show how many pauses there were and how long a typical one took**. A P99 GC pause that exceeds your request SLO is a good starting point for tuning.

The last row, `Read Time`, is monitor-side cost rather than target-process cost: it measures how long each `_remote_debugging.get_gc_stats()` call took, recorded once per successful poll of every monitored PID and aggregated into a single row — with child processes its `Count` is polls × PIDs, and there is no per-PID breakdown. Use it to sanity-check `--rate`: that interval is a wait *between* polling rounds, so the effective sampling period is `--rate` plus the read time for every PID in the round, and a mean `Read Time` close to `--rate` means you are sampling at roughly half the rate you asked for.

## Example Output

```bash
$ gcmon 12345 --stats --table-format md

| PID   | Metric           | Count |     Sum |     Avg |     P50 |     P90 |     P95 |     P99 |
|-------|------------------|-------|---------|---------|---------|---------|---------|---------|
| Total | GC Pause(0)      |    42 |  35.200 |   0.838 |   0.720 |   1.500 |   1.800 |   2.400 |
|       | GC Pause(1)      |    18 |  72.000 |   4.000 |   3.500 |   6.800 |   7.500 |  10.200 |
|       | GC Pause(2)      |     5 | 125.000 |  25.000 |  22.000 |  38.000 |  42.000 |  50.000 |
|       |                  |       |         |         |         |         |         |         |
| 12345 | GC Pause(0)      |    42 |  35.200 |   0.838 |   0.720 |   1.500 |   1.800 |   2.400 |
|       | GC Pause(1)      |    18 |  72.000 |   4.000 |   3.500 |   6.800 |   7.500 |  10.200 |
|       | GC Pause(2)      |     5 | 125.000 |  25.000 |  22.000 |  38.000 |  42.000 |  50.000 |
|       |                  |       |         |         |         |         |         |         |
|       | Read Time        |   300 | 750.000 |   2.500 |   2.400 |   3.100 |   3.600 |   5.200 |
```

*Values shown in milliseconds. Metrics are reported per GC generation (0, 1, 2).*

## Without `[stats]` extra

By default, statistics are computed from an in-memory buffer of up to 1024 samples, with percentiles calculated exactly by sorting the buffered values. Once the buffer is full, older samples are discarded, so data is lost on long-running sessions.

## With `[stats]` extra

Install the optional `ddsketch` dependency for high-accuracy, memory-efficient statistics:

```bash
pip install gcmon[stats]
```

This installs [DDSketch](https://github.com/DataDog/sketches-py), which:
- Tracks **all** samples without a fixed buffer limit
- Computes approximate quantiles with 0.1% relative accuracy
- Uses constant memory regardless of monitoring duration

For long-running processes or high-frequency polling, the `[stats]` extra is recommended.
