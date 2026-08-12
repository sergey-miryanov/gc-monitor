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
- *`Processes` track — a minimap of the session, one slice per monitored process (read durations from its annotations, not its width)*
- *`rss` counter track (when `--rss` is enabled) showing Resident Set Size per PID*

Perfetto features:
- **Counter Y-axis sharing**: Same metric names share Y-axis across generations (e.g., `G0 collected`, `G1 collected`, `G2 collected` all on one axis).
- **`Processes` track**: A minimap of the session — one slice per monitored process, so you can see at a glance which processes gcmon was watching when. The track is named `Processes`; that is the name to filter on in SQL. A slice spans the range over which gcmon *observed* the process, so one that ran for a minute without ever collecting still gets a minute-wide slice; `gcmon combine` has no monitor behind it and produces narrower, GC-activity-only spans. Every process gets exactly one slice, so these join to pids one-to-one. **A slice's width is a lower bound on the observed lifetime, not the lifetime itself.** Every process shares this one track, and slices on a Perfetto track have to nest, so where two spans overlap without nesting the earlier one is cut short to let the later one through — sometimes to nothing, leaving a zero-duration slice. Every slice therefore carries `real_start_ts` and `real_end_ts` annotations holding the span as observed, whether it was cut or not; see [Perfetto SQL](perfetto-sql.md) for reading them. The cut can be severe when processes start close together, which is normal for a fan-out of children, so prefer the annotations to the drawing whenever the number matters.
- **Process ordering**: Tracks are ordered by first event timestamp, so the earliest-starting process appears at the top.
- **Process command lines**: With the [`[cmdline]` extra](rss.md#the-cmdline-extra), each monitored process's command line is written to the trace — see [Process command lines](#process-command-lines) below.
- **`Start Process` marker**: A zero-duration instant event named `Start Process` is emitted on each process track at that process's first event. Perfetto hides a track that carries no events, so this guarantees the process track and its label always render. It is Perfetto-only; consumers that enumerate slices should filter it out.
- **RSS counter track**: A process-level `rss` counter track appears for each PID when `--rss` is enabled, showing Resident Set Size in bytes. Sampled at the configured `--rss-interval` (default 1s).
- **`GC Loss` track**: One row per interpreter, named `GC Loss {iid}`, sitting under that process's own track. Each slice marks one poll interval in which gcmon missed one or more records from the target. See [GC Loss slices](#gc-loss-slices) below.

This visualization helps you:
- **Identify GC pause patterns** - See when and how long GC pauses occur
- **Track object growth** - Monitor the live object count over time
- **Analyze collection efficiency** - Compare GC-related metrics
- **Debug memory issues** - Spot memory leaks or inefficient collection patterns
- **Correlate sub-step timing** - See which GC phase (mark, sweep, finalize) dominates pause time

> **Note:** Sub-step slices (Mark Alive, Fill increment, Deduce Unreachable, etc.) and their associated data are only available when using a custom CPython build with enhanced GC instrumentation. Standard CPython builds provide only the top-level GC Pause slices and counter data.

### GC Loss slices

A target whose collector runs faster than gcmon polls loses records; see
[How gcmon reads a process](monitoring.md). Each interval gcmon went blind in gets one
slice on a `GC Loss {iid}` track of its own.

**One span per poll interval**, from one read of the target to the next. Every GC run a
span accounts for finished between those two reads, and nothing places it more precisely. Consecutive spans meet without overlapping, so the row reads as a sequence.

The name lists the generations that lost records, `GC Loss(0,2)`, so the row says which
generations went blind before you click anything, and each combination keeps its own colour.

**Read the magnitude from the args, not from the width.** One lost 5 ms run can
draw a 130 ms bar: the width is the interval the records went missing in, and the pause
they took is in the args. That is why these slices get a row of their own, where an
interval-width bar cannot be mistaken for a very long `GC Pause`.

Each slice carries these totals for the whole interval:

| Arg | Meaning |
|---|---|
| `iid` | Interpreter the records were lost from |
| `observed_count` | Records gcmon read in this interval, across every generation |
| `missing_count` | Records it missed in the same interval |
| `seen` | The share that survived, as `87.0% (47 of 54)`. One interval wide, unlike the `--stats` table's `Cov` |
| `missing_pause_total` | Pause time the runs behind those records took, as `3s 316ms 458µs 100ns`. The bar above it can be 29 s wide |
| `missing_pause_total_ns` | The same total in nanoseconds, from the target's own counter. Sum this one in SQL |

Then one group per generation that collected or lost anything, named `gen0`, `gen1`,
`gen2`:

| Arg | Meaning |
|---|---|
| `observed_count` | Records of that generation gcmon read in this interval |
| `missing_count` | Records of it gcmon missed |
| `missing_collections` | Which ones, on that generation's `collections` counter: `413..431` for a streak, `11` for a single one, both ends included |
| `missing_pause_total` / `_ns` | What those cost, as text and as nanoseconds |

A generation that came through the interval whole still gets a group with what it
observed, so the groups add up to the totals above them. In SQL the trace processor
flattens a group by joining the names with a dot, so `gen1`'s count is
`args.debug.gen1.missing_count`.

**The range is exact where the width is not.** gcmon finds it by subtracting two of the
target's cumulative counters, so a group reading `missing_count = 19` also reads
`413..431`. Between the first and last record gcmon read on a generation's counter, every
run is either drawn as a `GC Pause` slice or inside exactly one span's range. None twice,
none missing.

A span covers the `GC Pause` slices that fall in its interval, its own interpreter's
included. The counts say how much of the interval gcmon saw; the bar says nothing about
it. Neither `Cov` nor `F` in the `--stats` table moves with any of this.

At default settings the track reads as a near-solid bar, since gcmon is blind for most of
every tick. Lower `--rate` or a calmer workload thins it out. See
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

Recording them requires the [`[cmdline]` extra](rss.md#the-cmdline-extra) and
degrades silently: if `psutil` is missing, or the process has already exited or
is inaccessible, the command line is dropped and the trace stays valid. In a
`combine` run the PIDs are historical and the processes are usually gone, so
this is the normal case.

Command lines are **Perfetto-only**. The Chrome Trace format carries a
`process_name` metadata event per PID and no command line.

## JSONL output

With `--format jsonl` (writes to file) or `--format stdout` (writes to terminal),
each line is a JSON object holding one GC record:

```jsonl
{"pid": 12345, "tid": 0, "gen": 0, "iid": 1, "ts_start": 1700000000000000, "ts_stop": 1700000001500000, "heap_size": 1048576, "collections": 42, "collected": 120, "uncollectable": 0, "candidates": 300, "duration": 0.0015}
{"pid": 12345, "tid": 0, "gen": 1, "iid": 2, "ts_start": 1700000200000000, "ts_stop": 1700000235000000, "heap_size": 2097152, "collections": 3, "collected": 85, "uncollectable": 1, "candidates": 150, "duration": 0.035}
```

| Field | Description | Build |
|-------|-------------|-------|
| `pid` | Process ID of the monitored target | Standard |
| `gen` | GC generation (0, 1, or 2) | Standard |
| `iid` | Interpreter ID (`0` for the main interpreter) | Standard |
| `ts_start`, `ts_stop` | When the run started and stopped (nanoseconds) | Standard |
| `heap_size` | Number of live objects the run recorded | Standard |
| `collections` | How many times this generation has run, cumulative | Standard |
| `collected` | Objects this run collected | Standard |
| `uncollectable` | Objects that could not be collected | Standard |
| `candidates` | Candidate objects for collection | Standard |
| `duration` | Pause duration in seconds (float, as reported by CPython) | Standard |
| `increment_size` | Increment size for incremental GC | Custom build |
| `alive_size` | Objects marked alive (gen > 0) | Custom build |
| `finalized_garbage_count` | Objects this run finalized | Custom build |
| `deleted_garbage_count` | Objects this run deleted | Custom build |
| `clear_weakrefs_count` | Weakrefs this run cleared | Custom build |

> **Note:** Fields marked **Custom build** require a CPython build with enhanced
> GC instrumentation — see the [Chrome trace and Perfetto output](#chrome-trace-and-perfetto-output)
> note above.

### Loss records

A session that missed records writes one line per blind poll interval per
interpreter, alongside the GC records. A loss record carries no `collections` and no `gen`
of its own; the per-generation counts sit in `gens`:

```jsonl
{"pid": 12345, "tid": -2, "iid": 0, "ts_start": 1700000001500000, "ts_stop": 1700000098000000, "gens": [{"gen": 0, "observed_count": 11, "lost_from": 413, "lost_count": 9, "lost_pause_ns": 57450000}, {"gen": 1, "observed_count": 3, "lost_from": 27, "lost_count": 1, "lost_pause_ns": 8100000}]}
```

| Field | Description |
|-------|-------------|
| `tid` | `-2 - iid`, the sentinel the trace formats draw the `GC Loss` track on. `-1` is reserved for `rss` |
| `iid` | Interpreter the records were lost from |
| `ts_start`, `ts_stop` | The interval, from one poll to the next (nanoseconds). Its width is uncertainty, not pause time |
| `gens` | One entry per generation that ran or lost anything in the interval |

and in each `gens` entry:

| Field | Description |
|-------|-------------|
| `gen` | The generation |
| `observed_count` | Records of it gcmon read in this interval |
| `lost_from` | First record gcmon missed, on that generation's `collections` counter |
| `lost_count` | Records of it gcmon missed. Zero for a generation that lost nothing |
| `lost_pause_ns` | Pause time the runs behind those records took, in nanoseconds |

The far end is `lost_from + lost_count - 1`, which is where the far end of the
`missing_collections` arg on a `GC Loss` slice comes from. Storing both would let the two
disagree, and `lost_count` is the number `--stats` sums.

Line order carries no meaning. Converting to a trace sorts the records by `ts_start`
first, so a tool may rewrite a capture in whatever order suits it.

Tell the record types apart by field presence: a GC record has `collections`, a loss
record has `gens`, an instant event has `type`. `gcmon combine` reads loss records back
and redraws the spans in Chrome or Perfetto output. `--normalize` shifts them with
everything else, and a loss record can be the earliest thing in a capture, since its
interval opens at the poll before the records that closed it.

Captures written before the record went per-poll are **not** readable, whether they carry
one line per generation with `lost_count` at the top level, or the older shape that
flattened all three generations into `lost_gen_0`..`lost_gen_2`. Neither has `gens`, and
nothing else in either line looks like a GC record, so `gcmon combine` stops with a
decoding error naming the first field it could not find rather than reading a blind
interval back as an observed run. Re-capture, or convert with the gcmon that wrote
the file.
