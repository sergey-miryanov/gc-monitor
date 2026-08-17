# 0049 — Poll on the requested schedule, and report when the loop overruns

- **Status:** **Pinned** (`tests/monitoring/test_monitor_loop.py::TestTheTickInstant::test_one_clock_read_per_tick_shared_with_the_sampler`)
- **Kind:** bug — correctness
- **Effort:** S
- **Origin:** a design session on 2026-08-17, from the observation that the loop runs at a rate
  nobody asked for
- **Respects:** [ADR-0009](../docs/adr/0009-nanoseconds-canonical-time-unit.md) (nanoseconds
  inside gcmon; convert at the boundary),
  [ADR-0011](../docs/adr/0011-process-lifetime-and-ordering.md) and
  [ADR-0013](../docs/adr/0013-rss-sampling.md) (one instant stamps a whole tick, monitor and
  sampler share it unconverted),
  [ADR-0017](../docs/adr/0017-monitor-owns-the-pid-lifecycle.md) (the monitor owns per-pid state;
  the loop owns the clock),
  [ADR-0014](../docs/adr/0014-perfetto-integration-test-strategy.md) (no probabilistic suite for
  this)

## 1. Problem

Run `gcmon run --rate 0.1` against a process tree of any width and gcmon does not poll ten times a
second. It polls every `0.1 s` *plus* however long a tick took, so the real interval is set by the
target: a tree wide enough to cost 30 ms a tick polls at 7.1 Hz. Nothing says so. The operator
sees low coverage in the `--stats` footer, reads the advisory telling them to lower `--rate`,
lowers it, and gets no improvement, because the number they lowered was never the one deciding how
often gcmon looked.

Two captures of the same workload are not comparable either. The interval tracks tick cost, tick
cost tracks the number of live pids, so a run that fans out mid-capture silently changes its own
sampling interval partway through.

## 2. Evidence

`MonitorLoop.run` sleeps a constant *after* the work:

```python
# MonitorLoop.run
report = self._monitor.tick(now_ns, self._stop_event.is_set)
...
self._stop_event.wait(timeout=self._rate)
```

Nothing subtracts the tick. The interval between two tick starts is `rate + tick_cost`, and the
error accumulates over a run rather than cancelling.

Measured on one Windows box, Python 3.15, `--rate 0.1`, 30 ticks, with a sleep standing in for the
tick. Read the ratios; the absolute figures date to the machine.

| tick cost | interval, median | drift over 29 ticks |
|-----------|------------------|---------------------|
| 4 ms | 109 ms | +272 ms |
| 30 ms | 141 ms | +1174 ms |
| 4 ms, scheduled against a deadline | 95 ms | +16 ms |
| 30 ms, scheduled against a deadline | 95 ms | +13 ms |

A second, independent contributor: `threading.Event.wait` quantises to the platform scheduler
tick, where `time.sleep` does not. On the same box `wait(0.020)` slept 31 ms, `wait(0.100)` slept
109 ms, and `wait(0.200)` slept 203 ms; `time.sleep` was within 0.7 ms at every one of those. The
loop must keep `Event.wait`, because shutdown has to interrupt the sleep — so a scheduled tick
start still lands up to a scheduler quantum late. The rows above show that scheduling against a
deadline stops that error accumulating even though it cannot remove it.

ADR-0013 already recorded the tick cost exceeding the rate, as a bound on RSS timestamp skew:
"the skew is bounded by how long the polls take, which on a wide tree exceeds the 0.1 s rate". The
same sentence is the bug, seen from the other side.

## 3. Scope

**Affected:** every live monitoring run, `gcmon run` and `gcmon monitor`, on every `--format` and
every platform. The pacing decision is entirely inside `MonitorLoop.run`; the operator-facing
consequences are the poll interval, the width of every `GC Loss` window, and the interval between
RSS samples on a run whose `--rss-interval` is at or below `--rate`.

**Not affected:** where a loss window's *edges* fall. Those are per-pid `ts_read_start` instants
taken inside `EventsMonitor.poll`, one clock read per pid per tick, and this change does not touch
them. It changes how far apart two consecutive reads of the same pid are — the window's **width**
— which today is `rate + tick_cost` and after this is `rate`.

Also not affected: the loss arithmetic in `RingAccumulator`, the wait policies, the run policies
in `run_policy`, discovery and pruning, and anything in the exporters. `DurationRunner` keeps
measuring wall time and ends a run at the same instant it does today; what changes is that the
number of ticks inside that wall time becomes predictable.

**Why the suite did not catch it:** nothing asserts an interval. `tests/monitoring/test_monitor_loop.py`
drives the loop with a mock runner yielding a fixed number of times and a mock monitor that
returns instantly, so the tick cost is nearly zero and `rate + tick_cost` is indistinguishable
from `rate`. The defect only appears when a tick costs something, which no unit test arranges and
no benchmark covers.

## 4. Proposed change

1. **Schedule tick starts against a deadline.** The loop keeps a next-start instant in
   nanoseconds, seeded from the first stamping read. After the tick, it advances the deadline by
   the rate until the deadline is in the future, and waits until it.

