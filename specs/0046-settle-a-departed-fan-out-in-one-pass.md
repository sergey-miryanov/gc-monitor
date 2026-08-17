# 0046 — Settle every departed pid in one pass over the running rings

- **Status:** Not started
- **Kind:** bug — performance
- **Effort:** S
- **Origin:** design question raised while landing 0038, 2026-08-17: whether a tick should batch
  the pids it forgets. Batching the *call site* buys nothing; the quadratic is one level down.
- **Respects:** [ADR-0016](../docs/adr/0016-the-ring-is-the-statistics-unit.md) (the ring is what
  settles, and it settles once), [ADR-0017](../docs/adr/0017-monitor-owns-the-pid-lifecycle.md)
  (the monitor prunes per-pid state once per tick, before the polls)

## 1. Problem

An operator monitoring a process tree that fans out wide sees one poll interval stretch when the
fan-out exits. On a tree of 1000 workers with three interpreters each, the tick that notices they
have gone spends tens of milliseconds settling their statistics before it polls anything, against
a default `--rate` of 0.1 s. The processes that *survive* that tick are the ones who pay: their
ring buffers keep filling while gcmon is busy, and a ring that wraps unread becomes a `GC Loss`
span. So a mass child exit can draw loss on its siblings, and the trace gives no hint that the
cause was gcmon rather than the target.

The stretch is measured. The loss it could cause is not — see §7.

## 2. Evidence

`StreamingStats.materialize` finds one pid's rings by scanning every running ring:

```python
# StreamingStats.materialize
for key in [ring for ring in self._running_rings if ring[0] == pid]:
```

`RingKey` is `(pid, iid)`, so `_running_rings` holds one entry per interpreter of every process
still open. `StreamingStats.retain` then calls that once per departed pid:

```python
# StreamingStats.retain
for pid in self._open_pids - set(pids):
    self.materialize(pid)
```

Settling *D* departed pids out of *R* running rings therefore costs *D × R*. When a whole
fan-out leaves at once, *D* and *R* are both the width of the tree, and the cost is quadratic in
it.

Measured on one machine, so the ratios matter and the absolute figures do not travel: a
`retain` that drops every pid takes 0.81 ms at 100 rings, 19.7 ms at 1000, and 55.5 ms at 3000
(1000 pids × 3 interpreters). A single-pass prototype of the same operation took 0.75 ms, 2.3 ms
and 3.5 ms, and left byte-identical state — the same open pids, epochs, running and settled ring
keys, admitted count, and per-ring `declined` flag, metrics presence, `loss` and `cumulative`.

## 3. Scope

**Affected:** every live monitoring run, `gcmon run` and `gcmon monitor` alike, on any
`--format`, whenever more than a few hundred interpreters depart in one tick. The cost is in
`StreamingStats` and reaches the trace only through the delay it adds to a tick.

**Not affected:** a tick where nothing departed, which pays one set difference and never enters
the loop. Small trees: at a hundred rings the whole operation is under a millisecond and the fix
buys nothing. The loss arithmetic itself, which is `RingAccumulator`'s and does not run here.
`EventsMonitor._forget`, the monitor's other caller of `materialize`, whose *D* is a pid that is
missing from the tree but still listed, normally none or one per tick.

**Why the suite did not catch it:** nothing measures `retain`. The benchmarks in
`tests/benchmarks/test_bench_stats.py` cover the ingest hot path — `update` across pids, the
projection, quantiles — and stop at the point where a process exits. A quadratic that only shows
up above a few hundred interpreters is also invisible to correctness tests, which use two or
three pids.

## 4. Proposed change

1. Give `StreamingStats` a way to settle a set of pids with one traversal: walk
   `_running_rings` once, group the keys whose pid is departing, then settle each group. The
   grouping pass is *R*, the settling is *D*, so the whole operation is *R + D*.
2. Keep `materialize(pid)` as the single-pid entry point, since `EventsMonitor._forget` wants
   exactly that and its *D* is one. Express it in terms of the same internal helper the batch
   path uses, so one body does the epoch advance, the `_admitted_rings` decrement and the move
   into `_settled_rings`. Two copies of that sequence is how a settled ring ends up with the
   wrong epoch.
3. Point `retain` at the batch path.

