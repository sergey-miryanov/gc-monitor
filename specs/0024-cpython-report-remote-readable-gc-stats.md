# 0024 — File an upstream report on the remote-readable GC stats ring

- **Status:** Not started — material for an upstream report, to be filed by the owner
- **Kind:** report — upstream (neither template fits; this produces an issue, not a change)
- **Effort:** S — no gcmon code changes
- **Origin:** built while implementing GC event loss detection, now [ADR-0015](../docs/adr/0015-gc-loss-spans-on-their-own-track.md)
- **Respects:** [ADR-0015](../docs/adr/0015-gc-loss-spans-on-their-own-track.md) (what gcmon reconstructs from an incomplete sample)

## 1. Summary

`_remote_debugging.get_gc_stats` exposes a per-interpreter, per-generation ring of GC records that
an out-of-process profiler can sample without touching the target. gcmon is built on it. Four
things about the current design limit what a remote reader can do with it, three of which cost
almost nothing to fix.

The headline number: in a target allocating in a loop, **87% of gen-0 collections are unobservable**
regardless of how fast the reader polls, because the ring is smaller than the number of collections
that occur between two reads.

Nothing here is a bug report against a promise CPython made. The API is new and works. This is what
a first serious consumer found while building on it.

## 2. Environment

- CPython 3.15.0b3 (`tags/v3.15.0b3-dirty:cf16a33fad1`), Windows 11, x86-64, default build.
- Consumer: gcmon, an out-of-process GC monitor polling `get_gc_stats(pid, all_interpreters=True)`
  on a timer.
- Sources referenced below are from the 3.15 branch at that tag. Line numbers are given because
  they are the report's evidence and the reader is expected to check them against a pinned tree,
  not against a moving branch:
  - `Include/internal/pycore_interp_structs.h:181-213`
  - `Python/gc.c:1367-1418` (`gc_get_stats`, `gc_get_prev_stats`, `add_stats`), `Python/gc.c:1593`
  - `Modules/_remote_debugging/gc_stats.c:29-121`

## 3. Findings

### 3.1 The ring is far smaller than the collection rate, and one slot under free-threading

```c
#ifdef Py_GIL_DISABLED
#define GC_YOUNG_STATS_SIZE 1
#define GC_OLD_STATS_SIZE 1
#else
#define GC_YOUNG_STATS_SIZE 11
#define GC_OLD_STATS_SIZE 3
#endif
```

Measured over a single 100 ms poll interval against an allocating target:

| gen | collections in the interval | ring slots | observable | lost |
| :-- | --------------------------: | ---------: | ---------: | ---: |
| 0   |                          87 |         11 |         11 |   76 |
| 1   |                           8 |          3 |          3 |    5 |

Polling faster does not close the gap. `get_gc_stats` costs ~583 µs median, ~1 ms p95 and 8.8 ms
max per process on this machine, against ~1.15 ms between gen-0 collections in the same workload.
The read cost alone bounds the achievable rate below the collection rate, so the loss is
structural rather than a tuning problem.

Sizing the ring to survive one interval at 10 Hz for this workload needs ~87 young slots.
`struct gc_generation_stats` is 64 bytes (eight 8-byte fields), so the whole `struct gc_stats` is
about 1.1 KB today and would be about 5.8 KB at 87 young slots — per interpreter, once.

One detail for whoever changes it: `index` is an `int8_t` in both buffer structs, so any size above
128 needs a wider field.

Under `Py_GIL_DISABLED` both sizes drop to 1. A reader then sees at most one record per generation
per poll no matter how often it looks, which makes remote GC sampling on free-threaded builds
close to useless. If the intent is to avoid per-thread cost, note that the buffer is per
interpreter, not per thread.

**Suggested fix.** Raise the sizes, and ideally make them settable at build time. Even 64/16 would
change the character of the data. Removing the free-threaded special case matters most.

### 3.2 The write cursor is read from the target and then discarded

`gc_stats.c:106-112` copies the entire `struct gc_stats` — including `young.index` and
`old[].index` — into a local snapshot. `read_gc_stats` (lines 29-65) then walks `items` and builds
one struct sequence per slot, and never touches `index`.

So the field a reader would want is already in the module's hands and is dropped before it reaches
Python. A consumer has to infer the write position by sorting on `collections`, which works but
means the returned list is in raw slot order — rotated around a write position the caller cannot
see, with the three generations concatenated. Every consumer will re-derive the same thing.

**Suggested fix.** Add `index` to the returned structure, or return the records rotated into
chronological order. The first is a two-line change.

### 3.3 The record publish is unsynchronized

