# ADR-0011: Show process lifetimes on one shared track, ordered by first event

- **Status:** Accepted
- **Date:** 2026-06-27 (ordering added 2026-06-28; laminar clipping added
  2026-07-31; emission simplified to unnested BEGIN/END pairs 2026-08-01; sort
  moved into the sweep and the once-per-trace guard made explicit 2026-08-02;
  monitor-reported liveness landed and the counter carve-out was removed
  2026-08-02; pointer to ADR-0015 added 2026-08-05; the reporting site moved
  from `MonitorLoop` to `EventsMonitor` 2026-08-17, see
  [ADR-0017](0017-monitor-owns-the-pid-lifecycle.md); the RSS round stopped
  adding start jitter the same day, see [ADR-0013](0013-rss-sampling.md); "one
  clock read" narrowed to one *stamping* read 2026-08-20, see
  [ADR-0019](0019-schedule-tick-starts-on-a-fixed-grid.md); a span became one
  per *process* rather than one per pid 2026-08-28, see
  [spec 0059](../../specs/0059-say-which-process-held-a-pid-in-the-trace.md);
  a process track became one per process too the same day, see
  [spec 0066](../../specs/0066-give-each-process-on-a-reused-pid-its-own-track.md))

## Context

[ADR-0010](0010-process-identity-cmdline-and-start-marker.md) gives each pid a
`Process <pid>` track and keeps it visible. Two gaps remained.

**Monitoring duration.** A process track in isolation says nothing about span,
and nothing groups the processes for cross-process comparison.

**Track order.** Process tracks came out in dict-insertion order, so the same
input in a different arrival order produced a differently-ordered trace.
Perfetto's mechanism here is `sibling_order_rank` on each process track
descriptor, but it is consulted for process tracks only when the special root
descriptor at `uuid = 0` carries
`process_ordering = PROCESS_ORDERING_EXPLICIT`. This is the same
OS-scoped-parent rule [ADR-0003](0003-gc-metrics-group-track.md) ran into,
seen from the other side: for process and thread tracks, ordering is
configured on the root rather than on the parent.

**Crossing spans.** Putting every pid on one track has a constraint the
original design missed: slices on a single Perfetto track are a *stack*. A
`TYPE_SLICE_END` force-closes everything stacked above the slice it closes, so
a pair that merely crosses (A starts first, B starts inside A, B ends after A)
cannot be expressed. Given pid 1111 `[100ms, 400ms]` and pid 2222
`[200ms, 600ms]`, the trace processor returns pid 2222 with a 200ms duration
and reports `misplaced_end_event: 1`. The failure is quiet: the slice table
still holds one row per pid, and only the durations are wrong. gcmon monitors
a process *tree*, so any two siblings whose lifetimes overlap without nesting
cross. The repository's own integration fixture crossed, and every assertion
in the suite passed anyway.

## Decision

**A single shared top-level track named `Processes`** holds one
`TYPE_SLICE_BEGIN`/`TYPE_SLICE_END` pair per **process**, named
`Process <pid>`, spanning `[first observed, last observed]` for that process
(see the liveness section below for what counts as an observation). A pid the
operating system hands out twice therefore draws two spans, so an operator
zooming to one is looking at an interval its process was alive in.

- Parented to the trace root, so `parent_uuid` is **absent on the wire**, not
  `0`, which is the reserved root descriptor
  ([ADR-0002](0002-perfetto-track-uuid-and-hierarchy.md)).
- No `process` / `thread` / `counter` sub-message, no `child_ordering` (it is
  a leaf: it has slice events, not child tracks), no `sibling_order_rank` (it
  is neither an explicit-ordered child nor a process or thread track, so the
  field would be ignored).
- Perfetto-only. Chrome JSON and JSONL are unchanged.

**A root `TrackDescriptor` at `uuid = 0`** is emitted once per trace with
`process_ordering = EXPLICIT` and `thread_ordering = EXPLICIT` (fields 19 and
20) and nothing else: no name, no parent, no sub-message.

**A process track is per process**, not per pid. A pid the operating system
hands out twice draws two `ProcessDescriptor` messages, and the second takes
the same `#N` suffix its span on the `Processes` track takes. Each is stamped
with its own first observation, and the thread, loss and counter tracks of a
process hang off the group of the process that produced them.

**Process tracks are ranked by first observation**, ties broken by ascending
pid and then epoch, sequential from 0. Only processes with at least one
non-meta event get a rank.

