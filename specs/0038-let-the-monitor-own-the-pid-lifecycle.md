# 0038 — Let the monitor own the pid lifecycle

- **Status:** Not started
- **Kind:** feature — cleanup
- **Effort:** M
- **Origin:** code structure review of `src/gcmon`, 2026-08-15
- **Respects:** [ADR-0011](../docs/adr/0011-process-lifetime-and-ordering.md) (liveness reported
  once per tick, after the poll phase — **this moves the reporting site and the ADR is amended
  with it**), [ADR-0013](../docs/adr/0013-rss-sampling.md) (the sampler is driven once per tick
  from the same instant), [ADR-0015](../docs/adr/0015-gc-loss-spans-on-their-own-track.md)
  (per-`(pid, iid, gen)` ring state)

## 1. Problem statement

Everything gcmon knows about a monitored pid has two owners. `EventsMonitor` keeps the ring
state — the `collections` cursor and the last poll instant that ADR-0015's loss arithmetic runs
on — while `MonitorLoop` keeps the wait policy that decides when that pid is finished, and each
prunes its own half against the same set of child pids, computed twice per tick from the same
listing.

The hazard is a recycled pid. The monitor's own reason for having a `forget` method is that a
reused pid must inherit no counter and no poll instant from the process before it: if it did,
the next poll would compare a fresh process's `collections` counter against a dead one's cursor
and report a loss window full of records that never existed — an operator would see a `GC
Loss(0)` span claiming hundreds of missing collections in an interval where nothing was lost.
Whether that can happen depends on the two prunes staying in agreement, and they are in
different modules, computed from different expressions, with no test that compares them.

`EventsMonitor` also exposes its exporter as a public property for one reason: so the loop can
reach through it and report liveness.

## 2. Solution

Nothing changes for an operator: the same poll cadence, the same spans, the same trace. What
changes is that one tick of monitoring is one call. The monitor answers "who was alive, and
should I keep going", and owns every piece of per-pid state behind that answer. The loop is
left with what it is for — timing and the stop signal.

## 3. User stories

1. As an operator monitoring a process tree that churns workers, I want a recycled pid to start
   from nothing, so that gcmon never reports a loss window for collections that never happened.
2. As a maintainer, I want per-pid state pruned once against one set, so that two prunes cannot
   disagree.
3. As a maintainer, I want the exporter to stop being reachable through the monitor, so that
   the only code emitting to it is the code that owns what it emits.
4. As a maintainer reading `MonitorLoop`, I want to see timing and shutdown, so that the file's
   subject is what its name says.
5. As a maintainer writing a test for poll behaviour, I want to drive one method and assert on
   its report, so that a test does not have to reproduce the loop's orchestration to exercise
   the monitor.
6. As an operator, I want the RSS sampler to keep pacing off the same instant the trace is
   stamped with, so that a sample and a liveness observation from one tick agree.
7. As a maintainer, I want a wait policy's lifetime tied to the pid it judges, so that a pid
   the policy gave up on cannot be re-polled by a fresh policy that answers "still starting".

## 4. Implementation decisions

**4.1 — One tick, one call.** `EventsMonitor` gains a method that performs a whole tick and
returns a small report: the pids that answered `PollStatus.OK`, and whether any policy still
wants the loop to continue.

```python
class PollReport(msgspec.Struct):
    live_pids: frozenset[int]   # answered OK this tick
    keep_running: bool          # any wait policy still holds the loop open
```

It absorbs, in the order the loop runs them today: child discovery, the prune of both the ring
state and the wait policies against the child set, the per-pid enable check, the poll, the
policy verdict, `forget` for a pid the policy gave up on, and the liveness report. The loop
keeps the clock, the stop event, the rate, the RSS sampler and the `keep_running` break.

**4.2 — The wait policies move to the monitor, and so does the enable predicate.** The policy is
per-pid state whose lifetime is exactly the ring state's lifetime; that is what makes the double
prune possible. The control-plane predicate moves with it, which also removes a genuine reading
hazard: `EventsMonitor._enabled` is a bool meaning "not stopped" and `MonitorLoop._enabled` is a
per-pid callable meaning "the control server has not suppressed this pid". Two fields, one name,
two modules, unrelated meanings. The monitor keeps the flag and takes the predicate under a name
that says which it is.

**4.3 — `EventsMonitor.exporter` goes.** Its only caller is the loop's liveness call, which
moves inside. Nothing else reaches through it.

