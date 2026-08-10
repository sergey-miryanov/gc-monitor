# How gcmon reads a process

gcmon runs outside the process it watches. It injects nothing and never pauses the
target to take a reading. It gets whatever CPython left in a buffer, and that is
where the `--stats` coverage column, the `GC Loss` track and the pyperf metrics all
come from.

## The ring buffer

CPython writes each finished collection into a small fixed ring buffer, one per
generation, and gcmon reads the whole ring on every poll.

The ring holds the newest few records. Nothing blocks when it fills: CPython
overwrites the oldest record, and the collection it described is gone. gcmon only
reads, so the target runs at full speed and gets no signal that a record went unread.

How few depends on the CPython version and build, so this page does not name a
number. gcmon counts the slots a poll returns and names the size it found in the
advisory it logs when coverage drops below 90%.

## Polling

`--rate` is the wait *between* rounds, and one round reads every monitored process
once. You sample at `--rate` plus those reads, so a run with several child processes
samples slower than the number you asked for. The `Read Time` row of the `--stats`
table measures the reads.

## Why records go missing

A target that runs collections more often than gcmon polls overwrites records before
anyone reads them. On a GC-heavy workload at default settings, expect it.

Raising `--rate` narrows the gap without closing it. Each round costs a read of every
monitored process, and that read time puts a floor under the interval.

## What gcmon recovers

CPython keeps a cumulative count of collections and a running total of pause time for
each generation. Both survive the overwrite, so the difference between two polls
measures how many collections gcmon missed and how long they took.

The correction reaches the totals only:

- **Counts and sums cover every collection**, read or not.
- **Percentiles describe only what was read, and read high.** A long collection
  delays its successors, so it sits in its slot longer and is likelier to survive to
  the next poll.
- **Coverage** is the share gcmon read, and says how far to trust the percentiles.

## Where this shows up

| Page | What it covers |
|---|---|
| [statistics.md](statistics.md) | `Cov` and `F`, the two-number cells, and the notes under the table |
| [formats.md](formats.md#gc-loss-slices) | The `GC Loss` track, one span per interval gcmon was blind for |
| [pyperf.md](pyperf.md) | `gc_pause_gen_N_coverage`, and which metrics are corrected |
