# 0051: Key the running rings by pid

- **Status:** Not started
- **Kind:** feature (efficiency)
- **Effort:** S
- **Origin:** [0039](0039-split-the-record-model-and-stats-by-concern.md) section 4.6, moved out
  2026-08-19; 0046 raised it and left it open ([RETIRED.md](RETIRED.md))
- **Respects:** [ADR-0016](../docs/adr/0016-the-ring-is-the-statistics-unit.md) (the ring is the
  unit statistics are kept in), [ADR-0017](../docs/adr/0017-monitor-owns-the-pid-lifecycle.md)
  (per-pid state has one owner and one prune)

## 1. Problem statement

`StreamingStats._running_rings` is a flat `dict[(pid, iid), RingStats]`, so every question about
one process is a walk over every process's rings. `StreamingStats.low_coverage` asks that question
on the hot path: `EventsMonitor._ingest` calls `_warn_low_coverage(pid)` after every successful
poll of every pid, and `_warn_low_coverage` returns early only once the advisory has fired.

A run that stays above `COVERAGE_ADVISORY` never fires the advisory. It walks every running ring,
once per polled pid, on every tick, until the run ends. The cost grows with the width of the tree
squared, which is the shape 0046 took out of `retain` and left here.

Measured on 3.15.0b3, Windows 11, x86-64, calling `low_coverage` once per pid over a fan-out whose
rings all sit above the advisory, median of 21 ticks:

| rings running | per tick |
| :-- | -----: |
| 30 (10 pids, 3 iids) | 0.027 ms |
| 90 (30 pids, 3 iids) | 0.158 ms |
| 255 (85 pids, 3 iids) | 1.089 ms |

Until 0048 landed an operator could not see this, because the read dominated it: the same
thirty-worker tree spent around 14 ms of every tick attaching, against 0.158 ms scanning.
Attaching once per pid ([ADR-0020](../docs/adr/0020-attach-to-a-process-once.md)) took that out.
The same tick's thirty reads now cost about 0.18 ms, and the scan is the same size as the work it
wraps.

## 2. Solution

Nothing an operator reads changes. The same advisory fires on the same runs, the `--stats` table
holds the same numbers, and the trace is byte-identical.

What changes is where a tick's time goes. Asking about one process costs that process's rings
instead of the whole tree's, so a wide fan-out stops charging its surviving members for the
interval they are measured over.

## 3. User stories

1. As an operator monitoring a process tree, I want the cost of a tick to grow with the number of
   processes and not with its square, so that a wide fan-out does not stretch the interval its
   members are timed against.
2. As an operator whose run is healthy, I want gcmon to stop paying for an advisory that will
   never fire, so that the well-behaved case is not the expensive one.
3. As an operator whose run is not healthy, I want the coverage advisory to fire on exactly the
   runs it fires on today, naming the same interpreter and generation.
4. As someone reading a trace afterwards, I want this change to be invisible in it, so that traces
   from either side of the change are comparable.
5. As a gcmon maintainer, I want a pid's rings reachable without a filter, so that a method that
   forgets the filter cannot mix two processes' rings.

## 4. Implementation decisions

### 4.1 The shape

```python
self._running_rings: dict[int, dict[int, RingStats]] = {}
```

Outer key pid, inner key iid. `low_coverage` and `_find_ring` become lookups; `_open_ring` becomes
two `setdefault` calls; `_all_rings`, `_keyed_rings`, `rings` and `untracked_rings` become nested
loops over the same rings in the same order.

**Rejected: a per-pid index beside the flat map.** It buys the same lookups and adds a second
structure to keep in step with the first, on the paths that open and settle rings. The key carries
it instead.

**Settled: `_settled_rings` stays flat.** It is keyed `(pid, iid, epoch)` and nothing asks it for
one pid's rings; `_find_ring` reaches it by full key. Re-keying it would be layout for its own
sake.

### 4.2 `_settle` loses its `keys` argument

`_settle(pid, keys)` exists because the caller has to find the pid's keys first, and it pops each
of them from the dict they were read out of, so *keys* has to be a list rather than a view over
`_running_rings`. Under the new shape it pops one entry:

```python
def _settle(self, pid: int) -> None:
    ...
    for iid, settled in self._running_rings.pop(pid, {}).items():
```

`materialize` calls `_settle(pid)`. `retain` iterates the departed pids and calls it once each, and
the one-pass grouping 0046 added goes with it: there is nothing left to group.