**4.4 — ADR-0011's constraints are preserved exactly, and the ADR is amended to match.** The
record anchors liveness reporting on `MonitorLoop` by name, in its liveness section and its
implementation notes, so moving the call is a name move and ADR-0011 gets amended rather than
contradicted — the ADR README's rule for exactly this case. What must not change: one
`time.monotonic_ns()` per tick; liveness reported **after** the poll phase, never during it;
the call skipped on an empty live set; and the same instant handed to the RSS sampler in
seconds. The ADR's note that a batch crossing `flush_threshold` mid-poll can still emit a
rank-less descriptor stays true, and stays true for the same reason.

**4.5 — `create_monitor` goes.** It forwards three arguments to the constructor and adds
nothing. It is re-exported from `gcmon/__init__.py`, so it stays as a name for one release,
aliased to the class, and the `__all__` entry goes when it does.

**Rejected: leave the split and add a test that the two prunes agree.** It pins the coupling
rather than removing it, and it can only compare the two expressions on inputs a test thinks
of. The prune is one operation; it should be written once.

**Rejected: move the timing into the monitor as well, leaving `MonitorLoop` a shell.** The
`Runner` seam (`run_policy`) is what makes a duration-limited run testable without waiting, and
the stop event is what the signal handler sets. Both belong to the loop. Two modules is the
right number; the line between them is what is wrong.

**Open, to settle when picked up:** whether `PollReport` carries the live set as a `frozenset`
or the loop keeps taking a `set`. `RssSampler.tick` takes `set[int]` today. Settled by whether
anything mutates it — nothing does, so `frozenset` unless the sampler's signature is more
disruptive to change than it is worth.

## 5. Seams and testing decisions

- **Seam:** `tests/monitoring/test_monitor_loop.py` and `tests/monitoring/test_monitor.py`,
  with `MockExporter` from `tests/helpers.py` recording what was emitted. This is the highest
  seam that can observe the behaviour: the defect class is per-pid state management, which
  produces no trace bytes of its own — it corrupts the *next* poll's loss arithmetic, which is
  observable at the exporter.
- **New seam needed:** none. `PollReport` is a return value at an existing seam, not a new one;
  it replaces assertions that currently reach into loop-local dicts.
- **What makes a good test here:** drive several ticks with a scripted child-pid listing and
  assert on what reached the exporter — specifically that a pid which disappears and returns
  produces **no** loss window on its first poll back. A test that asserts the internal state
  dicts are empty proves the prune ran, not that the prune was correct; the observable
  consequence is the absence of a fabricated `GC Loss` span.
- **Prior art:** `tests/monitoring/test_monitor_loop.py` for driving ticks against a fake
  runner; `tests/test_loss_replay.py` and `tests/captures.py` for replaying a known capture and
  asserting the reconstruction; `tests/monitoring/conftest.py` for the monitor mock.
- **Cases:**
  1. A pid that exits and whose number is reused starts from a clean cursor: its first poll
     back emits records and no loss window.
  2. A pid the wait policy gives up on is not re-polled by a fresh policy on the next tick.
  3. A failed child listing prunes nothing — today's `None` means "no answer", and a tick that
     cannot enumerate children must not drop state for pids it simply could not see.
  4. Liveness is reported once per tick, after the polls, skipped when nothing answered OK, and
     stamped with the same instant the RSS sampler paced off.
  5. Regression guard: a full monitored run over `tests/captures.py` produces the same trace it
     does today, byte for byte on the Chrome leg.

## 6. Out of scope

- The loss arithmetic itself. `RingAccumulator` and ADR-0015 are untouched; this changes who
  owns the accumulator's lifetime, not what it computes.
- The `Runner` / `WaitPolicy` protocols. Their shape is right; only where the policy instances
  live changes.
- The control plane. `ControlServer.is_enabled` keeps its signature; only the caller moves.
- Making the monitor thread-safe. It is driven from one loop today and this does not change
  that.
- Anything about child-process discovery itself, including whether `get_child_pids` should be
  cached across ticks.

## 7. Further notes

The two-prune hazard is currently latent rather than live: today both prunes derive from the
same `children` list within one tick, so they agree. It is a structural risk, not a bug report —
the fix is worth doing because the next edit to either module is what would separate them, and
the failure it produces (a fabricated loss window) looks like a gcmon measurement rather than a
gcmon bug.