2. **Read the clock a second time, after the tick, for pacing only.** The wait cannot be computed
   from the pre-tick instant without reintroducing the defect. The two reads have different jobs
   and the spec depends on their staying separate: the first stamps — liveness (ADR-0011), the RSS
   round (ADR-0013) — and is passed downstream unconverted; the second stamps nothing and reaches
   nothing outside `run`. The order in the body is: stamping read, `monitor.tick`, RSS round,
   `keep_running` check, pacing read, wait. The RSS round is inside the measured cost, because it
   is inside the tick.

3. **Skip missed positions; preserve the phase.** When a tick outlasts its position, the loop goes
   to the next position on the original grid, so tick starts stay on `t₀ + k·rate` for whole `k`
   and the effective interval degrades in multiples of the rate. Missed positions are dropped and
   never made up.

   **Settled:** re-basing the deadline to `now + rate` is rejected — one overrun would shift the
   phase permanently, and a run that overruns on most ticks has reverted to today's behaviour with
   `rate` as the gap again. Running the missed ticks back-to-back to preserve the count is
   rejected outright: it schedules the heaviest polling at the moment the target is slowest, and
   the extra polls read a ring that has not refilled.

4. **Floor the wait at 1 ms**, a module constant in `monitor_loop.py`. The grid already guarantees
   the interval is never shorter than the rate, so the only thing left to protect is idle: a tick
   that finishes a hair before the next position would otherwise re-enter immediately and pin
   gcmon at a full duty cycle against a target that is already struggling. Under a phase-preserving
   grid that window is narrow, so the floor is a spin-guard and not a policy, and 1 ms is sized for
   that. It is a constant rather than a fraction of the rate because a fraction lowers the
   protection exactly when the operator lowers the rate to chase coverage. It is not a constructor
   parameter: `MonitorLoop.__init__` gains nothing, and the parameter-set assertion in
   `TestTheLoopHoldsNoPerPidState` stays green.

5. **Count what was skipped, and return it.** The loop accumulates ticks run and positions skipped;
   `run()` returns a small `msgspec.Struct` carrying `ticks_run` and `ticks_scheduled`, with
   `overran` derived from them. It mirrors `PollReport` one level up: one tick answers a
   `PollReport`, one run answers this. The counters stay private to the loop and leave it only in
   the returned value.

6. **Split the coverage advisory by what each side knows.** `EventsMonitor._warn_low_coverage`
   fires once, mid-run, the first time coverage drops below its floor — which can be seconds in,
   before the loop has enough ticks to know whether it is keeping up. It keeps the half that is
   true regardless of pacing (percentiles are sampled and read high; counts and sums are
   reconstructed and exact) and loses its closing remediation sentence. The remediation moves to
   `summary_lines`, which runs after `loop.run()` returns and can be handed the report: either
   polling more often may observe more, or the loop overran on *N* of *M* scheduled ticks and a
   smaller `--rate` will not help.

   **Settled:** lending the monitor a saturation predicate, the way the loop already lends it
   `_stop_event.is_set`, is rejected. The answer is meaningless in the first few ticks — exactly
   when the advisory is most likely to fire — so it would need a warm-up threshold nobody can
   defend, and it would put loop state inside the monitor for no gain. Splitting the message keeps
   ADR-0017's boundary intact and means neither half is ever false.

7. **Keep all arithmetic in nanoseconds**, converting once where `Event.wait` demands seconds
   (ADR-0009). `rate` stays a float of seconds on the constructor, because an operator types it,
   and is converted once at construction, as `RssSampler` does with its interval.

8. **Correct the `--rate` help text** to describe an interval between poll starts rather than a
   rate. The name is not touched here; see §6.

## 5. Seams and testing decisions

- **Seam:** `tests/monitoring/test_monitor_loop.py`, in two halves. The pacing is asserted by
  observing the timeout the loop passes to its stop event, with `time.monotonic_ns` patched to a
  fixed sequence so tick cost is exact and no test sleeps. The counters are asserted on the value
  `run()` returns, which is public.
- **New seam needed:** none, but be honest about the rung. Observing the timeout means reaching
  `loop._stop_event`, the lowest rung on CONVENTIONS' ladder. It is the highest seam available
  once the arithmetic stays inline in `MonitorLoop`: nothing public exposes an intended wait, and
  the alternative — asserting real elapsed time — is the probabilistic suite ADR-0014 exists to
  forbid. The existing tests in this module already reach `loop._stop_event` directly. If the
  arithmetic is ever extracted into an object of its own, the pacing tests should move onto it and
  this note is the reason why.
- **What makes a good test here:** assert the interval the loop *intends*, never the interval it
  achieves. The achieved one carries a scheduler quantum of noise on every platform and up to
  16 ms of it on Windows (§2), so a test that measures elapsed time is asserting the operating
  system. Feed instants, assert the timeout.
- **Prior art:** `TestTheTickInstant` for patching `time.monotonic_ns` with a `side_effect`
  sequence and asserting what reached the monitor and the sampler; `TestRssSamplerInLoop` for
  driving a fixed number of ticks through `_runner(n)`; `tests/test_rss_sampler.py` for interval
  arithmetic asserted without a clock.