This is the part worth having. Today a caller can hand `_settle` a partial key list, and the result
is a process whose interpreters settle under two epochs, with its pid epoch advanced once per
group. Nothing in the signature stops it. After the re-key there is no key list to get wrong.

### 4.3 Ordering, which is observable in one place

`low_coverage` keeps the worst ring with a strict `<`, so the first ring examined wins a tie. Iids
within a pid keep their insertion order in the inner dict, so a tie resolves to the same
interpreter as today. Everything else that walks the rings either sorts (`rings`) or reduces
(`untracked_rings`, `pause_totals_by_gen`, `heap_high_water`).

## 5. Seams and testing decisions

- **Seam:** the existing suite. `low_coverage`, `materialize`, `retain`, `rings` and `pause_totals`
  are all public and already covered, so the behaviour this must preserve is asserted from outside
  the structure being changed.
- **New seam needed:** one benchmark, `test_streaming_stats_low_coverage_wide_fan_out` in
  `tests/benchmarks/test_bench_stats.py`, beside the fan-out benchmark 0046 added. The point of
  the change is a cost, and CodSpeed is where this repo keeps costs from coming back.
- **What makes a good test here:** assert on what `low_coverage` answers and on which rings survive
  a settle, never on the shape of `_running_rings`. The six assertions that do read the shape are
  listed below and have to change; that is the price of the change, not evidence against it.
- **Prior art:** `tests/benchmarks/test_bench_stats.py::test_streaming_stats_retain_wide_fan_out`
  for the benchmark, including its non-timing assertion that the work actually happened;
  `tests/stats/test_stats.py::TestAFanOutThatDeparts` for the settling cases.
- **Cases:**
  1. The advisory fires on the same runs and names the same interpreter and generation, including
     the tie in section 4.3.
  2. A pid's interpreters settle under one epoch, which section 4.2 makes structural. The case
     exists as `TestAFanOutThatDeparts::test_a_pid_whose_rings_interleave_settles_in_one_go` and
     should survive the re-key unchanged in intent.
  3. Regression guard: the `--stats` table and the trace for a fixed record sequence are unchanged.

**The six assertions that read the flat shape**, all of which become two-level:

| test | what it asserts |
| :-- | :-- |
| `test_streaming_stats_retain_wide_fan_out` | the fan-out settled, so a run that stopped settling does not read as a win |
| `TestAProcessThatExits::test_retain_settles_the_pids_it_leaves_out` | one pid's ring survives `retain` |
| `TestAProcessThatExits::test_every_interpreter_of_the_pid_settles` | `materialize` empties the running set |
| `TestAnOpenPidHoldsARing::test_each_path_that_opens_a_pid_opens_a_ring` | every open pid holds a ring |
| `TestAFanOutThatDeparts._state` | the tuple the settling-equivalence cases compare |
| `TestAFanOutThatDeparts::test_the_survivors_keep_their_rings` | the survivors keep their rings |

## 6. Out of scope

- **When the coverage advisory fires, and how often.** It fires once per run and stops scanning
  after it does; that is `EventsMonitor._warn_low_coverage`'s behaviour and this changes neither
  the threshold nor the once-per-run rule. Making the advisory cheap is what removes the reason to
  argue about it.
- **`_heap_size`.** Keyed `(pid, epoch)` and read only in aggregate, by `heap_high_water`. No
  caller asks it for one pid.
- **`MAX_ACTIVE_RINGS` and the admission bound.** Untouched: the same rings are admitted, declined
  and counted.
- **Anything about what a ring means or when it settles.** ADR-0016 owns the first and 0046 settled
  the second; this changes how a ring is reached, not what it is.

## 7. Further notes

**Order it after [0039](0039-split-the-record-model-and-stats-by-concern.md)**, which moves
`StreamingStats` into `gcmon/stats/`. Taken before 0039 it edits a module that is about to move;
taken after, it edits the module in its final home. 0046 was ordered before 0039 for the opposite
reason, that 0039 would otherwise move code 0046 was about to rewrite, and that argument does not
carry here: this touches one class, not the split. The other half of the ordering has gone: 0048
landed, and it is what made this cost visible.

**No ADR.** It changes how a ring is reached, not what a settled ring means, which is the reason
0046 wrote none either.

**CHANGELOG.** One line under the standing `### Internal` heading. No user-facing change and no
`Documentation` entry.