**The whole track is emitted at encoder close**, once per trace; convert
passes record spans and emit nothing. Two reasons the BEGIN cannot go out
earlier: keeping the track laminar needs every pid's span in hand at once, and
a clip discovered at close cannot correct a BEGIN already written. Nor could
the END, since `BufferedTraceExporter` flushes in chunks of `flush_threshold`
(default 1000) and Perfetto pairs a BEGIN with the **first** matching END,
orphaning the rest.

**Spans are clipped to a laminar set.** The sweep keys on `(pid, pid_epoch)`,
so two spans carrying one pid are made disjoint or nested exactly as two spans
on different pids are. Sorted by ascending start, ties broken by longer span
first and then ascending pid and epoch, a stack sweep pulls each crossed
span's end back to one nanosecond before the span that crosses it. Nesting is
untouched, so a parent outliving its children costs nothing. Spans that merely
touch (`A.end == B.start`) count as crossing when B extends past A, because
the relative order of an END and a BEGIN sharing a timestamp is not something
the wire format lets us pin down; a B that both starts and ends at `A.end` is
nested, and is left alone. Sorting longer-first on equal starts is what makes
the clip safe: two spans with the same start always nest, so a clip only
happens when `A.start < B.start`, and `B.start - 1` never lands before
`A.start`.

**Each span is emitted as an adjacent BEGIN/END pair, not interleaved into
stack order.** The trace processor sorts by timestamp and breaks ties by
position in the sequence, so order decides anything only where events share a
timestamp. Two ENDs at one timestamp need no rule, because gcmon names every
END and the trace processor matches it to the BEGIN with that name rather than
to the top of the stack, so an END cannot close the wrong slice. It does
force-close anything sitting *above* the slice it matched, and that is what
makes the other two collisions matter. Each is owned by one function and
neither is optional: building the pair BEGIN-first belongs to the emission
site, because a zero-length span emitted END-first reads as `dur = -1`;
putting the outer BEGIN of two spans sharing a start ahead of the inner
belongs to the sweep, because `[(100, 2, 6), (101, 2, 3)]` emitted inner-first
gives pid 100 a duration of 1 instead of 4 plus a `misplaced_end_event`.
Neither claim is argued: the fuzz suite below checks both against the trace
processor. The sweep's sort is therefore load-bearing twice over: longer-first
on a tie keeps the clip safe *and* orders these BEGINs, which is why the sweep
sorts its own input rather than documenting the order as a precondition.

The sweep and the emission order together, on the crossing shape from the
Context plus a nested third process (in ns, so the clip lands on 199):

```
                100     200     300     400     500     600  ns
observed
  pid 1111      [=======================]
  pid 2222              [===============================]
  pid 3333              [=======]

sorted by (start, -end, pid)      1111, then 2222 before 3333 -- tie on
                                  start 200, longer span first

after the sweep                   2222 crosses 1111, so 1111's end is pulled
  pid 1111      [======]          back to 199; 3333 nests inside 2222 and is
  pid 2222              [===============================]    left alone
  pid 3333              [=======]

emitted in that order, one adjacent pair per span
  BEGIN 1111 @100   END 1111 @199
  BEGIN 2222 @200   END 2222 @600
  BEGIN 3333 @200   END 3333 @300
        ^                    ^
        |                    +- 199, not 200: the sweep clipped 1111 apart
        |                       from the span that crosses it
        +- 2222 and 3333 share start 200; the longer one is emitted first,
           so 3333 opens inside it rather than outside. Reversed, 3333's
           END would force-close 2222 along with it
```

**Every slice carries `real_start_ts` and `real_end_ts` debug annotations** on
its BEGIN, holding the span as observed. They go on *every* slice, not only
clipped ones, so a consumer reads the observed span without an
annotation-present check. Where `ts`/`dur` and the annotations disagree, the
annotations are the truth.

**No span is ever dropped.** A pid observed at a single instant, and a pid
clipped down to nothing, both still get a BEGIN/END pair; the trace processor
accepts it and reports `dur = 0`. A missing slice would leave no record that
the process was monitored at all.

