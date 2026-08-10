# How gcmon reads a process

gcmon runs outside the process it watches. Nothing is injected, no callback is
registered, and the target never pauses for a read. The `--stats` table, the trace
tracks and the pyperf metrics all inherit their limits from that arrangement, so this
page describes it once.

## The ring buffer

CPython writes each finished collection into a small fixed ring buffer, one per
generation, and gcmon reads the whole ring on every poll.

The ring is a window on the recent past rather than a queue. Nothing blocks when it
fills: the oldest record is overwritten and the collection it described is gone. The
read is passive, so the target runs at full speed whether or not anyone is watching,
and it gets no signal that a record went unread.

## Polling

`--rate` is the wait *between* rounds, and one round reads every monitored process
once. The period you sample at is `--rate` plus those reads, so a target with several
children drifts above the number you asked for. The `Read Time` row of the `--stats`
table measures the read side of it.

## Why records go missing

A target that runs collections more often than gcmon polls overwrites records before
anyone reads them. On a GC-heavy workload at default settings that is the normal case
rather than an edge case.

Raising `--rate` narrows the gap without closing it. Each round costs a read of every
monitored process, and that read time puts a floor under the interval.

## What gcmon recovers

CPython keeps a cumulative count of collections and a running total of pause time for
each generation. Both survive the overwrite, so the difference between two polls gives
how many collections gcmon missed and how long they took. Those are measurements, not
estimates.

The totals come out whole and the distribution does not:

- **Counts and sums cover every collection**, read or not.
- **Percentiles describe only what was read, and read high.** A long collection
  delays its successors, so it sits in its slot longer and is likelier to survive to
  the next poll.
- **Coverage** is the share gcmon read, and says how far the second point applies.

## Where this shows up

| Page | What it covers |
|---|---|
| [statistics.md](statistics.md) | `Cov` and `F`, the two-number cells, and the notes under the table |
| [formats.md](formats.md#gc-loss-slices) | The `GC Loss` track, one span per interval gcmon was blind for |
| [pyperf.md](pyperf.md) | `gc_pause_gen_N_coverage`, and which metrics are corrected |
