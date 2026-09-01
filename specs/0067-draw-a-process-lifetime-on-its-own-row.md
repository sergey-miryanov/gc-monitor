# 0067: Draw each process's monitored lifetime on its own row

- **Status:** Not started
- **Kind:** feature (enhancement)
- **Effort:** M
- **Origin:** design session 2026-09-01, taken up after 0066 split the process
  track per process
- **Respects:**
  - [ADR-0002](../docs/adr/0002-perfetto-track-uuid-and-hierarchy.md) (uuid
    allocation and parenting)
  - [ADR-0009](../docs/adr/0009-nanoseconds-canonical-time-unit.md)
    (nanoseconds)
  - [ADR-0010](../docs/adr/0010-process-identity-cmdline-and-start-marker.md)
    (the process track renders only with an event on it)
  - [ADR-0011](../docs/adr/0011-process-lifetime-and-ordering.md) (the
    `Processes` track, laminar clipping, track ranking)
  - [ADR-0012](../docs/adr/0012-trace-output-formats.md) (a Perfetto-only
    feature stays Perfetto-only)
  - [ADR-0014](../docs/adr/0014-perfetto-integration-test-strategy.md) (assert
    through the trace processor)
  - [ADR-0015](../docs/adr/0015-gc-loss-spans-on-their-own-track.md) (what a
    loss span covers and which row it is drawn on)
  - [ADR-0025](../docs/adr/0025-create-every-process-in-one-place.md) (the
    monitor reads a command line once per process)

## 1. Problem statement

Open a trace and click the `Process 12345` row. It carries one point named
`Start Process` and whatever marks the workload wrote, and its Args panel is
empty. The row answers none of the questions someone reading it has: how long
gcmon watched this process, how much of its GC activity reached the file, how
many interpreters it ran.

Those answers exist, on a different row. The shared `Processes` track carries
one span per process with the command line and the pid epoch on it. Reading a
process means leaving its own row and finding it again among every other
process in the run. The duration read there is not the observed one either: a
span that crosses a sibling's is shortened to keep that track's slice stack
laminar, and a process observed for 300 ms can draw 99 ms. The observed pair
reaches the file as the `real_start_ts` and `real_end_ts` annotations, and the
bar is drawn from the clipped one.

A process gcmon polled successfully but read no collections from has no row at
all. It reaches the file as a span on `Processes` and nothing else, and the
timeline never says gcmon watched it.

Loss is split the same way. The `GC Loss` row draws one bar per blind interval
per interpreter, and an operator asking how much the capture missed counts
bars.

## 2. Solution

The `Process 12345` row draws one bar for as long as gcmon observed the
process. Click it, and the Args panel says what the process was running, which
epoch of the pid it is, how many interpreters it ran, how many records gcmon
read, and how many it missed with the GC pause time inside them.

The bar is the interval gcmon observed, uncorrected. Where the shared
`Processes` track shortens a span to keep its stack laminar, the process's own
row does not, and an annotation on the bar says whether the two rows disagree.

A process gcmon polled but read no collections from draws a row too: the bar,
the command line, and nothing under it. That is the difference between a
process gcmon watched and read nothing from, and one it never reached.

The workload's marks draw inside the bar, where they happened.

`--format jsonl` and `--format stdout` are unchanged.

## 3. User stories

1. As someone reading a trace in the Perfetto UI, I want a process's row to
   say how long gcmon watched it, so that I do not have to find the same
   process again on a shared track.
2. As someone reading a trace in the Perfetto UI, I want the bar on that row
   to be the interval gcmon observed, so that the duration I read is the one
   gcmon measured.
3. As someone comparing processes, I want the `Processes` track to keep the
   spans and annotations it has, so that the row I scan for comparison is
   unchanged.
4. As an operator judging a capture, I want the records read and the records
   missed on one panel, so that I can tell a thin capture from a quiet process
   without counting `GC Loss` bars.
5. As an operator monitoring a tree, I want a process gcmon read nothing from
   to draw a row, so that the timeline distinguishes a quiet process from one
   gcmon never reached.
6. As a developer profiling a subinterpreter workload, I want the interpreter
   count on the process panel, so that I can see at a glance which processes
   ran more than one.
