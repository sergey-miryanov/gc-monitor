# RSS Tracking

RSS (Resident Set Size) tracking samples the physical memory usage of each monitored process and emits it as a process-level counter track.

Supported by the `chrome` and `perfetto` formats. The `jsonl` and `stdout` formats discard RSS samples; `--rss` logs a warning when combined with them.

## How to Use

```bash
# Enable RSS tracking with Perfetto output (default 1s interval)
gcmon 12345 --format perfetto -o trace.pftrace --rss

# Custom sampling interval
gcmon 12345 --format perfetto --rss --rss-interval 0.5
```

Requires the `[cmdline]` extra (which installs `psutil`). Without psutil, `--rss` is silently ignored and an info log is emitted.

## How It Works

- RSS sampling runs inside the GC poll loop, so its effective rate is capped by `--rate`. If `--rss-interval` is shorter than `--rate`, a warning is logged and RSS is sampled at the poll rate.
- Only PIDs that returned a successful GC poll status are sampled — no stale data for dead processes.
- The counter track is process-level (`tid=-1`), parented directly to the process track outside the `GC Metrics` group.
- The `rss` counter track displays in the Perfetto UI with the name `"rss"` and the value in bytes.
- Graceful degradation: if `psutil` is not installed, `--rss` is ignored without crashing.

## SQL Query Example

See [Example: Querying RSS Values](perfetto-sql.md#example-querying-rss-values).