**The span is `[min, max]` over every observation, with no event-kind
exception.** An observation is any non-meta trace event, counters included, or
a **liveness observation** from `EventsMonitor`: a `(pid, ts)` pair meaning
gcmon read GC state out of that process at that instant. One tick of
monitoring is one call on the monitor, which reports the whole `PollStatus.OK`
set through `add_process_liveness(pids, ts_ns)` once, after its poll phase, so
the cost is one call per tick rather than one per pid. `MonitorLoop` takes one
stamping clock read per tick and hands the instant in, so a liveness
observation and an RSS sample from one tick agree. The accumulator folds a
`(pid, ts)` in as a plain min/max with no keyword: the counter carve-out this
ADR called provisional is **removed**, since the sampler liveness it kept out
of the end is now reported directly.

**A gap in the liveness reports is where one process ends and the next
begins.** The reports arrive once per tick carrying the whole live set, so a
pid absent from one and present in a later one has been handed on, and the
accumulator opens a span of its own for whatever holds it next. Evidence of
either kind opens that span, a GC event as much as a tick, because a poll
returns collections that already happened and the first thing a new process
produces may well predate the tick that found it.

The counting is the encoder's own, from evidence it already receives: nothing
new is plumbed through the exporter protocol and no record grows a field. It
costs an ordering obligation instead. `PerfettoExporter` buffers events and
flushes on a threshold, so a report that drops a pid has to be preceded by
whatever the buffer holds; otherwise a straggler arrives after the span closed
and opens one the process never had. The exporter hands the buffer over on
exactly those reports, which is one extra flush per process death rather than
one per tick.

**Liveness folds in alongside events rather than replacing them.**
`[first OK, last OK]` was rejected because `get_gc_stats` returns collections
that *already happened*, so a freshly discovered child's first GC event can
predate gcmon ever polling it. Under a replace rule every such child would
draw a GC slice outside its own lifetime slice. Membership in `children` is
**not** an observation: `get_child_pids` is the OS's claim about the process
tree, and taking it as evidence reintroduces the `create_time()` approach
rejected below.

**A successful read is what makes the span mean anything.** `get_gc_stats`
returns only once the runtime has finished initializing, so the first `OK`
dates the process becoming ready rather than the OS creating it. A read that
fails after earlier ones succeeded means the process has died or entered
finalization, so the last `OK` dates the other end. That is the interval the
slice draws, and it is why an unpolled or never-collecting process still has
one.

**A pid that misses a report reads as two processes.** The control server
suppressing a pid mid-run is the plain case: it is not polled while
suppressed, so it drops out of the reports, and re-enabling it opens a second
span where this ADR used to promise one continuous span across the gap. A read
that fails once, or a tick where `get_child_pids` answered nothing and no
child was polled at all, does the same. Each gap is drawn where it happened
and the count of processes is what is wrong, and the `--stats` table, which
advances its epoch on the pid leaving the process tree, disagrees. Narrowing
the rule to the evidence the table uses needs the monitor to report an exit,
which is a wider change than this one.

**A pid no report has ever named is not closed by one.** It has not dropped
out of anything: nothing polls it, and its events reach the trace from a
control client instead. Closed on every tick that omitted it, and reopened by
its next event, it would draw a process per tick on a run where no pid was
reused at all.

**A slice goes to the process it began in, whole.** A collection that started
before its process exited belongs to that process, so both ends of the slice
widen that process's span and the encoder draws it there. Folding the ends one
at a time filed the tail under whatever took the pid over and drew a span for
a process that produced nothing. The widened end is held one nanosecond short
of the next process's start where there is one, so widening a span that has
already closed cannot make two spans on one pid overlap.

**Liveness is always on**, with no flag. The cost that justified `--rss`
([ADR-0013](0013-rss-sampling.md)) does not transfer: `live_pids` is already
built by the poll phase, and this is one batched call and two dict comparisons
per pid per tick. A flag would ship two definitions of a `Processes` slice.

**Liveness attaches at `PerfettoExporter`, not on the `EventEncoder`
protocol**, which is three methods meaning "translate a batch of `TraceEvent`
into bytes"; a liveness observation is neither. `PerfettoExporter` builds its
own `ProtobufEventEncoder`, so it keeps a typed handle and overrides the
liveness call; Chrome, JSONL and stdout reach the `EventsExporter` no-op. The
override takes the I/O lock, which is not optional: it guards every other
encoder touch, closing included, and `ControlServer` writes from its own
thread. Without it a concurrent read-modify-write can drop a min/max update,
and a new pid arriving while the exporter closes can raise
`RuntimeError: dictionary changed size during iteration` out of the span
iteration.

