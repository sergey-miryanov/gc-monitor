# 0048 — Attach to each pid once, instead of on every poll

- **Status:** Not started
- **Kind:** feature — efficiency
- **Effort:** M
- **Origin:** a design session on `_remote_debugging.GCMonitor`, 2026-08-17
- **Respects:** [ADR-0017](../docs/adr/0017-monitor-owns-the-pid-lifecycle.md) (per-pid state has
  one owner and one prune), [ADR-0015](../docs/adr/0015-gc-loss-spans-on-their-own-track.md) (the
  instant a read begins closes the previous poll's interval),
  [ADR-0011](../docs/adr/0011-process-lifetime-and-ordering.md) (a successful read is the evidence
  a process existed)

## 1. Problem statement

Every poll gcmon makes opens the target process, locates its `PyRuntime`, reads and validates its
debug offsets, reads the rings, and throws all of that away again. Only the fourth step is the
point. On this machine the discarded work is **470 µs of the 473 µs** a poll costs, per process,
per tick — and gcmon polls the target plus every descendant, so a tree of thirty workers spends
around 14 ms of every 100 ms tick re-deriving what it derived on the tick before.

An operator sees it three ways. The `Read Time` row of the `--stats` table reports it directly.
The advice built on that row in `docs/statistics.md` — sanity-check `--rate` by comparing it to
the mean `Read Time` — is really telling them how much of each tick is attach. And the machine
running gcmon carries the cost against the machine's other work, which for an operator attached to
a production process is the cost they were most careful about.

It also puts a floor under `--rate` that has nothing to do with the target.
[0024](0024-cpython-report-remote-readable-gc-stats.md) §3.1 rests its headline on that floor —
*"The read cost alone bounds the achievable rate below the collection rate, so the loss is
structural rather than a tuning problem"* — measuring ~583 µs median against ~1.15 ms between
gen-0 collections. Two thirds of that floor is attach, and none of it is load-bearing.

## 2. Solution

gcmon attaches to a process once and reads it many times. Nothing about what gcmon reports
changes: the same records, the same rings, the same trace, the same counts and percentiles.

What an operator sees change is `Read Time`, from hundreds of microseconds to single digits, and
the character of the row with it — it stops being "how long attaching takes" and becomes "how long
reading takes", with one attach-sized outlier per process. Monitoring a wide tree stops costing a
double-digit share of every tick. The floor `--rate` could never go below moves down by roughly
two orders of magnitude, and what remains bounding coverage is the ring buffer alone, which is the
honest answer.

The one behaviour an operator could notice and should not: nothing about starting up, waiting for
a target, or watching one exit may change. A target that has not started yet must still be waited
for; a target that exits must still end the run quietly. §4 treats that as the risk it is.

## 3. User stories

1. As an operator attaching to a production process, I want gcmon's own CPU cost to be a small
   multiple of the reads it makes, so that monitoring is not itself the load I have to explain.
2. As an operator monitoring a process tree, I want the cost of a tick to grow with the number of
   reads and not with the number of attaches, so that a wide fan-out does not stretch the interval
   its survivors are measured over.
3. As a developer profiling a script under `gcmon run`, I want `Read Time` to tell me what reading
   costs, so that the `--rate` advice in `docs/statistics.md` calibrates against something real.
4. As an operator whose target has not started yet, I want gcmon to keep waiting exactly as long
   as it does today, so that `--startup-timeout` still means what it meant.
5. As an operator whose target has just exited, I want the run to end as quietly as it does today,
   with no traceback on stderr, so that a clean exit still looks clean.
6. As someone reading a trace afterwards, I want this change to be invisible in it, so that traces
   from either side of the change are comparable.
7. As a gcmon maintainer, I want the knowledge of how to reach `_remote_debugging` in one place,
   so that the next change to that API touches one module and one set of fakes.
8. As a CI job, I want a test that forgets to inject a reader to fail loudly rather than attach to
   whatever process happens to hold that pid on the runner.

## 4. Implementation decisions

### 4.1 What the API offers

CPython gained `_remote_debugging.GCMonitor` in 3.15.0b1; gcmon requires 3.15, so there is no
version gate to write. Pinned to `tags/v3.15.0b4:0a6fa62`:

```
GCMonitor(pid, *, debug=False)                  # module.c, _remote_debugging_GCMonitor___init___impl
GCMonitor.get_gc_stats(all_interpreters=False)  # module.c, _remote_debugging_GCMonitor_get_gc_stats_impl
```

`__init__` calls `init_runtime_offsets` and holds the result; the free function calls
`init_runtime_offsets`, reads, then `cleanup_runtime_offsets`, on every call. Both reach the same
`get_gc_stats` in `Modules/_remote_debugging/gc_stats.c`.

**The read is equivalent, not merely similar.** The whole GC-stats path — `iterate_interpreters`
and the `struct gc_stats` copy — uses `_Py_RemoteDebug_ReadRemoteMemory`, which is uncached. The
page cache in `proc_handle_t` is only reached through `_Py_RemoteDebug_PagedReadRemoteMemory`,
which nothing on this path calls. `iterate_interpreters` re-walks the interpreter list from
`runtime_start_address` on every call, so an interpreter created after the attach is still found.
Measured on the same target, both forms returned the same 17 rows.

Measured on 3.15.0b4, Windows 11, x86-64, against a target allocating in a loop, 200 calls each:

| | median | p95 | max |
| :-- | -----: | --: | --: |
| `get_gc_stats(pid, all_interpreters=True)` | 473 µs | 562 µs | 2780 µs |
| `GCMonitor.get_gc_stats(all_interpreters=True)` | 6.1 µs | 6.3 µs | 25.3 µs |
| `GCMonitor(pid)` | 470 µs | — | — |

### 4.2 A reader seam, because the pid moved

The pid moves from an argument to an identity, so something must hold one attachment per pid and
prune it. That is per-pid state, which ADR-0017 says has one owner and one prune — and
`StreamingStats` is the precedent for holding it in a collaborator that `EventsMonitor` prunes
from its single pass, through `retain` and `materialize`.

A new module `gcmon.events_reader` carries a Protocol and one implementation:

```python
class EventsReader(Protocol):
    def read(self, pid: int) -> Sequence[TGCStatsInfo]: ...
    def retain(self, pids: Set[int]) -> None: ...
    def forget(self, pid: int) -> None: ...
```

`RemoteEventsReader` is the one backed by `GCMonitor`, following the `WaitPolicy`/`NoWaitPolicy`
and `TargetProcess`/`ExternalProcess` convention. `EventsMonitor.poll` calls `read`;
`EventsMonitor._retain` and `EventsMonitor._forget` call the other two beside their existing
`StreamingStats` calls.

`all_interpreters` is not a parameter. gcmon passes `True` unconditionally, and per-iid reporting
is structural to what gcmon is.

**Settled: `reader` is a required keyword argument on `EventsMonitor`.** A default would build a
real reader, and a test that forgot to inject would attach to whatever process holds the integer
it used as a pid — `tests/monitoring/test_monitor.py` polls 12345 and 999 throughout. The
existing assertion that `wait_policy_factory` is required grows a second missing argument.

**Settled: the name is `EventsReader`, not `RecordsReader`.** CONVENTIONS §4 and `CONTEXT.md`
both reserve **record** for what is read and **event** for what is written, and by that rule this
is misnamed. It is named for its siblings instead — `EventsMonitor`, `EventsExporter` — because
renaming one member of a family makes the family less coherent, not more. §6 carries the rename.

**Settled: `get_child_pids` stays a module-level import in `gcmon.monitor`.** It is not on
`GCMonitor`, it caches nothing, and it answers a question about the process tree rather than about
a ring. The line the seam draws is statefulness, not provenance.

### 4.3 The exception taxonomy, which changed

`GCMonitor.get_gc_stats` reports a dead target as `ProcessLookupError`, where the free function
reported `RuntimeError`. This is deliberate upstream — `Python/remote_debug.h` sets `ESRCH`
explicitly on Windows *"so we can tell our caller that the process is dead and not just that the
read failed"*, Linux surfaces `ESRCH` from `process_vm_readv`, macOS raises
`PyExc_ProcessLookupError` by name.

`EventsMonitor.poll` today maps `RuntimeError` and `PermissionError` to
`PollStatus.INVALID_PROCESS` at debug level, and everything else to `PollStatus.FAIL` with
`logger.warning(..., exc_info=...)`. Dropped in unchanged, **every normal target exit prints a
traceback**. Worse, on Linux a not-yet-existing pid fails through `process_vm_readv` rather than
`OpenProcess`, so the startup path takes the `FAIL` arm, and `StartupTimeoutPolicy.wait` returns
`False` for `FAIL` unconditionally — the startup wait is defeated, on one platform, silently.

`RemoteEventsReader.read` therefore translates. `(RuntimeError, OSError)` becomes a gcmon-owned
`TargetUnavailable`, chained from the cause; anything else propagates. `poll` catches
`TargetUnavailable` → `INVALID_PROCESS`, `Exception` → `FAIL`, and names no exception type from
`_remote_debugging`.

**Settled: preserve today's classification exactly.** `PermissionError` is an `OSError`, so it
stays where it is; `ProcessLookupError` is an `OSError`, so it moves to where it belongs; macOS's
`ValueError` arm stays on `FAIL` as today. Widening `poll`'s `except` to `(RuntimeError, OSError)`
was rejected: two words smaller, but it leaves `gcmon.monitor` owning a platform-specific
vocabulary — including that macOS raises `ValueError` for a bad read — which is what the seam
exists to contain, and it makes test fakes impersonate CPython's error taxonomy instead of saying
"unavailable". Returning `Sequence | None` was rejected for losing the cause the debug log prints.

### 4.4 `debug=True`, which is not a verbosity flag

`_remote_debugging.h`:

```c
#define set_exception_cause(unwinder, exc_type, message)  \
    do { ... if (unwinder->debug) { _set_debug_exception_cause(exc_type, message); } } while (0)
```

`_set_debug_exception_cause` **replaces** the live exception with one of `exc_type` and demotes
the original to `__cause__`. The flag decides which type the caller catches. Same dead target:

```
debug=False -> ProcessLookupError: [Errno 3] No such process
debug=True  -> RuntimeError: Failed to read interpreter state address
                 └─ __cause__: ProcessLookupError: [Errno 3] No such process
```

The free function hardcodes `debug=1`, which is why it reported `RuntimeError`.

**Settled: pass `debug=True` explicitly, with a comment saying what it does.** It is the setting
that makes this a swap rather than a change, and it keeps the descriptive message gcmon already
logs. `debug=False` gives strictly better signal — it is the only way to tell target death from a
`gc_stats` size mismatch — but nothing consumes that distinction today, since §4.3 collapses both
into `TargetUnavailable`. It becomes the right answer alongside the §6 mismatch spec, not before.

The macro only fires on an error path, so the happy path costs nothing either way.

### 4.5 The attachment's lifetime

Attach lazily inside `read`, on first use of a pid. **Never cache a failed attach** — a target
that has not started yet must be retried on the next tick, which is the whole point of
`StartupTimeoutPolicy`. **Drop the attachment on any failed read**, not only on
`TargetUnavailable`.

Dropping on failure is not defensive tidiness. `GCMonitor` holds `runtime_start_address` and
`debug_offsets` derived from the process that existed when it attached, and revalidates neither.
An attachment applied to a recycled pid reads another process's memory at the old runtime address,
and since every field is an integer copied out of memory, the result parses as plausible records.
Dropping on failure closes every window gcmon can detect, and reconstruction costs 470 µs on a
tick that already failed — today's steady-state cost, on the rarest path.

It also produces the right ordering. The tick that sees a target die drops the attachment, and the
policy on that same tick returns `keep_waiting=False`, so `_forget` drops the cursors and settles
the statistics. Reader and cursors die together, which is ADR-0017's rule holding for the new
state. On Windows there is a further consequence worth stating: `_Py_RemoteDebug_InitProcHandle`
calls `OpenProcess`, and a held handle keeps the process object alive, so **the pid cannot be
recycled while gcmon is attached**. Under this lifetime the pin lasts exactly until gcmon has
recorded the death and advanced the pid epoch. Linux holds no handle and gets no such pin; §6
carries what follows from that.

Verified: with a live attachment held, constructing a second `GCMonitor` on the killed pid got
past `OpenProcess` and failed at `"Failed to find the PyRuntime section"`, where a pid that never
existed fails at `"Failed to initialize Windows process handle"`.

### 4.6 The clock stays in `poll`

`EventsMonitor.poll` keeps reading `time.monotonic_ns` either side of the read, keeps handing
`StreamingStats.record_read_time` the difference, and keeps passing the start instant to `_ingest`
— ADR-0015 makes that instant the one that closes the previous poll's interval, so it cannot move.

The first read of a pid therefore charges its attach to `Read Time`. **Settled: that is correct.**
The time was spent reading. A separate attach statistic was rejected: it needs a second row in the
`--stats` table, and this change adds no output.

Rename the locals in `EventsMonitor.poll` and `EventsMonitor._ingest` while both are being
rewritten — they are called `events` and hold `TGCStatsInfo`, which CONVENTIONS §4 calls records.
Two identifiers, in the two functions this spec already touches.

## 5. Seams and testing decisions

- **Seam:** the injected `EventsReader`. It is the highest seam that can observe this, and it
  replaces `patch("gcmon.monitor.get_gc_stats", ...)` at 16 sites across 6 files with an argument.
  End-to-end coverage rides on `tests/monitoring/test_run_cmd.py::TestRunCommandScriptMode`, which
  already drives the real CLI against real scripts and so exercises the real `_remote_debugging`
  boundary — a wrong method or keyword name fails there.
- **New seam needed:** a `FakeEventsReader` in `tests/helpers.py`, wrapping a
  `Callable[[int], Sequence[TGCStatsInfo]]` and recording its `retain`/`forget` calls. The
  existing `side_effect=one_read`, `side_effect=[poll_0, poll_1]` and
  `side_effect=lambda pid, **_: per_pid[pid]` callables all port over, because `read` still takes
  the pid. The four `patch("gcmon.monitor.get_child_pids", ...)` sites are untouched.
- **What makes a good test here:** assert on `PollStatus` and on what reached the exporter and the
  stats, never on whether the reader was called. The hazards this change introduces are all
  invisible to a "did it read?" assertion: a warning where a debug line belongs, a startup wait
  that stops waiting, an attachment outliving its process.
- **Prior art:** `tests/monitoring/test_monitor.py` for poll-level assertions and the required-
  argument check; `tests/monitoring/test_monitored_run_trace.py` for the scripted-clock discipline,
  whose per-tick budget of one loop read plus two reads per polled pid is unchanged;
  `tests/test_monitor.py::TestGCMonitorReadTime` for the `Read Time` assertions;
  `tests/conftest.py` for the two monitor fixtures every construction site routes through.
- **Cases:**
  1. A target that exits mid-run yields `INVALID_PROCESS` and **no** `logger.warning`, asserted
     through `caplog`. This is the regression the change most invites, and today nothing would
     catch it: the end-to-end tests exercise real target death but assert only trace validity.
  2. A pid whose first attach fails is retried on the next tick and succeeds, so a target that
     starts late is still waited for. Guards §4.5's "never cache a failed attach" and the Linux
     startup path in §4.3.
  3. A failed read drops the attachment: the next `read` of that pid attaches again.
  4. A pid leaving the child listing, and a pid a policy gives up on, each drop their attachment in
     the same pass that drops their cursors. ADR-0017's rule, asserted for the new state.
  5. `Read Time` still records once per successful poll, not at all on a failed one, and the
     existing sub-microsecond and accumulation cases still hold — the arithmetic is untouched.
  6. `EventsMonitor` cannot be built without a reader.
  7. Regression guard: the trace produced for a fixed record sequence is unchanged. Records,
     ordering, loss windows and statistics are all outside what this touches, and
     `tests/test_loss_replay.py` and `tests/exporters/` already assert them through the monitor.

## 6. Out of scope

- **Changing `--rate`.** This removes a floor; it does not follow it down. The gen-0 ring holds
  11 slots against ~87 collections per 100 ms (0024 §3.1), so a 10× faster poll may buy real
  coverage and a 100× one mostly buys CPU. That is a measurement nobody has taken, and it cannot
  be taken until this lands. Its own spec, and the payoff this one enables.
- **A version mismatch masquerading as "target not started yet."** §4.3 keeps `RuntimeError` →
  unavailable, which swallows `gc_stats.c`'s *"Remote gc_stats size does not match local size"* —
  gcmon built against a different CPython than its target — and burns the whole startup timeout in
  silence. Pre-existing, operator-facing, and its fix wants `debug=False` and a classification of
  its own, so it must argue against ADR-0019 rather than preempt it.
- **A pid recycled between two ticks with no failing read in between.** §4.5 closes every window
  gcmon can detect; this one it cannot. Linux only — Windows cannot recycle a pinned pid. It is
  pre-existing for cursors and ADR-0017 was written about it; what this change alters is the
  consequence, from a wrong number to records fabricated out of another process's memory that pass
  every filter gcmon has. It wants the pid-epoch machinery, not a start-time check bolted onto the
  reader. **File this one; §7 says when.**
- **Renaming the `Events*` family.** `EventsMonitor` reads records and writes events, and the
  family is named for the second. Worth settling as a set, in one pass, the way 0042 treats the
  process-session seam — not by renaming one member here.
- **A `Poll` entry in `CONTEXT.md`.** The word does load-bearing work in the **Loss window** and
  **Exact** entries and is nowhere defined. Pre-existing, and the entry has to agree with **Loss
  window**, **Observed span** and **Sampled** at once, which is a glossary change standing on its
  own merits rather than one smuggled in under a performance change. **Attach** is added by this
  spec because this spec is what resolves it.
- **`get_child_pids`.** Stateless, uncached, and about the process tree. §4.2 gives the reason.
- **Anything about what a record means, how loss is computed, or what reaches the trace.**
  ADR-0015 and ADR-0016 own those and this contradicts neither.

## 7. Further notes

**How to pick this up.** Two artifacts before any code:

1. **Write ADR-0019**, and let it own the three decisions a later reader would otherwise
   "clean up" and thereby reintroduce: that `debug=True` selects an exception type rather than a
   log level (§4.4); that an attachment is dropped on every failed read, and why a stale
   `debug_offsets` is worse than a stale cursor (§4.5); and that on Windows a held handle pins the
   pid until gcmon lets go, which orders the release after the pid epoch advances. It extends
   ADR-0017's "one owner, one prune" to the new state and contradicts nothing.
2. **Break the work into tickets** under `.scratch/0048-attach-once-per-pid/issues/`, in the
   `NN-slug.md` shape with *What to build*, *Blocked by*, *Status* and an acceptance checklist.
   The natural cut: the reader module and its exception translation; the `EventsMonitor` wiring and
   the required argument; the test double and the 16 conversions; the new cases in §5; the
   `GCMonitor` entry in `stubs/_remote_debugging.pyi`; and a closeout ticket for the documentation
   and the CHANGELOG.

**Documentation this makes wrong**, for the closeout ticket. `docs/monitoring.md` states that read
time puts a floor under the interval, that the real interval is `--rate` plus those reads, and that
a shorter `--rate` narrows the gap without closing it. `docs/statistics.md` tells an operator to
sanity-check `--rate` by comparing it to the mean `Read Time`. Correct them to be true at 6 µs;
leave any *change* to `--rate` guidance to the spec that measures it.

**CHANGELOG.** One line, under a new `### Internal` heading in `## WIP`, at the level of
"Stability, correctness and performance improvements". No implementation detail, and no
`Documentation` entry — that section is for new user-facing documentation files, and this adds
none.

**Spec 0024 needs an edit before it is filed**, not a new spec. Its §2 environment is 3.15.0b3 and
its §3.1 headline argues that read cost bounds the achievable poll rate, citing ~583 µs. Two
thirds of that figure is attach, and this spec removes it. The ring-size finding survives intact
and is the stronger half; the read-cost sentence does not. Filing 0024 with that sentence in it
invites the reply that the reporter did not know about `GCMonitor`.

**File the recycled-pid spec when ADR-0019 exists**, so it can cite §4.5's lifetime as the thing it
extends rather than restate it. It is the only §6 item that stands entirely on today's code, and
the only one whose consequence this change makes worse.
