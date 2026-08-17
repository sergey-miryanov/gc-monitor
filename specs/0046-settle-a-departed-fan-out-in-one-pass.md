# 0046 — Settle every departed pid in one pass over the running rings

- **Status:** Not started
- **Kind:** bug — performance
- **Effort:** S
- **Origin:** a design question while landing 0038, 2026-08-17: should a tick batch the pids it
  forgets? Batching the call site buys nothing. The quadratic is one level down.
- **Respects:** [ADR-0016](../docs/adr/0016-the-ring-is-the-statistics-unit.md) (the ring settles,
  and settles once), [ADR-0017](../docs/adr/0017-monitor-owns-the-pid-lifecycle.md) (the monitor
  prunes per-pid state once per tick, before the polls)

## 1. Problem

Monitor a process tree that fans out wide, and one poll interval stretches when the fan-out exits.
On 1000 workers with three interpreters each, the tick that notices they have gone spends tens of
milliseconds settling their statistics before polling anything, against a default `--rate` of
0.1 s. The survivors pay: their ring buffers fill while gcmon is busy, and a ring that wraps
unread becomes a `GC Loss` span. A mass child exit can draw loss on its siblings, and the trace
gives no hint that gcmon caused it rather than the target.

The stretch is measured. The loss is not. See §7.

## 2. Evidence

`StreamingStats.materialize` finds one pid's rings by scanning every running ring:

```python
# StreamingStats.materialize
for key in [ring for ring in self._running_rings if ring[0] == pid]:
```

`RingKey` is `(pid, iid)`, so `_running_rings` holds one entry per interpreter of every open
process. `StreamingStats.retain` calls that once per departed pid:

```python
# StreamingStats.retain
for pid in self._open_pids - set(pids):
    self.materialize(pid)
```

Settling *D* departed pids out of *R* running rings costs *D × R*. When a whole fan-out leaves at
once, *D* and *R* are both the width of the tree.

One machine, so read the ratios and ignore the absolute figures. A `retain` dropping every pid
takes 0.81 ms at 100 rings, 19.7 ms at 1000, and 55.5 ms at 3000 (1000 pids × 3 interpreters). A
single-pass prototype took 0.75 ms, 2.3 ms and 3.5 ms, and left identical state: the same open
pids, epochs, running and settled ring keys, admitted count, and per-ring `declined` flag,
metrics presence, `loss` and `cumulative`.

## 3. Scope

**Affected:** every live monitoring run, `gcmon run` and `gcmon monitor`, on any `--format`,
whenever more than a few hundred interpreters depart in one tick. The cost sits in
`StreamingStats` and reaches the trace through the delay it adds to a tick.

**Not affected:** a tick where nothing departed, which pays one set difference and never enters
the loop. Small trees, where a hundred rings settle in under a millisecond. The loss arithmetic,
which belongs to `RingAccumulator` and does not run here. `EventsMonitor._forget`, the other
caller of `materialize`, whose *D* is a pid missing from the tree but still listed, so none or one
per tick.

**Why the suite did not catch it:** nothing measures `retain`. The benchmarks in
`tests/benchmarks/test_bench_stats.py` cover the ingest hot path and stop where a process exits. A
quadratic that appears above a few hundred interpreters is also invisible to correctness tests,
which use two or three pids.

## 4. Proposed change

1. Give `StreamingStats` a way to settle a set of pids in one traversal: walk `_running_rings`
   once, group the keys whose pid is departing, settle each group. Grouping costs *R* and settling
   costs *D*, so the operation is *R + D*.
2. Keep `materialize(pid)` as the single-pid entry point, since `EventsMonitor._forget` wants
   exactly that. Express it through the same internal helper the batch path uses, so one body does
   the epoch advance, the `_admitted_rings` decrement and the move into `_settled_rings`. Two
   copies of that sequence is how a settled ring ends up under the wrong epoch.
3. Point `retain` at the batch path.

