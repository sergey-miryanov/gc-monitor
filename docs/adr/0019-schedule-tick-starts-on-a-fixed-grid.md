# ADR-0019: Schedule tick starts on a fixed grid, and skip the positions a slow tick misses

- **Status:** Accepted
- **Date:** 2026-08-17

## Context

`--rate` names the interval an operator wants between polls. The loop honoured it by waiting that
long *after* each tick, so the interval was the rate plus whatever the tick had cost, and the tick's
cost is set by the target: the number of live pids, the rings read for each, the RSS round. A wide
process tree therefore polled measurably slower than the number asked for, and the error accumulated
across a run rather than cancelling. Two captures of the same workload were not comparable either,
because a tree that fanned out mid-capture changed its own sampling interval partway through.

Three constraints shaped the fix.

**The wait must stay interruptible.** Shutdown reaches the loop by setting an event that a signal
handler owns, and the loop waits on that event rather than sleeping. That primitive rounds its
timeout up to the platform scheduler tick where a plain sleep does not, measurably so on Windows,
where the rounding is a large fraction of the default rate. Swapping it for a more precise sleep
would buy per-tick accuracy at the cost of shutdown latency.

**One instant already stamps a tick.** The loop reads the clock once, before the tick, and hands
that instant to the monitor for liveness ([ADR-0011](0011-process-lifetime-and-ordering.md)) and to
the RSS sampler, which both paces and stamps with it ([ADR-0013](0013-rss-sampling.md)). Everything
one tick emits agrees on that instant, and nothing converts it out of nanoseconds
([ADR-0009](0009-nanoseconds-canonical-time-unit.md)).

**The wait cannot be computed from that instant.** Working out how long to wait needs to know what
the tick cost, which needs the time *after* it. Using the pre-tick instant is exactly the arithmetic
that produced the defect.

## Decision

**Tick starts land on a fixed grid.** The loop holds a next-start instant, seeded from the first
stamping read, and waits until it rather than for a fixed span. Starts fall on `t0 + k * rate` for
whole `k`, whatever a tick costs.

**A second clock read paces the loop.** It is taken after the tick and the RSS round, immediately
before the wait. It stamps nothing, is passed to nothing, and does not leave `MonitorLoop.run`, so
ADR-0011 and ADR-0013 hold as written; ADR-0013 is amended to say *stamping* read. Collapsing the
two reads back into one reintroduces the defect.

**A tick that outlasts its position skips to the next position on the same grid.** The missed
positions are dropped and never made up. The phase survives, so the effective interval degrades in
whole multiples of the rate rather than to an arbitrary value, and two captures stay comparable even
when one of them fell behind. How far to skip is one division rather than a step per position, which
also terminates when the rate is zero: a rate of zero or less asks for no schedule and gets none,
leaving `MIN_IDLE_NS` as the only thing between two ticks.

**The wait is floored at `MIN_IDLE_NS`, one millisecond.** Without a floor, a tick finishing a hair
before its next position sends the loop straight back in and pins gcmon at a full duty cycle against
a target that is already struggling.

**The floor bounds the rate gcmon can hold, and the schedule follows the floor rather than fighting
it.** A rate at or below a millisecond cannot be met: the guard is longer than the interval asked
for, so tick starts land further apart than requested. The bound is academic, since a real tick
costs more than a millisecond on its own, but it is real, and it must not be *misreported*. When the
guard stretches a wait past the next position, the schedule moves to where the tick will really
begin instead of carrying the difference as a debt against the grid. Left as a debt it surfaces
later as a skipped position, and the summary blames the target for a wait gcmon chose. Choosing to
wait and failing to keep up are different things and the report distinguishes them.

**A run answers a `RunReport`.** `MonitorLoop.run` returns how many ticks ran and how many positions
were scheduled, and the end-of-run summary states both. Without it a run that never kept up is
indistinguishable from a healthy one: both show low coverage, and only this says whether gcmon ever
got to look as often as it was asked to.

**A run overruns at a share of its ticks, `OVERRUN_SHARE`, not at one missed position.** The wait
primitive rounds its timeout up to the platform scheduler tick, so an occasional overshoot past a
position is what a healthy run looks like, and a long one is near certain to contain a few: a
ten-minute run at the default rate is thousands of ticks. Reading one late wake-up as an overrun
would tell an operator their rate is unreachable, and suppress the advice that would have helped, on
the strength of noise this record already accepts. A tenth of the run has to go missing first.