```c
    memcpy(cur_stats, prev_stats, sizeof(struct gc_generation_stats));

    cur_stats->ts_start = stats->ts_start;
    cur_stats->collections += 1;
    ...
    /* Publish ts_stop last so remote readers do not select a partially
       updated stats record as the latest collection. */
    cur_stats->ts_stop = stats->ts_stop;
```

The comment states a contract with remote readers, and gcmon relies on it: a slot whose `ts_start`
is not less than its `ts_stop` is mid-write and is skipped. That is the intended protocol and it
works.

The stores implementing it are plain, non-atomic and unordered. Nothing prevents a compiler from
sinking the `ts_start` store past the two that follow, and on a weakly-ordered target such as
AArch64 store-store order is not architecturally guaranteed to any other observer — including the
kernel performing the cross-process read on the profiler's behalf.

A reader landing inside a reordered window can obtain a record assembled from two collections: the
previous collection's `ts_start` against this collection's `ts_stop`, under a fresh `collections`.
That combination satisfies `ts_start < ts_stop`, so it passes the documented validity test and is
indistinguishable from a genuine record. The consequence is a pause measurement too long by one
inter-collection interval, silently.

The window is a handful of instructions and we have not observed it. It is raised because the
contract is load-bearing for every remote reader and currently rests on optimizer and hardware
behaviour rather than on anything enforced.

**Suggested fix.** A release store on `ts_stop` and an acquire on the reader side, or — better,
since it also covers 3.4 — a seqlock: an even/odd generation counter bumped before and after the
record update, which lets a reader detect that the slot changed underneath it and retry.

### 3.4 One collection is briefly visible in two slots

`gc_get_stats` advances `buffer->index` before the write, and `add_stats` then memcpy's the
previous record into the new slot before overwriting any field. Between the memcpy at
`Python/gc.c:1405` and the `ts_start` store at 1407, two slots hold byte-identical records with
the same `collections`.

A reader that identifies records by `collections` must therefore deduplicate, or it double-counts
one collection. This is benign once known — the twins are identical, so either can be kept — but
it is not documented anywhere, and a consumer that keys on the slot index instead of on
`collections` gets it wrong.

**Suggested fix.** Document it, or eliminate it with the seqlock in 3.3. Filling the new slot in a
scratch record and publishing it with a single store would also work.

## 4. What gcmon does today

For context on which of these are already worked around and which are not. The handling in rows 1,
2 and 4 is the subject of [ADR-0015](../docs/adr/0015-gc-loss-spans-on-their-own-track.md) and
lives in `gcmon.loss` and `EventsMonitor._ingest`.

| Finding | gcmon's handling |
| :------ | :--------------- |
| 3.1 ring size | Reconstructs the exact count and pause sum from `Δcollections` and `Δduration`, and draws the unobserved intervals as explicit gaps. Recovers the aggregates; the individual records stay lost. |
| 3.2 write cursor | Sorts by `collections` per `(iid, gen)`. Works, costs a sort per poll. |
| 3.3 publish ordering | Not handled. No sound client-side check exists — each reordering leaves a different fingerprint and any heuristic risks discarding real records. |
| 3.4 duplicate slot | Deduplicates on `collections`. |

The reconstruction in the first row is only possible because `collections` and `duration` are both
cumulative and monotonic. That is a genuinely good property of this API and worth preserving in any
redesign — it is what lets a reader recover exact totals from an incomplete sample.

## 5. Suggested order of value

1. **3.1** — raise the ring sizes, especially the free-threaded case. Largest effect on what is
   measurable, and it is a constant.
2. **3.3** — synchronize the publish. Correctness of a contract that is already documented.
3. **3.2** — expose `index`. Two lines, saves every consumer the same inference.
4. **3.4** — document, or subsume into the seqlock from 3.3.

## 6. Out of scope for the report

- Anything about gcmon's own design. The report should stand on the C API alone.
- Requesting a callback or push interface. The polling model is the right one for a monitor that
  must not perturb the target.
- Sub-phase timestamps. The extended fields are consumed and are not part of this.

## 7. Further notes

**Lifecycle.** This spec is done when the issue is filed: replace the file with nothing and record
the issue URL in [ADR-0015](../docs/adr/0015-gc-loss-spans-on-their-own-track.md), which is where
a future maintainer will look when the ring sizes change upstream. Any measurement here that gets
re-run before filing should be re-run on the tag in §2, or the tag updated with it.

**A finding to re-check before filing:** whether 3.1's ring sizes still hold on the current 3.15
branch. They are constants and constants get tuned; a report quoting a stale value is easy to
dismiss.
