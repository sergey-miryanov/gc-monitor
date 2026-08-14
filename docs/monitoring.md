# How gcmon reads a process

gcmon collects a stream of GC records from a running process. Each time the
collector finishes a run, CPython writes one record describing it: the
generation, the start and stop times, what the run freed, the heap size, and the
target's running totals. gcmon derives everything else it prints or draws from
those records. Read them as they arrive, or save them and come back later.

gcmon sits outside the process it watches, injecting nothing and pausing
nothing. It reads what CPython already wrote, so the target runs at full speed
and never learns anyone is reading.

## The ring buffer

CPython keeps one small fixed buffer per generation and writes each new record
into it. gcmon reads the whole buffer on every poll.

The buffer holds the newest few records and never blocks. When it is full,
CPython drops the oldest record to make room, and nothing else describes the run
it held.

How many records it holds depends on the CPython version and build. A
free-threaded build keeps one per generation, so only the newest survives to the
next poll. gcmon reads that size off the first poll that returns records and
keeps it for the session.

## Polling

`--rate` is the wait *between* rounds, in seconds. One round reads every
monitored process once, so the real interval is `--rate` plus those reads. Watch
several child processes and you sample slower than the number you asked for.

## Records gcmon misses

A target whose collector runs faster than gcmon polls drops records before any
poll reads them. At the default 0.1 s rate, a GC-heavy workload might lose
records on most ticks.

A shorter `--rate` narrows the gap without closing it. Every round still reads
each monitored process, and that read time puts a floor under the interval.

A lost record takes its timestamps with it. The two polls around it still bound
the run: a record goes missing between two consecutive reads, and nothing
narrows that interval further.

## What the counters recover

For each generation CPython keeps two cumulative numbers: how many runs have
finished, and how long they took together. Every record carries both as they
stood at its own run. A dropped record takes nothing off either total, so the
difference between two polls gives how many runs gcmon missed and what they cost
together.

Three numbers in the stream come off those counters exactly:

- how many runs happened between two polls, read or not
- how many of them gcmon read
- how much pause time the rest took

The counters give totals and nothing else. gcmon cannot split a total back into
the runs behind it, so averages and percentiles cover only the records it read.