7. As someone whose process was clipped on the shared track, I want the two
   rows' disagreement stated on the bar, so that I am not left to discover it
   by subtracting.
8. As a user of `--format jsonl` or `--format stdout`, I want my output
   byte-identical, so that a Perfetto-only feature stays Perfetto-only.
9. As someone running `gcmon combine`, I want a converted capture to draw the
   same bar with the annotations it can supply, so that offline conversion
   loses only what it never had.
10. As a gcmon maintainer, I want one rule for where process metadata lives,
    so that a later annotation has one home rather than a choice.

## 4. Implementation decisions

**`Start Process` becomes `Lifetime`, a slice on the same row.** The
`TYPE_INSTANT` `_emit_start_process_marker` writes becomes a
`TYPE_SLICE_BEGIN` / `TYPE_SLICE_END` pair on the process track uuid, named
`Lifetime`. `_START_PROCESS_INSTANT_NAME`, `_emit_start_process_marker`,
`_maybe_emit_start_process_marker` and its three call sites in
`convert_trace_events_to_perfetto` go, along with
`PerfettoTrackState.has_start_process_marker` and `mark_start_process_marker`.

The slice stays on the process track and does not move to a child of it. An
empty process track is the defect ADR-0010 exists to fix: the Perfetto UI
renders a track's `description` only where the track has an event, and the
`description` is the command line. A `Lifetime` slice on a child row would
empty the parent and un-render it.

**The slice draws `[real_start_ts, real_end_ts]`.** `ClippedSpan` already
carries both pairs. `_clip_spans_to_laminar` exists because every process
shares one track and slices on a Perfetto track are a stack. A process's own
row holds one slice, and the only other events on it are the workload's
`Instant` marks, which nest without closing anything. Nothing on that row can
cross the slice, and nothing needs clipping.

The two rows therefore disagree for a clipped process, and the process's own
row is the one telling the truth. This extends ADR-0011's existing rule that
where `ts` / `dur` and the annotations disagree, the annotations are the
truth: the row that can draw the observed pair draws it.

**Emission moves to `finalize_perfetto_packets`.** `real_end_ts` is not known
until the trace closes, and the pair cannot go out during a convert pass. The
sweep that emits the `Processes` track emits the `Lifetime` slices too, one
pair per `ClippedSpan`, BEGIN before END for the reason ADR-0011 gives: a
zero-length span emitted END-first reads as `dur = -1`.

**A process with a span and no descriptor gets one.** `finalize` walks every
process with a recorded span, including one known only from
`add_process_liveness`. Where such a process has no `ProcessDescriptor`,
`finalize` calls `_emit_process_descriptor` for it. `get_process_track_ranks`
already ranks from `_process_lifetime_start`, which liveness observations fold
into. The rank and the `start_timestamp_ns` are in hand.

**The `Lifetime` BEGIN carries eight annotations.**

| Annotation | Type | Source |
|---|---|---|
| `cmdline` | string | `state.get_cmdline`, space-joined |
| `pid_epoch` | int | `Process.pid_epoch` |
| `interpreters` | int | count of `InterpreterTrack` in `state._tracks` |
| `clipped` | bool | `span.end_ts != span.real_end_ts` |
| `sampled_count` | int | new accumulator, `add_event` |
| `lost_count` | int | new accumulator, `add_loss_event` |
| `lost_pause_ns` + `lost_pause` | int + string | same accumulator |

`lost_pause_ns` and `lost_pause` are the ns-for-SQL and readable pair
`convert_loss_to_trace_format` already writes on a `GC Loss` slice.

The slice carries no `real_start_ts` or `real_end_ts`. Its own `ts` and `dur`
are those two numbers.

The `Processes` slice keeps all four annotations it has. It is the row an
operator scans to compare processes, and the `real_*` pair is what corrects
its own clipped drawing.

**Two new per-process accumulators on `PerfettoTrackState`.**
`record_sampled(process)` counts one record per `add_event`, and
`record_loss_totals(process, lost_count, lost_pause_ns)` sums a `LossMsg`'s
`gens`. Both are read at close.

