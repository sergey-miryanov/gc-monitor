# Output formats

gcmon writes traces in four formats, selected with `--format`: `chrome`
(Chrome Trace Event), `perfetto` (Perfetto binary protobuf), `jsonl` (JSONL to
file), and `stdout` (JSONL to stdout). See the [CLI reference](cli.md) for the
flag itself.

## Chrome trace and Perfetto output

<img src="images/chrome-trace-example.png" alt="Chrome Trace Example" width="800">

*Example: GC monitoring data visualized in Perfetto UI showing:*
- *Process tracks labelled with the process command line (requires `[cmdline]` extra)*
- *GC Pause slices with sub-step breakdown (Mark Alive, Fill increment, Deduce Unreachable, etc.)*
- *Per-gen `G{gen}` counter tracks (`collected`, `candidates`, `duration`, `uncollectable`)*
- *Shared `heap_size` top-level counter*
- *`Processes` track — a minimap of the run, one slice per monitored process (read durations from its annotations, not its width)*
- *`rss` counter track (when `--rss` is enabled) showing Resident Set Size per PID*

Perfetto features:
- **Counter Y-axis sharing**: Same metric names share Y-axis across generations (e.g., `G0 collected`, `G1 collected`, `G2 collected` all on one axis).
- **`Processes` track**: A minimap of the run — one slice per monitored process, so you can see at a glance which processes gcmon was watching when. The track is named `Processes`; that is the name to filter on in SQL. A slice spans the range over which gcmon *observed* the process, so one that ran for a minute without ever collecting still gets a minute-wide slice; `gcmon combine` has no monitor behind it and produces narrower, GC-activity-only spans. Every process gets exactly one slice, so these join to pids one-to-one. **A slice's width is a lower bound on the observed lifetime, not the lifetime itself.** Every process shares this one track, and slices on a Perfetto track have to nest, so where two spans overlap without nesting the earlier one is cut short to let the later one through — sometimes to nothing, leaving a zero-duration slice. Every slice therefore carries `real_start_ts` and `real_end_ts` annotations holding the span as observed, whether it was cut or not; see [Perfetto SQL](perfetto-sql.md) for reading them. The cut can be severe when processes start close together, which is normal for a fan-out of children, so prefer the annotations to the drawing whenever the number matters.
- **Process ordering**: Tracks are ordered by first event timestamp, so the earliest-starting process appears at the top.
- **Process command lines**: With the [`[cmdline]` extra](rss.md#the-cmdline-extra), each monitored process's command line is written to the trace — see [Process command lines](#process-command-lines) below.
- **`Start Process` marker**: A zero-duration instant event named `Start Process` is emitted on each process track at that process's first event. Perfetto hides a track that carries no events, so this guarantees the process track and its label always render. It is Perfetto-only; consumers that enumerate slices should filter it out.
- **RSS counter track**: A process-level `rss` counter track appears for each PID when `--rss` is enabled, showing Resident Set Size in bytes. Sampled at the configured `--rss-interval` (default 1s).
- **`GC Loss` track**: One row per interpreter, named `GC Loss {iid}`, sitting under that process's own track. Each slice marks an interval in which the ring buffer overwrote GC records before gcmon could read them — see [GC Loss slices](#gc-loss-slices) below.

This visualization helps you:
- **Identify GC pause patterns** - See when and how long GC pauses occur
- **Track object growth** - Monitor the live object count over time
- **Analyze collection efficiency** - Compare GC-related metrics
- **Debug memory issues** - Spot memory leaks or inefficient collection patterns
- **Correlate sub-step timing** - See which GC phase (mark, sweep, finalize) dominates pause time

> **Note:** Sub-step slices (Mark Alive, Fill increment, Deduce Unreachable, etc.) and their associated data are only available when using a custom CPython build with enhanced GC instrumentation. Standard CPython builds provide only the top-level GC Pause slices and counter data.

### GC Loss slices

CPython exports GC records through a small ring buffer of 11 slots for generation 0
and 3 for the older two, so a target collecting faster than gcmon polls overwrites
records before anyone reads them. gcmon detects this from CPython's cumulative
`collections` and `duration` counters and marks each blind interval with a slice
named `GC Loss`, on a `GC Loss {iid}` track of its own.

**The slice's width is the interval the records were lost in rather than the pause
they took.** Nothing in the ring says where inside that interval the missing
collections ran, so the bar spans the whole stretch gcmon could not see: from the
last thing it observed to the next record it recovered. One lost 5 ms collection
can draw a 130 ms bar. Read the magnitude from the args and not from the width.
That gap between the two is why these slices sit on a row of their own, since
among the `GC Pause` slices a window-width bar would read as a very long pause.

Each slice carries:

| Arg | Meaning |
|---|---|
| `iid` | Interpreter the records were lost from |
| `lost_gen_N` | Collections of generation *N* that ran unobserved in this interval |
| `lost_pause_gen_N` | Pause time those collections took, in nanoseconds — exact, from the target's own counter |
| `lost_total` | Collections lost across all generations |
| `lost_pause_total` | **Read this for the magnitude.** Total pause time lost in the interval, in nanoseconds |

Only generations that lost something get a `lost_gen_N` / `lost_pause_gen_N` pair;
`lost_total` and `lost_pause_total` are always present.

Where a window brackets a collection gcmon did observe, it draws as several slices
with a hole around that collection instead of one bar over the top of it, since no
lost record can have run during an observed one. gcmon then shares the counts
across the pieces in proportion to width, so **a piece's `lost_gen_N` is a share
rather than a measurement**. A piece reading "1 lost, 8.75 ms" means that of the
records this window lost, this stretch covers 15% of the blind time. Add up the
pieces of one window and the totals are exact; only their distribution is
estimated. How a span draws leaves the `--stats` table's `Cov` and `F` columns
untouched.

At default settings the track reads as a near-solid bar, because gcmon is blind for
most of every tick. Lower `--rate` or a calmer workload thins it out. See
[ADR-0015](adr/0015-gc-loss-spans-on-their-own-track.md) for the reasoning, and
[Statistics](statistics.md) for what the loss does to the numbers.

### Process command lines

A trace routinely contains several processes, since gcmon discovers and follows
child PIDs. `Process 4821` is not enough to tell them apart, so gcmon records
each process's command line — in **three** places, because no single one serves
both the UI and SQL:

| Where | Form | Visible in the UI | Queryable from SQL |
|---|---|---|---|
| `ProcessDescriptor.cmdline` on the process track | argv, one string per argument | Yes | **No** — the trace processor does not surface this field |
| `description` on the process track | argv joined with single spaces | Yes | Yes, via `args` (key `description`) |
| `cmdline` debug annotation on the `Process {pid}` slice of the `Processes` track | argv joined with single spaces | Yes, in the slice's details | Yes, via `args` (key `debug.cmdline`) |

Queries for the latter two are in
[Trace Analysis with Perfetto SQL](perfetto-sql.md#example-querying-process-command-lines).
The rationale for the duplication is [ADR-0010](adr/0010-process-identity-cmdline-and-start-marker.md).

Collection requires the [`[cmdline]` extra](rss.md#the-cmdline-extra) and
degrades silently: if `psutil` is missing, or the process has already exited or
is inaccessible, the command line is dropped and the trace stays valid. In a
`combine` run the PIDs are historical and the processes are usually gone, so
this is the normal case.

Command lines are **Perfetto-only**. The Chrome Trace format carries a
`process_name` metadata event per PID and no command line.

## JSONL output

With `--format jsonl` (writes to file) or `--format stdout` (writes to terminal),
each line is a JSON object representing one GC event:

```jsonl
{"pid": 12345, "tid": 0, "gen": 0, "iid": 1, "ts_start": 1700000000000000, "ts_stop": 1700000001500000, "heap_size": 1048576, "collections": 42, "collected": 120, "uncollectable": 0, "candidates": 300, "duration": 0.0015}
{"pid": 12345, "tid": 0, "gen": 1, "iid": 2, "ts_start": 1700000200000000, "ts_stop": 1700000235000000, "heap_size": 2097152, "collections": 3, "collected": 85, "uncollectable": 1, "candidates": 150, "duration": 0.035}
```

| Field | Description | Build |
|-------|-------------|-------|
| `pid` | Process ID of the monitored target | Standard |
| `gen` | GC generation (0, 1, or 2) | Standard |
| `iid` | Interpreter ID (`0` for the main interpreter) | Standard |
| `ts_start`, `ts_stop` | Event timestamps (nanoseconds) | Standard |
| `heap_size` | Number of live objects at event time | Standard |
| `collections` | Cumulative collection count for this generation | Standard |
| `collected` | Objects collected in this event | Standard |
| `uncollectable` | Objects that could not be collected | Standard |
| `candidates` | Candidate objects for collection | Standard |
| `duration` | Pause duration in seconds (float, as reported by CPython) | Standard |
| `increment_size` | Increment size for incremental GC | Custom build |
| `alive_size` | Objects marked alive (gen > 0) | Custom build |
| `finalized_garbage_count` | Objects finalized in this event | Custom build |
| `deleted_garbage_count` | Objects deleted in this event | Custom build |
| `clear_weakrefs_count` | Weakrefs cleared in this event | Custom build |

> **Note:** Fields marked **Custom build** require a CPython build with enhanced
> GC instrumentation — see the [Chrome trace and Perfetto output](#chrome-trace-and-perfetto-output)
> note above.

### Loss records

A run that lost records to ring-buffer wrap also writes one line per blind
interval, alongside the GC events. A loss record carries no `gen` field, since it
can span several generations at once, and no `collections`:

```jsonl
{"pid": 12345, "tid": -2, "iid": 0, "ts_start": 1700000001500000, "ts_stop": 1700000098000000, "lost_gen_0": 9, "lost_gen_1": 1, "lost_gen_2": 0, "lost_pause_gen_0": 57450000, "lost_pause_gen_1": 8100000, "lost_pause_gen_2": 0}
```

| Field | Description |
|-------|-------------|
| `tid` | `-2 - iid`, the sentinel the trace formats draw the `GC Loss` track on. `-1` is reserved for `rss` |
| `iid` | Interpreter the records were lost from |
| `ts_start`, `ts_stop` | The blind interval (nanoseconds). Its width is uncertainty, not pause time |
| `lost_gen_N` | Collections of generation *N* that ran unobserved in the interval |
| `lost_pause_gen_N` | Pause time those collections took, in nanoseconds |

Tell the record types apart by field presence: a GC event has `gen`, a loss record
has `lost_gen_0`, an instant event has `type`. `gcmon combine` reads loss records
back and reproduces the spans in Chrome or Perfetto output. `--normalize` shifts
them with everything else, and a loss record can be the earliest thing in a
capture, since a window opens before the record that closes it.