**The low-coverage advisory no longer prescribes a remedy.** It fires the first time coverage dips,
which can be before the loop has run enough ticks to know whether it is holding its schedule, and
whether polling more often can help at all depends on that. It states what survives the loss; the
summary carries the remedy, choosing between "polling more often may observe more" and "the loop
overran, so a smaller `--rate` will not help" on the report. No loop state crosses into the monitor,
which keeps [ADR-0017](0017-monitor-owns-the-pid-lifecycle.md)'s boundary intact.

## Consequences

- The interval an operator asks for is the interval they get, at any tick cost, so `--rate` is a
  property of the capture rather than of the target's size.
- Per-tick jitter remains: the wait primitive still rounds up to the scheduler tick. Scheduling
  against an absolute grid does not remove that error, it stops it accumulating, and a late wake-up
  is absorbed by the next wait instead of shifting every tick after it.
- **Loss-window widths become predictable.** A window's edges are per-pid read instants and are
  untouched by this, but its width is the gap between consecutive reads of the same pid, which the
  schedule now sets. Comparing loss windows across a run, or across captures, means something it did
  not before.
- RSS sampling inherits an evenly spaced schedule at no cost, since the sampler paces off the
  stamping instant and its own interval logic is unchanged.
- Rates at or below a millisecond are not honoured, and the report will not say so: it counts
  positions the target cost gcmon, and a rate below the floor costs none. The bound belongs to the
  constant and is documented there; if `--rate` ever grows a lower bound, this is the number.

## Alternatives considered

- **Re-basing the schedule to `now + rate` after each tick.** Rejected: one overrun shifts the phase
  permanently, and a run that overruns on most ticks has reverted to treating the rate as a gap,
  which is the defect. It fixes the accumulating error without fixing the coupling to tick cost.
- **Running the missed ticks back-to-back to preserve the count.** Rejected outright: it schedules
  the heaviest polling at the moment the target is slowest, and the extra polls read a ring that has
  not refilled.
- **A floor proportional to the rate.** Rejected: it lowers the protection exactly when the operator
  lowers the rate, which is when a tick is most likely to outlast its position. An absolute constant
  is correct across the range the rate spans, at the cost of bounding that range at the bottom.
- **Capping the floor at the rate**, so the guard never exceeds the interval asked for. Rejected: it
  does not remove the conflict where the two are equal, and it weakens the spin-guard precisely
  where ticks are shortest and the loop is most able to spin. Bounding the rate and saying so is
  more honest than a guard that quietly stops guarding.
- **Changing the wait primitive** to beat the scheduler quantum, with a chunked sleep or by raising
  the platform timer resolution. Rejected: it costs shutdown latency, and the grid already stops the
  error accumulating, which is the part that made captures incomparable.
- **Extracting the arithmetic into a schedule object.** Rejected for now: it is fifteen lines of
  integer arithmetic with no I/O, and a separate object earns itself only if something other than
  the loop needs to ask. The cost is that pacing is tested by observing the timeout the loop passes
  to its stop event, which reaches a private attribute; if the arithmetic ever moves, the tests
  should move onto it.
- **Lending the monitor an overrun predicate**, the way the loop already lends it a stop predicate,
  so the advisory could pick its own wording. Rejected: the answer is meaningless in the first few
  ticks, exactly when the advisory is most likely to fire, so it would need a warm-up threshold with
  nothing to justify it.

## Implementation

- `src/gcmon/monitor_loop.py` holds the grid, the two clock reads, the skip, `MIN_IDLE_NS` and the
  counters. `MonitorLoop.run` returns the report.
- `src/gcmon/run_report.py` holds `RunReport` and `OVERRUN_SHARE`, in a module of its own because
  the report crosses from the loop to the summary. `PollReport` sits beside its producer in
  `monitor.py`; this one cannot, because `stats_output` is reached from `_env` and the option
  parser, and importing the loop there would pull the monitor in behind every environment read.
- `src/gcmon/stats_output.py` states the tick counts and selects the remedy; `src/gcmon/monitor.py`
  carries the advisory that no longer prescribes one.
- Tests: `tests/monitoring/test_monitor_loop.py` for the schedule, the skip, the floor and the
  report, driven by a scripted clock and a stop event that records what it was asked to wait for,
  never by elapsed wall time, which would assert the operating system rather than gcmon;
  `tests/stats/test_stats_output.py` for the summary line and the two remedies;
  `tests/test_monitor_coverage.py` for the advisory keeping to what it knows.
