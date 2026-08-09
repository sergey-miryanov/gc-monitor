# Statistics

Use `--stats` to display a statistics table at the end of monitoring. The table reports GC pause durations (p50, p90, p95, p99) and counts per generation, with one row per monitored process plus an overall Total row.

Read it as: **P99 is your tail latency** (1 in 100 pauses is at least this long), **Sum divided by the monitoring wall time gives the share of the run spent in GC**, and **Count and Avg show how many pauses there were and how long a typical one took**. A P99 GC pause that exceeds your request SLO is a good starting point for tuning.

The last row, `Read Time`, is monitor-side cost rather than target-process cost: it measures how long each `_remote_debugging.get_gc_stats()` call took, recorded once per successful poll of every monitored PID and aggregated into a single row — with child processes its `Count` is polls × PIDs, and there is no per-PID breakdown. Use it to sanity-check `--rate`: that interval is a wait *between* polling rounds, so the effective sampling period is `--rate` plus the read time for every PID in the round, and a mean `Read Time` close to `--rate` means you are sampling at roughly half the rate you asked for.

## Example Output

```bash
$ gcmon 12345 --stats --table-format md

| PID   | Metric                   |   Count |             Sum |    Avg |    P50 |    P90 |    P95 |    P99 |    Cov |      F |
|-------|--------------------------|---------|-----------------|--------|--------|--------|--------|--------|--------|--------|
| Total | GC Pause(0)              |  42/210 |  55.795/240.595 |  1.328 |  1.323 |  1.922 |  2.304 |  2.373 |  20.0% |  4.312 |
|       | GC Pause(1)              |   18/25 | 116.416/154.216 |  6.468 |  7.026 |  8.468 |  8.890 |  9.655 |  72.0% |  1.325 |
|       | GC Pause(2)              |       5 |         167.709 | 33.542 | 38.937 | 42.645 | 43.270 | 43.770 | 100.0% |  1.000 |
|       |                          |         |                 |        |        |        |        |        |        |        |
|       | GC Deduce Unreachable(0) | 42/~210 | 37.197/~160.397 |  0.886 |  0.882 |  1.281 |  1.536 |  1.582 |  20.0% |  4.312 |
|       | GC Deduce Unreachable(1) |  18/~25 | 77.611/~102.811 |  4.312 |  4.684 |  5.645 |  5.927 |  6.437 |  72.0% |  1.325 |
|       | GC Deduce Unreachable(2) |       5 |         111.806 | 22.361 | 25.958 | 28.430 | 28.846 | 29.180 | 100.0% |  1.000 |
|       |                          |         |                 |        |        |        |        |        |        |        |
| 12345 | GC Pause(0)              |  42/210 |  55.795/240.595 |  1.328 |  1.323 |  1.922 |  2.304 |  2.373 |  20.0% |  4.312 |
|       | GC Pause(1)              |   18/25 | 116.416/154.216 |  6.468 |  7.026 |  8.468 |  8.890 |  9.655 |  72.0% |  1.325 |
|       | GC Pause(2)              |       5 |         167.709 | 33.542 | 38.937 | 42.645 | 43.270 | 43.770 | 100.0% |  1.000 |
|       |                          |         |                 |        |        |        |        |        |        |        |
|       | Read Time                |     300 |         750.000 |  2.500 |  2.400 |  3.100 |  3.600 |  5.200 |        |        |

1. Coverage: gen 0 20.0% gen 1 72.0%. Count and Sum read sampled/exact; percentiles are sampled and read high.
```

*Values shown in milliseconds. Metrics are reported per GC generation (0, 1, 2).*

## Three intervals, and which one a cell reports

CPython exports GC records through a fixed ring buffer of 11 slots for generation 0 and 3 for the older two. A target collecting faster than gcmon polls overwrites records before anyone reads them. Every number in the table describes one of three intervals, and you cannot read the table without knowing which:

- **Sampled**: the records gcmon read. `Avg` and every percentile are sampled.
- **Exact, over the observed span**: every collection between the first and last record gcmon saw, including the ones it never read. `Count` and `Sum` report this. CPython's `collections` and `duration` counters are cumulative, so the difference between two polls gives the number of missed collections and the pause time they took. It is a reconstruction rather than an estimate.
- **Lifetime**: everything the interpreter has collected since it started. It appears in the footer under the table, and as `pause_gen_N_lifetime_*` in [pyperf metadata](pyperf.md). It covers the whole history including the monitored part, so it does not compare with the other two, and it stays out of `Cov` and `F`. Collections that ran before gcmon attached are not loss, and no poll rate would have caught them.

The observed span starts at the first record gcmon read, not at process start. gcmon cannot tell "ran before we attached" from "lost", so anything earlier falls outside the span.

## `Count` and `Sum` cells: `sampled/exact`

Where the two differ, the cell shows both:

```
Count           Sum
42/210          55.795/240.595
```

