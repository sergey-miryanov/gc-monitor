# ADR-0020: Attach to a process once, and let go the moment a read fails

- **Status:** Accepted
- **Date:** 2026-08-17

## Context

Reading a process's GC records has two halves. One is finding the target: opening it, locating its
runtime, reading and validating its debug offsets. The other is copying the rings out. Only the
second is the point, and gcmon used to do both on every poll of every process, throwing the first
half away each time.

The two halves are not comparable in cost. Attaching is dominated by scanning the target's loaded
modules for the runtime section and validating what it finds; a read is a handful of remote memory
copies. The ratio holds at roughly two orders of magnitude on any machine: time
`_remote_debugging.get_gc_stats` against a held `_remote_debugging.GCMonitor` on the same target.
gcmon was therefore spending upwards of nine tenths of every poll re-deriving what it had derived
on the poll before, per process, per tick, across a whole process tree.

CPython 3.15 offers both shapes: a free function that attaches, reads and detaches, and a
`GCMonitor` object that attaches once and reads many times. Taking the second is arithmetic, and it
is not free: the pid stops being an argument and becomes an identity, something gcmon now *holds*
per process, with a lifetime, in a program whose central hazard is a pid that outlives the
process that owned it ([ADR-0017](0017-monitor-owns-the-pid-lifecycle.md)).

## Decision

**Attachment is state, so it has one owner and one prune.** It lives behind `EventsReader` in
`gcmon.events_reader`, injected into `EventsMonitor` as a required argument, and the monitor drops
it in the same pass that drops that pid's cursors and streaming statistics. ADR-0017's rule extends
to it unchanged: cursors and attachment share a lifetime, and nothing prunes either one anywhere
else. A default reader was rejected; see below.

**`debug=True` is passed explicitly, and it selects an exception type rather than a log level.**
Under `debug=False` a dead target surfaces as `ProcessLookupError`; under `debug=True` CPython
*replaces* the live exception with a `RuntimeError` carrying a descriptive message and demotes the
original to `__cause__`. It is not a verbosity setting and does not print anything. gcmon passes
`True` because that is what the free function hardcoded, so the switch to `GCMonitor` changes what
gcmon reads and not what it catches or logs.

`debug=False` gives strictly better signal: it is the only way to tell a target that has died from
one whose GC layout does not match gcmon's build. Nothing consumes that distinction today, because
both collapse into `TargetUnavailable`. It becomes the right answer when something does.

**An attachment is dropped on every failed read, and a failed attach is never cached.** Not only on
`TargetUnavailable`, and not only on a read that proves the process is gone. Any failure at all.

**On Windows, a held attachment pins the pid.** Attaching opens a process handle, and a held handle
keeps the process object alive, so the operating system will not hand that pid to a new process
while gcmon is attached. The pin covers exactly the interval gcmon holds the attachment, which is
exactly the interval gcmon reads through it, so **no successful read on Windows can ever be served
from a recycled pid**. The failing read that observes the death releases the pin, and gcmon reads
that pid no further before dropping its cursors: the epoch advance that follows is bookkeeping
against state gcmon already holds, not another look at the process.

It does not cover the gap between the child listing naming a pid and the first attach to it. That
gap exists on every platform, is not detectable from the reader's side, and is not addressed here.

## Consequences

- **Reconstructing an attachment after a failed read looks wasteful and is deliberate.** It costs
  one attach on a tick that already failed, which is what every tick used to cost. Do not "optimise"
  it into a retry that keeps the old attachment.

  The reason is that an attachment holds a runtime address and debug offsets derived from the
  process that existed when it was made, and revalidates neither. Applied to a recycled pid it
  reads an unrelated process's memory at the old address. Every field gcmon wants is an integer
  copied out of memory, so the result is not a crash and not an error: it is a set of records that
  are structurally valid, pass every filter gcmon has, and reach the trace. A stale cursor makes a
  number wrong; a stale attachment invents the data. That asymmetry is why the two share a prune
  but not a tolerance.

- **The window this closes is every one gcmon can detect, and no more.** A pid recycled between two
  polls with no failing read in between is not detectable from the reader's side and is not
  addressed here; it wants the pid-epoch machinery. Windows is not exposed to it, because of the
  pin.