## Consequences

- You can see each process's lifetime at a glance and compare across
  processes, and the same events in a different input order produce the same
  ranks.
- **A clipped slice under-reports how long the process was observed**, by an
  amount that depends on how close together the starts are, not on how much
  the spans overlap: the clip is to `later.start - 1`. Siblings fanning out
  from a fork loop start microseconds apart, so the losses are severe in
  ordinary use: 1000 children with nanosecond start jitter and varying
  lifetimes retain **0.37%** of their total observed duration. Liveness cuts
  the other way for part of a fan-out: children whose earliest evidence is the
  tick that first polled them share that timestamp exactly and nest rather
  than clip, while those whose first GC event predates the poll keep their
  jitter.
- **`--rss` no longer adds jitter of its own.** It used to. The sampler read
  its own clock per sample, so a round spread across however long `psutil`
  took, every sample moved its span's start, and `set` iteration order picked
  which sibling got clipped. The sampler now stamps a whole round with the
  tick instant it was given ([ADR-0013](0013-rss-sampling.md)), so those spans
  share a start and nest.
- **The drawn duration is a lower bound, never an upper one**, so deaths are
  misreported as early rather than late. `real_end_ts - real_start_ts`
  recovers what was observed; `docs/perfetto-sql.md` carries the query.
- **Exactly one slice per process gcmon polled**, so consumers join
  `Processes` slices to pids one-to-*many*: a reused pid carries a slice per
  process that held it, and the slice a record belongs to is the one its
  timestamp falls in. A pid that answered a single poll and never collected
  gets one; only a pid seen through meta events alone has none. Finalization
  therefore does *not* filter on a pid having a process descriptor, which
  would have required an event.
- **A zero-GC pid's slice carries no cmdline**, since cmdline registration
  hangs off the encoder's write and such a pid never reaches it. It has no
  process track either, which the UI hides anyway, the problem
  [ADR-0010](0010-process-identity-cmdline-and-start-marker.md)'s
  `Start Process` marker was invented for. Emitting either for it is out of
  scope.
- **A recycled pid gives two `upid`s.** Measured against the trace processor
  the suite pins: two descriptors carrying one pid do not collapse, each keeps
  its own `start_ts`, and two thread descriptors sharing a pid and a tid stay
  apart by the group each hangs off. A per-process total is a `GROUP BY upid`
  rather than a join through the `Processes` track.
- **Rank gaps.** A zero-GC pid consumes a rank, since ranking sorts the same
  dict liveness writes, but has no descriptor to apply it to, so real pids get
  0, 1, 2, 4, 5. Harmless: `sibling_order_rank` is a sort key, not an index.
  Splitting the accumulator to keep ranks event-derived would add a second
  exception to it in the change that deletes the first, for a cosmetic gain.
- **Deep nesting is now the normal shape.** Processes still alive when the
  loop stops share an end timestamp, and the sweep breaks out on
  `outer_end >= end`, so co-terminating spans nest one level per process
  instead of clipping. Staggered deaths still clip, so traces mix both.
- **The trace processor closes at most 512 nested slices.** The fuzz suite
  measures this against the real trace processor: at 512 every slice reads
  back exactly, and each level past it leaves one more with `dur = -1`. The
  loss is **silent**, since `misplaced_end_event` stays 0 and no other
  non-info stat is raised. gcmon writes a well-formed pair for every span
  either way, so the limit sits in the reader. Bounding nesting depth is out
  of scope.
- **`combine` diverges from live capture.** Offline conversion has no monitor
  polling anything, so its spans stay event-derived and narrower. Carrying
  liveness through JSONL or Chrome so `combine` could reproduce it is out of
  scope.
- **`sibling_order_rank` is not exposed as a SQL column.** It is a UI hint, so
  the trace-processor tests act as a *schema-validity guard*: they confirm the
  layout is accepted and the `process` and `track` tables survive intact, but
  only the Perfetto UI can assert display order. Perfetto's docs call these
  orderings "strong hints" in any case, so the UI may still rearrange tracks
  in special contexts.