gcmon read 42 gen-0 pauses totalling 55.795 ms. 210 collections ran in that window, taking 240.595 ms. When nothing was lost the cell carries a single number, since a run that saw everything is worth saying once rather than twice in every cell.

Sub-phase rows (`GC Mark Alive`, `GC Deduce Unreachable`, `GC Delete Garbage`, …) mark their second number with a leading `~`: `42/~210`. CPython accumulates a total for the whole pause only, so a sub-phase has no exact counterpart. Those numbers are the sampled value scaled by `F`, and they are estimates.

## The `Cov` and `F` columns

**`Cov`** is the share of collections gcmon read, `sampled ÷ exact`. At `20.0%`, four out of five collections in that row were overwritten before a poll reached them. It will not round up to a completeness the cells beside it deny: a row that lost 8 of 1771 collections prints `<100.0%`.

**`F`** is the multiplier taking a sampled pause sum to the exact one, `exact_sum ÷ sampled_sum`. It scales the sub-phase rows. Like `Cov`, it refuses to round to a value claiming nothing was lost, and prints `>1.000` instead.

Both are blank on rows with no generation, such as `Read Time`.

If coverage falls below 90%, gcmon logs one advisory per run naming the ring-buffer size and the read cost. Lowering `--rate` past about 0.6 ms per process buys nothing, since that is what a single `get_gc_stats` read costs.

## Percentiles are sampled, biased high, and not corrected

`P50` through `P99` describe the collections gcmon read rather than the collections that ran, and the difference is not random. A long collection delays its successors, so it sits in its ring slot for longer and is likelier to survive until the next poll. Long pauses are over-represented among the survivors, so **the reported percentiles read high**, the more so the lower `Cov` is.

`F` does not fix this. It is a ratio of two totals, so applying it to a quantile would assume the sampled and unsampled pauses share a distribution, which is the assumption the bias violates. Nothing places an unread collection in the distribution, so gcmon reports the quantiles it measured and documents the bias.

On a low-`Cov` row, trust the counts and sums and distrust the shape.

The trace draws where the missing collections were, on a `GC Loss` track. See [output formats](formats.md#gc-loss-slices).

## The notes under the table

Below the table gcmon prints a numbered note for each thing the cells cannot say. Which of the three appear depends on the run, and a run that saw every collection of a target that collected nothing before gcmon attached prints no footer at all. The numbers are there because that mix varies: a reader cannot learn the order, and the number is what separates one note from the next when two of them wrap across a narrow terminal. Numbering starts at 1 whatever the mix, so a lone note still reads `1.`.

**1. Coverage.**

```
1. Coverage: gen 0 20.0% gen 1 72.0%. Count and Sum read sampled/exact; percentiles are sampled and read high.
```

The `Cov` column gathered across every PID, plus the rule for reading the two-number cells. It appears whenever anything was lost, and only the generations that lost something are listed.

**2. Lifetime totals.**

```
2. Since interpreter start, monitored window included: gen 0 4820 in 6231.400 ms.
```

The third interval above. It covers each interpreter's whole history including the monitored part, so it neither adds to nor subtracts from any cell, and it stays out of `Cov` and `F`.

**3. Loss spans held back.**

```
3. Loss spans not drawn: gen 0 2 (bounds arrived reversed, so the interval could not be placed). Counts above are unaffected.
```

A loss window is bounded at one end by the `ts_start` of the first record read after the blind interval, and at the other by the newest `ts_stop` seen anywhere in that interpreter. When the second does not precede the first, the window describes no interval, the overwritten records had nowhere to run, and gcmon draws no slice for it. The count is spans held back per generation, not collections.

**The note names no cause, because two reach it and gcmon cannot tell them apart.** One is ordinary. A poll copies an interpreter's rings over about 0.6 ms while the target keeps collecting, so a collection finishing after its own ring was copied but before a later ring's is missed by that poll, while the later ring carries a newer `ts_stop`. The window then opens after the record it bounds, with nothing misbehaving. The other is a target bug: CPython publishes `ts_stop` last so a remote reader never selects a half-written record, but those stores carry no memory barrier, and a weakly-ordered machine can hand the reader a record assembled from two collections. Neither leaves a fingerprint the other does not.

The line does rule out gcmon having dropped something, so lowering `--rate` will not change it.

**Counts above are unaffected** is exact rather than a hedge. gcmon counted the collections the window measured before it looked at the bounds, since `lost_count` is arithmetic on the ring's own counters with no timestamp in it. `Count`, `Sum`, `Cov` and `F` read the same as they would had the span been drawn; only the trace is a bar short. See [ADR-0015](adr/0015-gc-loss-spans-on-their-own-track.md) for why it is held back rather than drawn backwards.

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

The buffer bounds the percentiles alone. `Count`, `Sum`, `Cov`, `F` and the lifetime totals are running values, and do not depend on how many samples the buffer retains.