- **A pid gcmon is attached to cannot be recycled on Windows**, which is a guarantee on one platform
  and not on the others. Anything relying on it must say so, and must not read it as covering a pid
  gcmon has merely been told about.

- **`gcmon.monitor` no longer names any exception type from `_remote_debugging`.** The platform
  vocabulary for an unreadable target, `ESRCH` on Linux and Windows, `ProcessLookupError` by name
  on macOS, and whatever `debug=True` rewrites each into, stops at the reader, which translates it
  into `TargetUnavailable`. Test doubles say "unavailable" instead of impersonating CPython's
  taxonomy.

- **The translation takes `ProcessLookupError` and `PermissionError`, not `OSError`.** Those two
  are the platform's way of saying the process is gone or closed to gcmon. Every other `OSError`
  says the read itself was wrong, `EFAULT` and `EINVAL` out of `process_vm_readv` among them, and
  a wrong read is a gcmon defect.

- **`ValueError` is outside it for the same reason.** macOS raises it for a read whose arguments
  made no sense *while `task_info` reports the task still valid*: a bad address, not a dead
  process. A terminated macOS target raises `ProcessLookupError`, which is translated. So both
  belong on `PollStatus.FAIL` with a traceback, where they were before this change. Widening the
  translation to swallow either would turn a bug into a silent "target unavailable" and burn the
  startup timeout hiding it.

- **The first poll of each process is still attach-sized**, and it is charged to the `Read Time`
  statistic, which is where the time was spent. Operators see one outlier per process and single
  digits thereafter.

## Alternatives considered

- **Default the reader argument, so existing construction sites keep working.** Rejected. A default
  builds a real reader, and a test that forgets to inject one attaches to whatever process happens
  to hold the integer it used as a pid; the monitor tests use small integers as pids throughout.
  A required argument fails loudly on a runner instead of silently reading a stranger.

- **Keep the free function and cache nothing.** The status quo. It is correct, and it makes gcmon's
  own cost proportional to the number of reads rather than to the number of processes, which is the
  cost an operator attached to a production process is least willing to explain.

- **Drop the attachment only when a read proves the process is gone.** Narrower and cheaper, and
  wrong for the reason above: a read can fail without proving anything, and what gcmon must not do
  is carry offsets it can no longer vouch for.

- **Revalidate the attachment on each read**, comparing the target's start time, say. Rejected: it
  reintroduces a per-poll probe of the target, which is the cost this decision exists to remove,
  and it is re-deriving on every tick exactly what attaching once was meant to stop.

- **Let the monitor widen its `except` clause instead of translating in the reader.** Two words
  smaller, and it leaves `gcmon.monitor` owning a platform-specific error vocabulary, which is
  what the seam exists to contain. Returning an empty result instead of raising was also
  rejected: it loses the cause the debug log prints.

## Implementation

- `src/gcmon/events_reader.py` holds `EventsReader`, `RemoteEventsReader` and `TargetUnavailable`,
  and is the only module in the package that imports a stateful handle from `_remote_debugging`.
  `get_child_pids` stays where it is: it is stateless, caches nothing, and answers a question about
  the process tree rather than about a ring.
- `src/gcmon/monitor.py` takes the reader as a required keyword argument, calls it inside the
  `time.monotonic_ns` brackets that feed `Read Time`, and prunes it alongside the cursors.
  [ADR-0015](0015-gc-loss-spans-on-their-own-track.md) fixes the read-start instant as the one that
  closes the previous poll's interval, so that bracket did not move.
- Tests: `tests/test_events_reader.py` for the lifetime against real subprocesses, a second read of
  the same pid costing an order of magnitude less than the first, a failed attach retried rather
  than remembered, and a failed read followed by a fresh attach; `tests/test_monitor.py` for both
  arms of a poll, an unreadable target yielding `INVALID_PROCESS` with **no** warning and an
  unrecognised failure yielding one with a traceback, since a test watching one arm passes with the
  two swapped; `tests/monitoring/test_monitor.py` for the prune, a pid leaving the child listing
  and a pid its policy gives up on each losing their attachment in the pass that drops their
  cursors, and a failed child listing dropping neither.