**Settled:** the per-pid API stays. A set-only method would push a one-element set onto `_forget`,
the hotter caller and the one that runs while polls are pending.

**Open, to settle when picked up:** whether to re-key `_running_rings` so the lookup is structural
rather than a scan, as `dict[int, dict[int, RingStats]]` or a per-pid index beside it. That removes
the traversal instead of amortising it, and helps the other walks that filter by pid, but five
other methods read that structure. Settled by whether 0039 moves it anyway, which is why the
one-pass fix comes first: contained, and it prejudges nothing.

## 5. Seams and testing decisions

- **Seam:** `tests/benchmarks/test_bench_stats.py`, extended with a departure case. It is the
  highest seam that can observe a cost, and it already exists for this kind of claim. Correctness
  rides on `tests/stats/test_stats.py`, where settling is asserted today.
- **New seam needed:** none. A benchmark over a wide fan-out is a new case in an existing suite,
  and CodSpeed carries it as its own series.
- **What makes a good test here:** compare state, not timing. The hazard in a batch rewrite is a
  ring settled under the wrong `pid_epoch`, which no wall-clock assertion sees and which surfaces
  later as one process's percentiles under its predecessor's heading. Assert the settled keys and
  each ring's totals, over a shape with several interpreters per pid and a partial retain: a
  whole-tree drop exercises neither the grouping nor the survivors.
- **Prior art:** `tests/benchmarks/test_bench_stats.py::test_streaming_stats_update_many_pids` for
  the fan-out setup and the CodSpeed marker; `tests/stats/test_stats.py` for what settling leaves
  behind; ADR-0016 for what a settled ring means.
- **Cases:**
  1. A wide fan-out departing in one `retain` call, as a benchmark, so the quadratic has a number
     and a regression has somewhere to show up.
  2. A partial retain over pids with several interpreters each leaves what the per-pid path
     leaves: same open pids, epochs, settled keys, totals.
  3. Settling a pid twice stays a no-op, and a pid absent from `_open_pids` still costs nothing.
     `materialize`'s early return is what keeps `retain` cheap on a quiet tick.
  4. A pid whose rings the `MAX_ACTIVE_RINGS` bound declined settles with `_admitted_rings`
     unchanged. The decrement is conditional on `metrics`, so a batch path decrementing per ring
     rather than per admitted ring drifts the bound downward and starts declining rings it should
     admit.

## 6. Out of scope

- The `MAX_ACTIVE_RINGS` bound and what happens when a tree exceeds it. This changes how fast
  rings settle, not which ones are admitted.
- `EventsMonitor.tick` batching the pids it forgets, the question this spec came from. The call
  site is already one call per departed pid, so a list leaves the same *D × R* and separates the
  wait policy's verdict from its consequence. ADR-0017 records the tick's shape.
- What a settled ring contains, or when a run reads one. ADR-0016 owns that.
- The other scans over `_running_rings` that filter by pid. The keying question in §4 fixes them
  together; alone, none of them sits in a poll interval.

## 7. Further notes

**The operator consequence is a mechanism, not an observation.** The stall is measured. Getting
from stall to fabricated `GC Loss` span is arithmetic: a poll interval stretched past the time a
ring takes to wrap loses records, and ADR-0015's arithmetic reports that. Nobody has run the case.

The measurement that would settle it: a fan-out wide enough to cost tens of milliseconds, exiting
in one tick, with a surviving sibling collecting fast enough to wrap its gen-0 ring inside the
stall, then a check for a `GC Loss` span on that survivor across the exit. If no span appears,
this is a latency cleanup and its Kind becomes `feature — cleanup`. The fix is worth the same S
either way; §1 would be overclaiming.

**Why not measure first.** That experiment is harder to build than the fix and needs a tuned
collector racing a tuned ring. The one-pass change is contained, its state equivalence is
checkable, and it removes the mechanism whether or not anyone reproduces the symptom.
