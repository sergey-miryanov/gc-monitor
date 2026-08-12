# How gcmon reads a process

gcmon collects a stream of GC records from a running process. Each time the
collector finishes a run, CPython writes one record describing it: the
generation, the start and stop times, what the run freed, the heap size, and
the target's running totals. That stream is the product. Watch it live, or
write it to a file and post-process it later.

gcmon sits outside the process it watches. It injects nothing and never pauses
the target. It reads what CPython already wrote, so the target is not slowed
and never learns anyone is reading.

## The ring buffer

CPython keeps one small fixed buffer per generation and writes each new record
into it. gcmon reads the whole buffer on every poll.

The buffer holds the newest few records and never blocks. When it is full,
CPython drops the oldest record to make room, and nothing else describes the
run it held.

The size depends on the CPython version and build. A free-threaded build keeps
one record per generation, so only the newest survives to the next poll. gcmon
counts the slots a poll returns instead of assuming a size, and names that size
when it reports what it missed.

## Polling

`--rate` is the wait *between* rounds, in seconds. One round reads every
monitored process once, so the real interval is `--rate` plus those reads. Watch
several child processes and you sample slower than the number you asked for.

## Records gcmon misses

A target whose collector runs faster than gcmon polls drops records before any
poll reads them. On a GC-heavy workload at default settings, expect it.

A shorter `--rate` narrows the gap without closing it. Every round still reads
each monitored process, and that read time puts a floor under the interval.

A lost record takes its timestamps with it, so nothing says when that run
happened. The two polls around it still bound it: a record goes missing only
between two consecutive reads, so that interval is as tight as any bound gets.

## What the counters recover

For each generation CPython keeps a running count of finished runs and a running
total of the time they took. Both keep climbing whether or not a record
survives, so the difference between two polls gives how many runs gcmon missed
and what they cost together.

That is subtraction over the target's own counters, so three numbers in the
stream are exact rather than estimated:

- how many runs happened between two polls, read or not
- how many of them gcmon read
- how much pause time the rest took

The records themselves do not come back. What a lost run freed, how long it took
on its own, and where in the interval it happened are gone. Averages and
percentiles therefore describe what gcmon read, not what ran.