`sampled_count` cannot come from the loss path. `EventsMonitor` emits a
`LossMsg` only for a poll interval that lost something, and the
`observed_count` on a `GC Loss` slice therefore counts records read during
lossy intervals. Summed per process it reads as a total and is not one, and
for a process that lost nothing it is zero, asserting gcmon read nothing where
it read everything. `lost_count` and `lost_pause_ns` are unaffected: a
`LossMsg` carries the whole loss for its interval.

**`interpreters` counts `InterpreterTrack`s, and that is every interpreter
gcmon saw.** `convert_item_to_trace_format` is the only place an
`InterpreterTrack` is built, and it takes the iid off a record. An interpreter
with no record reaches no part of the exporter. Loss does not widen the count
either: `gens_by_iid` is built from records already read.

**A bool debug annotation.** An int `clipped` renders as `1`.
`DebugAnnotationField.BOOL_VALUE` is already in `perfetto_proto`, and
`perfetto_builders` gains `_build_debug_annotation_bool` beside the int,
string and dict builders.

**What each linked record constrains.** ADR-0002: the slice reuses the process
track uuid, allocates none and raises no parenting question. ADR-0009: the
annotations are nanoseconds, as the `real_*` pair already is. ADR-0010: the
process track must hold an event, which the slice satisfies as the instant
did. ADR-0011: the sweep, the emission order and the ranking are reused
unchanged, and only the drawing on the process's own row is new. ADR-0012:
`--format jsonl` and `--format stdout` are untouched. ADR-0014: the seam is
the trace processor. ADR-0015: loss totals are summed from what the loss path
already reports, and no loss figure is recomputed. ADR-0025: every process the
monitor creates publishes a command line, and a quiet process has one to draw.

**Rejected: keep the instant and add a slice beside it.** Two events on the
row for one fact, and the instant's only job was to keep the row rendered,
which the slice does.

**Rejected: draw the clipped interval so the two rows agree.** It makes the
process's own row repeat a shortening that is an artifact of the other row's
stack, and leaves the observed interval readable only through annotations on
both.

**Rejected: move the workload's marks to a child row**, where nothing would
enclose them. `CONTEXT.md` defines the process track as the row that holds a
process's marks and its RSS, and the change would add a row per process to
preserve a depth nothing depends on.

**Rejected: duplicate `real_start_ts` and `real_end_ts` onto the slice** for
one query shape across both rows. On this slice they are `ts` and `dur`, and
the copy states the same fact twice.

**Rejected: move `cmdline` off the `Processes` slice** now that the process
row carries it. Clicking a span on the comparison row would stop saying what
it is running.

**Vocabulary.** `CONTEXT.md` defines **Span** as a slice on the `Processes`
track. There are two such slices now, and the entry keeps its meaning and
drops the track: a span is a slice bounding a process's observed lifetime, on
the shared row or on the process's own. The **Lifetime totals** entry points
the bare word **lifetime** at the `Processes`-track span, and repoints at the
interval.

## 5. Seams and testing decisions

- **Seam:** the trace processor, through
  `tests/exporters/test_perfetto_exporter_integration.py`. It is the highest
  seam that can see a slice's name, track, duration and args at once, and the
  only one that proves an annotation is attached to the right slice rather
  than present in the byte stream.
- **New seam needed:** none. `TestStartProcessMarker` already queries the
  marker through `slice` joined to `process_track` and `process`, and every
  new assertion is that query with a `dur` or an `args` join added.
- **What makes a good test here:** assert the meaning. The clipping case is
  the one that matters: build the crossing shape ADR-0011's Context describes,
  then assert the `Processes` span is short and the `Lifetime` slice on the
  same process's own row is not. A test reading only one of the two rows
  passes on an implementation that clips both.
- **Prior art:** `TestStartProcessMarker` for the marker's placement, the
  `debug.cmdline` assertions in the same file for reading an annotation
  through its slice, and `tests/exporters/test_perfetto_loss_track.py` for
  proving a slice landed on the process row rather than a child of it.