- **Cases:**
  1. The case that fails today: rate 0.1, stamping read at `t`, pacing read at `t + 30 ms` ⇒ the
     wait is 0.070, not 0.1. Today it is 0.1 regardless of the second instant.
  2. Phase preservation across an overrun: rate 0.1, tick ends at `t₀ + 150 ms` ⇒ the next start is
     `t₀ + 200 ms`, so the wait is 0.050 and one position was skipped. The following tick is still
     on the original grid.
  3. The floor: a tick ending a hair before its next position waits 1 ms, not the sub-microsecond
     remainder. Assert the constant, not a range.
  4. The regression guard, and the pinned test amended: exactly one *stamping* read per tick, still
     shared unconverted between monitor and sampler. Its current form asserts
     `monotonic_ns.call_count == 2` for a two-tick run, which the pacing read breaks; narrow it to
     the instant that reaches `monitor.tick` and `rss_sampler.tick` rather than deleting it. Both
     must still receive the same `int`, and the sampler must still be paced by it.
  5. The report: `ticks_run + skipped == ticks_scheduled`, a run where nothing overran reports zero
     skipped and `overran` false, and a run that breaks early on `keep_running` reports the ticks
     it actually ran.
  6. The advisory split: the mid-run warning no longer contains remediation, and the end-of-run
     summary carries the one of the two sentences the report selects. Assert the branch, not the
     wording.

## 6. Out of scope

- **Renaming `--rate`.** It is a period in seconds under a name that means a frequency, and the
  code shows the strain — `monitoring_options` logs `"Rate: %ss"`, and ADR-0013 writes "10 Hz" and
  "the 0.1 s GC poll rate" one sentence apart. The confusion is plausibly upstream of this bug:
  nobody sleeps `rate` after the work if they are thinking "period". But the name has 45
  references across five doc pages, three ADRs, five specs, the CLI, the advisory text and the
  tests, and renaming it is a compatibility decision with no bearing on pacing. Spec 0050.
- **A trace counter track for overruns**, in the style of 0033's loss counter. It would answer
  "one bad phase or the whole run?", which the summary's two integers cannot, but it reaches into
  the exporter layer for a fix otherwise confined to one file and one log line. Take it when
  someone has a capture where the answer matters.
- **Changing the wait primitive** to beat the scheduler quantum — a chunked sleep, or raising the
  platform timer resolution. It buys per-tick precision at the cost of shutdown latency, and §2
  shows deadline scheduling already stops the error accumulating, which is the part that made
  captures incomparable.
- **A lower bound on `--rate`.** A rate below the floor is already well defined: every tick
  overruns, the floor applies, and the summary reports it. Validation would add a threshold to
  argue about and tell the operator nothing the report does not.
- **RSS pacing.** `RssSampler.tick` checks its own interval against the instant it is handed, so
  regular tick starts make RSS sampling regular for free. No change, and none needed.
- **What the coverage floor is, or when the advisory fires.** This spec moves one sentence out of
  the message and leaves the trigger alone.

## 7. Further notes

**Two records to write when this lands, not before.** Both would be false today, and
`docs/adr/README.md` dates a record to when the change shipped.

- **A new ADR** for the pacing policy. It has to exist because this spec is deleted on retirement
  and two of its decisions are ones a future reader will try to undo. The second clock read reads
  as a violation of ADR-0011 and ADR-0013 — someone tidying up will collapse it back and
  reintroduce the bug — and skip-don't-catch-up reads as a missing feature rather than a rejected
  alternative. Its content is §4 steps 2 to 4 plus the rejected alternatives named there; the
  measurements in §2 stay here, since a reading from one machine settles nothing the shape does
  not settle on its own.
- **An amendment to ADR-0013**, narrowing "the loop takes one `time.monotonic_ns()` per tick and
  passes it unconverted" to one *stamping* instant per tick. The pacing read stamps nothing and is
  passed nowhere, so it does not overturn that decision — but the sentence as written becomes
  false the moment this lands, and CONVENTIONS §4 says amend the record rather than the code alone.

**On the vocabulary.** `CONTEXT.md` gained **Tick**, **Poll**, **Rate** and **Overrun** alongside
this spec, because the loose usage is what made the bug easy to write: prose in this repo has used
"poll" for both one pid's read and one iteration of the loop, and "rate" for both a frequency and
an interval. The words used here follow those entries. Note in particular that there is no noun
for a position on the schedule — `slot` belongs to the ring buffer, where 0024 counts ring slots —
so the report counts ticks, and **overrun** is the one word for the condition at both scales.

**On the measurements.** The table in §2 stands a `time.sleep` in for the tick, so it isolates the
scheduling arithmetic and says nothing about what a real tick costs. It is evidence that the
interval is wrong and that deadline scheduling fixes it, not a benchmark of gcmon. Two numbers
worth having afterwards, neither of which blocks the fix: what a tick actually costs across tree
widths, and how often a default-rate run overruns in practice. 0048 has the read-cost half of the
first.