**Settled:** the per-pid API stays. Deleting `materialize(pid)` in favour of a set-only method
would push a one-element set onto `_forget`, which is the hotter of the two callers and the one
that runs while polls are pending.

**Open, to settle when picked up:** whether `_running_rings` should be keyed to make this
lookup structural rather than a scan — a `dict[int, dict[int, RingStats]]`, or a per-pid index
beside it. That removes the traversal instead of amortising it, and it would help
`aggregate`-style walks that filter by pid too, but it changes a structure five other methods
read. Settled by whether 0039 moves that structure anyway, which is why the one-pass fix is
proposed first: it is contained, and it does not prejudge the split.

## 5. Seams and testing decisions

- **Seam:** `tests/benchmarks/test_bench_stats.py`, extended with a departure case. It is the
  seam that can observe a cost, and the file already exists for exactly this kind of claim.
  Correctness rides on `tests/stats/test_stats.py`, at the level the settling behaviour is
  already asserted.
- **New seam needed:** none. A benchmark that settles a wide fan-out is a new *case* in an
  existing suite, and CodSpeed will carry it as its own series.
- **What makes a good test here:** the correctness test has to compare *state*, not timing. The
  hazard in a batch rewrite is a ring settled under the wrong `pid_epoch`, which no
  wall-clock assertion can see and which surfaces later as one process's percentiles appearing
  under its predecessor's heading. Assert the settled keys and each ring's totals, over a shape
  with more than one interpreter per pid and a partial retain, since a whole-tree drop exercises
  neither the grouping nor the survivors.
- **Prior art:** `tests/benchmarks/test_bench_stats.py::test_streaming_stats_update_many_pids`
  for the fan-out setup and the CodSpeed marker; `tests/stats/test_stats.py` for what settling
  is expected to leave behind; ADR-0016 for what a settled ring means.
- **Cases:**
  1. A wide fan-out departing in one `retain` call, as a benchmark, so the quadratic has a
     number attached to it and a regression has somewhere to show up.
  2. A partial retain over pids with several interpreters each leaves exactly the state the
     per-pid path leaves: same open pids, same epochs, same settled keys, same totals.
  3. Settling a pid twice is still a no-op, and a pid absent from `_open_pids` still costs
     nothing — `materialize`'s early return is what makes `retain` cheap on a quiet tick.
  4. A pid whose rings were declined by the `MAX_ACTIVE_RINGS` bound settles with
     `_admitted_rings` unchanged. The decrement is conditional on `metrics`, so a batch path
     that decremented per ring rather than per admitted ring would drift the bound downward and
     silently decline rings that should have been admitted.

## 6. Out of scope

- The `MAX_ACTIVE_RINGS` bound and what happens when a tree exceeds it. This changes how fast
  rings settle, not which ones are admitted.
- `EventsMonitor.tick` batching the pids it forgets. That was the question this spec came from,
  and the answer is no: the call site is already one call per departed pid, and collecting them
  into a list first leaves the same *D × R* while separating the wait policy's verdict from its
  consequence. ADR-0017 records the tick's shape.
- Any change to what a settled ring contains, or when a run reads one. ADR-0016 owns that.
- The other linear scans over `_running_rings` that filter by pid. If the keying question in §4
  is taken up they all improve together; on their own none of them sits in a poll interval.

## 7. Further notes

**The operator consequence is a mechanism, not an observation.** What is measured is the stall.
The step from stall to fabricated `GC Loss` span is arithmetic — a poll interval stretched past
the time a ring takes to wrap loses records, and ADR-0015's loss arithmetic reports exactly that
— but nobody has run the case. The measurement that would settle it: a fan-out wide enough to
cost tens of milliseconds, exiting in one tick, with a surviving sibling collecting fast enough
to wrap its gen-0 ring inside the stall, and a check for a `GC Loss` span on the survivor whose
interval brackets the exit. If that span does not appear, this is a latency cleanup and its Kind
should change to `feature — cleanup`; the fix is worth the same either way at S effort, but the
framing in §1 would be overclaiming.

**Why not measure first and then decide.** The experiment above is harder to build than the fix,
and it needs a tuned collector racing a tuned ring. The one-pass change is contained, its state
equivalence is checkable, and it removes the mechanism whether or not anyone reproduces the
symptom.