- **Cases:**
  1. Every process draws one `Lifetime` slice on its own process track, with
     `dur` equal to `real_end_ts - real_start_ts` from its `Processes` span.
  2. A clipped process draws the full interval on its own row and the
     shortened one on `Processes`, and carries `clipped` as true.
  3. A process reported only through `add_process_liveness` draws a process
     track, a descriptor and a `Lifetime` slice.
  4. Every annotation is readable from `args` through the `Lifetime` slice,
     and `sampled_count` matches the records fed in.
  5. A process that lost nothing carries `lost_count` 0 and a `sampled_count`
     equal to every record it produced.
  6. The workload's marks sit inside the slice, at depth 1 on the process row.
  7. `--format jsonl` and `--format stdout` output is byte-identical.
  8. `gcmon combine` draws the slice with no `cmdline` annotation and every
     other annotation present.

Existing assertions that move rather than break: the `Start Process` name
constant in `test_perfetto_exporter.py`,
`test_perfetto_exporter_integration.py`, `test_perfetto_format.py` and
`test_perfetto_slice_expansion.py`; the `s.depth = 0` filter and the
`_process_slices` helper in `test_perfetto_loss_track.py`; the instant counts
in `test_exporter_thread_safety.py`; and
`tests/fixtures/monitored_run_perfetto_trace.txt`.

Emission moving to close reverses packet order on the wire.
`test_perfetto_exporter.py` asserts the marker precedes the workload's first
instant in the stream; afterwards the instant is written first and the
`Lifetime` BEGIN last, while still sorting earlier by timestamp. That
assertion is on emission order and has to become an assertion on what the
trace processor reads back.

## 6. Out of scope

- **Python version and GC thresholds.**
  [0020](0020-process-metadata-in-perfetto-traces.md) owns both, including
  where they are sourced from. This spec re-aims that one at the `Lifetime`
  slice and builds none of it.
- **`loss_duration` on the slice.** One poll interval draws one `GC Loss`
  slice per interpreter over the same wall-clock window, and a sum of widths
  reports several times the interval gcmon was blind for. Carrying it means
  unioning the blind intervals per process, which is its own piece of work and
  answers a question `lost_count` and `lost_pause_ns` already cover.
- **Anything on the `Processes` track.** Its slices, annotations, clipping,
  ordering and emission are unchanged.
- **A per-interpreter lifetime.** An interpreter's observed interval is a
  ring-level figure and belongs with the statistics table.
- **Naming the workload.**
  [0062](0062-name-a-workload-from-a-sanitized-command-line.md) owns turning a
  command line into a name; this spec writes the joined command line the
  process track already carries.

## 7. Further notes

**The ADR rewrites come first.** This spec amends two records, and
[CONVENTIONS.md](CONVENTIONS.md) rule 10 puts the record ahead of the spec
that changes it. ADR-0010 loses the synthetic instant and gains the slice;
ADR-0011 gains the two-row divergence and the rule that the process's own row
draws the observed pair. Take both in the ADRs, and the sections above become
implementation steps.

**ADR-0011 also reverses itself.** It says of a zero-GC process that "It still
has no process track, which the UI hides anyway, the problem ADR-0010's
`Start Process` marker was invented for. Emitting one for it is out of scope."
Section 4 emits one. The **Rank gaps** consequence below that clause goes with
it: the gaps existed because a zero-GC pid consumed a rank with no descriptor
to apply it to, and once every process with a span has one the ranks run 0, 1,
2 again.

Three clauses are stale independently of this work, and the rewrite is where
they go:

- `finalize_perfetto_packets` says in its docstring that a liveness-only
  process has "no process descriptor and no cmdline". Since ADR-0025 the
  monitor publishes a command line for every process it creates, and this
  holds for `combine` and not for a live run.
- ADR-0011 says "Only a process with at least one non-meta event gets a rank".
  `get_process_track_ranks` ranks from `_process_lifetime_start`, which
  liveness observations fold into.
- ADR-0010 names "the chrome-perfetto equivalence test" as the consumer that
  must filter the marker. `--format chrome` is gone and that test with it.

**This work re-aims spec 0020.** Its section 4 puts `python_version` and
`gc_thresholds` on the `Processes` slice. They move to `Lifetime`, and process
metadata has one home. The edit is a paragraph in an unstarted spec. It
changes nothing about where 0020 sources its two values.