- **Ranks are not applied retroactively.** If a pid's `ProcessMeta` lands in
  an earlier batch than its first non-meta event, the descriptor goes out
  before the rank is known, and emission is idempotent, so that pid gets no
  rank. Within a batch the Perfetto conversion's pre-pass folds every non-meta
  event into the span state *before* the main loop, so same-batch
  `ProcessMeta` still gets its rank. Liveness shrinks this without closing it:
  the monitor enqueues the `ProcessMeta` *during* the poll while the liveness
  call happens after it, so a batch crossing `flush_threshold` mid-poll still
  emits a rank-less descriptor.
- The `Processes` block lands at the end of the file, descriptor first. The
  trace processor resolves track references across the whole trace rather than
  in file order.
- Consumers enumerating slices must filter `track.name == 'Processes'`, as the
  equivalence test does, since these slices are Perfetto-only.
- [ADR-0015](0015-gc-loss-spans-on-their-own-track.md) needs no sweep: its
  loss spans are one per poll interval and meet without overlapping. Its
  `GC Loss` track is separate so a reader can tell intervals gcmon recorded
  from intervals it lost.

## Alternatives considered

- **One lifetime track per pid**, representing crossing spans exactly with no
  clipping. Rejected: gcmon runs on captures with hundreds to thousands of
  processes, and a track per pid makes the timeline unreadable. A collapsible
  parent group does not help; the row count is the problem.
- **Packing spans into lanes**, colouring the interval graph so the row count
  is maximum *concurrency* rather than process *count*. Strictly better than a
  track per pid (8 workers running 1000 tasks needs 8 rows), but rejected: N
  children alive at once are N mutually crossing intervals and still need N
  lanes, so the timeline is as unreadable as before.
- **Dropping a slice that ends up zero-length**, the original decision here.
  Reversed: it optimised the rendering at the cost of the record, and the pids
  likeliest to be clipped to nothing are exactly the short-lived children a
  reader is looking for.
- **Snapping near-equal starts together before the sweep**, turning a jittered
  fan-out back into the nesting it almost is; every end survives at a cost of
  at most ε on each start, and the 0.37% above becomes 100%. Not adopted,
  still open: ε is a heuristic, nesting N deep costs N rows of vertical space
  inside the track, and the trace processor stops closing slices past 512 (see
  Consequences).
- **Extending the earlier span's end instead of clipping it**, nesting the
  later span inside. Rejected: it makes a dead process look alive, and the
  nesting implies a parent/child relationship that may not exist.
- **Clipping whichever side loses fewer nanoseconds.** Rejected: it makes the
  direction of the distortion depend on the data rather than on a stated rule.
- **Leaving crossing spans alone and documenting the mismatch.** Rejected: the
  durations are silently wrong, and `misplaced_end_event` is not something a
  reader of the UI would think to check.
- **OS-level process times via `psutil.Process(pid).create_time()`.**
  Rejected: the span should describe what gcmon observed, not when the OS
  started the process; the difference would be misread as monitoring coverage.
- **Deliberately snapping every pid in a tick to one timestamp**, making spans
  that share a start nest rather than cross, so the "snap near-equal starts"
  alternative above lands with no ε to choose. Noted and not taken: the
  benefit is an artifact of `--rate` and would degrade silently as the rate
  drops.
- **Emitting liveness as a `TraceEvent`.** Rejected: at 10 Hz × N pids, a
  60-second run with ten children carries ~6,000 extra events, visible on the
  process tracks, to record two numbers per pid.
- **A `RssSampler`-style collaborator** accumulating `first`/`last` per pid
  and flushing at close. Rejected: it mirrors state the exporter already holds
  and adds a close-ordering hazard. Against a min/max, the redundant per-tick
  calls cost only dict comparisons.
- **A fourth method on the `EventEncoder` protocol**, with a no-op in
  `JsonEventEncoder`. Rejected: it widens a precise abstraction to carry
  per-trace state one implementation has, and taxes the other for the life of
  the protocol.
- **Emitting the slice END at the end of each convert call.** The original
  implementation, and wrong; see above.
- **Re-emitting a process descriptor with a corrected rank in a later batch.**
  Rejected: it breaks idempotent emission for a cosmetic gain in a rare
  ordering.

## Implementation

- `src/gcmon/exporters/perfetto_process_lifetime.py` holds this decision: the
  `Processes` track name, the sweep, the two emission steps and the
  finalization the encoder calls at close. The sweep sorts its input by
  `(start, -end, pid)` and returns it in that order, carrying each span's
  observed start and end through untouched alongside the drawn ones, so the
  emission site can annotate every slice without knowing which fields moved.
  The track descriptor asserts on the once-per-trace flag rather than trusting
  its caller: a second descriptor for one uuid is accepted silently, so
  nothing downstream would report it.
