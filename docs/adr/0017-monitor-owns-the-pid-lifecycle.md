# ADR-0017: Give the monitor every piece of per-pid state, and leave the loop the clock

- **Status:** Accepted
- **Date:** 2026-08-17

## Context

`EventsMonitor` held the ring cursors and the poll instant that
[ADR-0015](0015-gc-loss-spans-on-their-own-track.md)'s loss arithmetic runs on. `MonitorLoop`
held the `WaitPolicy` deciding when a pid was finished. Both pruned their own half against the
child pids, computed twice per tick from one listing, in two modules, through two expressions,
with no test comparing them.

They agreed, so no trace anyone opened was wrong. What they would produce on disagreeing is the
reason to remove the split: a pid the OS reuses inherits the dead process's `collections` cursor,
the next poll subtracts a fresh counter from a stale one, and gcmon draws a `GC Loss` span for
hundreds of collections that never ran. An operator reads that as a measurement, not a bug.

`EventsMonitor` also exposed its exporter as a public property. One caller used it: the loop,
reaching back through the monitor to report liveness.

## Decision

**One tick of monitoring is one call.** `EventsMonitor.tick` performs a whole tick and returns a
`PollReport` carrying the pids that answered `PollStatus.OK` and whether any policy still wants
the run open. It absorbs child discovery, one prune, the control-plane enable check, the poll,
the policy verdict, and the liveness report.

**Per-pid state has one owner and one prune.** Cursors, poll instant, streaming stats and wait
policy share a lifetime, so one pass over one child set drops them together.

**The loop keeps the clock and the stop signal.** `MonitorLoop` reads `time.monotonic_ns()` once
per tick, hands that instant to `tick` and the same one in seconds to `RssSampler`, breaks on
`keep_running`, and owns the `threading.Event` a signal handler sets. It lends the monitor a
read of that event so a shutdown need not wait out a process tree.

**`EventsMonitor` requires a wait policy factory, keyword-only.** There is no safe default.
`no_wait_policy` gives up on the first failed poll, so a monitor that got it by omission would
end a run against a target still initializing, and would end it by answering
`keep_running=False`, which the loop reads as an orderly finish. The failure leaves no error to
trace. Every construction site names a policy, including the nine test monitors that only poll
and do not care which.

**A pid the policy gives up on keeps its policy and loses its cursors.** A replacement policy
would not have seen the pid alive, so it would answer "still starting" to every later invalid
poll and hold the run open for a whole startup timeout.

**A failed child listing prunes nothing.** `None` means the OS did not answer, not that the tree
is empty. Reading it as empty would drop every live child's cursor and re-export its whole ring.

## Consequences

- Two prunes cannot disagree, because there is one.
- A test drives one method and asserts on its report, instead of reproducing the loop's
  orchestration against a mock.
- Every construction site names a wait policy, including the eight test monitors that only poll
  and do not care which one. That is the cost, and it buys "no policy was configured" being
  unreachable by omission.
- Liveness reporting moved off `MonitorLoop`, so
  [ADR-0011](0011-process-lifetime-and-ordering.md) was amended rather than contradicted. Its
  constraints hold unchanged: one clock read per tick, reporting after the poll phase, the call
  skipped on an empty live set, and the same instant reaching the RSS sampler
  ([ADR-0013](0013-rss-sampling.md)).
- A pid that leaves the tree and returns re-exports whatever its ring still holds, since the
  prune took its cursor. Duplicate slices are what the prune costs, and the alternative is the
  fabricated loss window above.
- `MonitorLoop` is now short enough that folding it into the monitor looks tempting. The
  rejected alternative below says why it stays.
- Nothing thread-safe was added. One loop drives the monitor, as before.

## Alternatives considered

- **Leave the split, add a test that the two prunes agree.** Pins the coupling instead of
  removing it, and compares the two expressions only on inputs a test author thought of.
- **Move timing into the monitor too, leaving `MonitorLoop` a shell.** Rejected: the `Runner`
  seam makes a duration-limited run testable without waiting, and the stop event is what the
  signal handler sets. Two modules is the right count; the old line between them was wrong.
- **Default `wait_policy_factory` to `no_wait_policy`.** Rejected for the silent early exit
  above. A required argument costs eight test call sites once.
- **Keep `create_monitor`,** either as an alias for the class or as a function supplying
  `no_wait_policy`. Deleted instead: counting call sites found none. The command path constructs
  `EventsMonitor`, the nine poll-only test monitors construct it too, and no example or doc page
  imports the name. It was a convenience for a caller nobody could point to.
- **Have `PollReport` carry a `set`.** `frozenset`, since nothing downstream mutates the live
  set and both the exporter and the prune already took `collections.abc.Set`.

## Implementation

- `src/gcmon/monitor.py` holds the tick, the prune and `PollReport`.
  `src/gcmon/monitor_loop.py` holds the clock, the stop event, the rate and the sampler call.
  `src/gcmon/wait_policy.py` holds `no_wait_policy`, a function rather than the class object,
  which satisfies `WaitPolicyFactory` structurally but not to a type checker.
- `tests/monitoring/test_monitor.py` drives ticks against a scripted child listing and asserts
  at the exporter. A pid that leaves and returns holding an unrelated counter emits records and
  no loss window; the same ring without the departure emits a window of 297, which is what gives
  the first assertion teeth. Asserting that the state dicts are empty would prove the prune ran,
  not that it was right.
- `tests/monitoring/test_monitored_run_trace.py` runs the whole loop over the capture in
  `tests/captures.py` on a scripted clock and pins the Chrome output against
  `tests/fixtures/monitored_run_chrome_trace.json`. It was written before this change and passed
  through it untouched, which is the evidence that operators see the same trace.
- Two rules survive only because a test states them: `tests/monitoring/test_monitor.py`
  `TestAPidThePolicyGaveUpOn` covers both halves of policy-stays-cursors-go, since a test
  watching one half passes with the other inverted.