- `src/gcmon/exporters/perfetto_proto.py` carries `process_ordering` at field
  19 and `thread_ordering` at field 20. Fields 6 and 7 on the same message are
  `chrome_process` and `chrome_thread`, so a wrong number writes a different
  message and fails silently
  ([ADR-0001](0001-hand-rolled-perfetto-protobuf-encoder.md)).
- `src/gcmon/exporters/perfetto_track_state.py` holds the span accumulator, a
  plain min/max over `(pid, ts)` with no keyword since the carve-out was
  removed, so start and end always carry identical key sets. Both are keyed on
  `(pid, pid_epoch)`; `observe_process_liveness` takes a whole tick's report,
  closes the span of every pid it omits that an earlier report named, and
  folds the rest in. `update_process_lifetime_span` folds a slice's whole
  interval into one process. It hands the spans back for finalization, ranks
  them by `(start_ts, pid, pid_epoch)` over every process, and owns the
  once-per-trace flag that makes finalization safe to call twice, covering the
  non-idempotent track descriptor too.
- `src/gcmon/exporters/perfetto_format.py` emits the root descriptor, guarded
  so it goes out once.
- The liveness path, monitor to accumulator:
  `src/gcmon/monitoring/monitor_loop.py` takes one stamping
  `time.monotonic_ns()` per tick and hands it to the monitor, then unconverted
  to the RSS sampler ([ADR-0013](0013-rss-sampling.md)). A second read paces
  the loop and stamps nothing
  ([ADR-0019](0019-schedule-tick-starts-on-a-fixed-grid.md)).
  `src/gcmon/monitoring/monitor.py` reports the live set at the end of a tick,
  after the poll phase and skipped on an empty set. The clock and the stop
  signal belong to the loop; everything per-pid belongs to the monitor
  ([ADR-0017](0017-monitor-owns-the-pid-lifecycle.md)).
  `src/gcmon/exporters/exporter.py` holds the no-op base;
  `src/gcmon/exporters/combined_exporter.py` fans it out;
  `src/gcmon/exporters/perfetto_exporter.py` overrides it under the I/O lock,
  forwarding to `src/gcmon/exporters/encoder.py` and handing over the buffer
  ahead of a report that drops a pid.
- Encoder close gates on having packets to emit, not on whether anything was
  written before. That guard meant "no spans exist" while only events could
  create one; liveness reaches the track state without passing through the
  encoder's write, so the first write may now have to select `"wb"`. A trace
  with neither events nor liveness still produces no file.
- `tests/exporters/test_perfetto_process_lifetime.py` covers the sweep
  directly at full statement and branch coverage, and the same shapes through
  finalization, additionally checking the emitted BEGIN/ENDs are ones the
  trace processor can pair up. The accumulator is covered in
  `tests/exporters/test_perfetto_track_state.py`, next to the class it
  exercises. Ranking and `start_timestamp_ns` are in
  `tests/exporters/test_perfetto_ordering.py`.
- `tests/exporters/test_perfetto_emission_order_fuzz.py`, marked `fuzz` and
  run by its own CI job, settles the emission-order claims above against the
  real trace processor: paired emission reads back exactly over random laminar
  span sets, and the orderings rejected here are asserted to *break*, so the
  positive case cannot pass by ordering being irrelevant. It also pins both
  sides of the 512-deep nesting limit above.
- `tests/exporters/test_perfetto_exporter_integration.py` asserts
  `misplaced_end_event == 0` against a deliberately crossing trace, that a
  same-ts BEGIN/END is paired rather than orphaned and every pid keeps a
  slice, the two liveness shapes, and that a run forced through many flushes
  with `flush_threshold=5` still ends its slice at the last event's timestamp.
- Liveness unit tests: `tests/monitoring/test_monitor.py`, beside the tick
  that reports it, and `tests/monitoring/test_monitor_loop.py` for the
  stamping read. `tests/monitoring/test_monitored_run_trace.py` pins a whole
  run's Chrome output byte for byte;
  `tests/exporters/test_perfetto_exporter.py`, whose locking test asserts by
  contention rather than by inspection; and
  `tests/exporters/test_buffered_exporter.py`, pinning Chrome, JSONL and
  stdout output as byte-identical with and without liveness.
