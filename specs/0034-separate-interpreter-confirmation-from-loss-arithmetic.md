# 0034 — Give interpreter confirmation its own seam, and put the mid-write bound back

- **Status:** Not started (unblocked; the mechanism this restores was removed by ADR-0015's redesign)
- **Kind:** feature — enhancement
- **Effort:** S
- **Origin:** grilling session, 2026-08-08
- **Respects:** [ADR-0015](../docs/adr/0015-gc-loss-spans-on-their-own-track.md) (the loss
  arithmetic and what gcmon trusts the target for),
  [ADR-0007](../docs/adr/0007-shared-trace-converter-pipeline.md) (one conversion pipeline)

## 1. Problem statement

Open a trace and a `GC Loss(0)` bar starts earlier than it needs to, covering a stretch
the trace itself shows was not blind. The count on the bar stays exact. The interval is looser
than the evidence supports, which is the only thing a loss span claims.

The redesign dropped the in-flight bound to keep its own change subtractive, and the spans got
wider for it. A window now opens at the newest record the previous poll saw *finish*, so it
reaches back across a collection that poll caught *starting*. CPython serializes collections,
so nothing lost since can have run before that one ended.

The mechanism is also spread out. It lived as `EventsMonitor._in_flight_starts` plus a rescan
of the batch inside `_ingest`, tangled with the cursor bookkeeping and with cleanup duties in
`forget` and `retain`. "What is the latest moment gcmon has evidence about this interpreter" is
a different question from "how many records did this ring lose", and folding the first into the
second made it cheaper to delete than to keep.

## 2. Solution

Loss spans start at the latest moment gcmon can prove the interpreter was still busy or still
observed, rather than at the last collection it watched finish. Bars get shorter, their counts
do not change, and the stretch the bar excludes is one the trace can already be seen to account
for.

Nothing else about a capture changes: the same spans, the same generations, the same counts and
pause sums, drawn tighter.

## 3. User stories

1. As someone reading a trace in the Perfetto UI, I want a loss bar to start no earlier than the
   last thing gcmon observed about that interpreter, so that the bar's width is a claim I can
   check against the row above it.
2. As an operator comparing captures across a change to `--rate`, I want window width to track
   how blind gcmon was, so that a narrower bar means better coverage rather than a
   different code path.
3. As a gcmon maintainer, I want the confirmation bound behind an interface I can test without
   constructing a poll, so that the next change to loss arithmetic does not have to reason about
   mid-write records at the same time.
4. As a gcmon maintainer, I want the reason this bound is sound written down beside it, so that
   nobody rejects it by analogy to the clipping ADR-0015 already abandoned.

## 4. Implementation decisions

**Extract the concern first, restore the behaviour second.** The unit is "the latest evidence
gcmon holds about interpreter *iid*", fed by two kinds of observation and consulted by
`KeyAccumulator._open_run` in place of today's `read_bound` argument. `loss.read_bound_per_interpreter`
is the seed of it; the in-flight dict and the `finished` rescan are the rest, currently in
`EventsMonitor`. Land the extraction with the existing (wider) semantics and no behaviour change,
then restore the mid-write bound as a second commit, so the diff that changes span widths
contains nothing else.

**Why the mid-write bound is sound, and the abandoned clipping is not.** ADR-0015 tried and
rejected *"clipping a window's far end to the poll's earliest observation anywhere in the
interpreter"*, because oldest-first eviction orders a key's lost records against **that key's**
kept records and says nothing about another generation's — a lost gen-0 collection can have run
after an observed gen-2 one. The two arguments are unrelated:

- The rejected clipping is an **eviction-order** argument. It infers when a lost record ran from
  where it sat in a ring it does not share with the record being compared. That inference is
  invalid across keys, in either direction.
- The in-flight bound is a **temporal** argument. A record in progress at poll N's read occupied
  the interpreter at that moment. Collections in an interpreter are serialized, so anything lost
  after that read ran after that collection ended, whatever generation either belongs to. No
  ring, no eviction order, no cross-key inference.

Write this into the extracted unit's docstring. It is the whole reason the mechanism can exist,
and its similarity to a rejected idea is the reason it will be deleted again if it is not stated.

**Both edges of the mid-write record confirm, and they are not redundant.** Its `ts_start`
survives the record never coming back — the slot is often overwritten before the next read, which
is the situation that produced it. Its `ts_stop`, learned when the record returns complete a poll
later, raises the bound further. Take the max of whatever is available.

**Preserve the shared left edge.** The nesting guarantee rests on every window in a poll
starting at the same timestamp, which holds because the bound is computed per interpreter and not
per key. Any extraction must keep it per interpreter. A per-key confirmation bound would
reintroduce crossing spans, which the redesign removed the merge for. This is the constraint
the extraction must not break, and `tests/test_loss.py::TestOneLeftEdgePerPoll` plus
`tests/exporters/test_loss_track_stack.py` are what catch it.

**Keep the discard path.** `LossWindow.is_drawable` drops a window with `ts_stop <= ts_start`
and reports it in the `--stats` footer. That was first written as a target-bug detector, and a
later review found it fires without any target bug: the read bound is a maximum across the
interpreter's rings, and a poll reads those rings over ~0.6 ms while the target collects.
Raising the bound here makes it fire more often for that second reason. The discard stays and
the footer already names no culprit, so nothing needs rewording; expect the count to rise.

## 5. Seams and testing decisions

- **Seam:** the extracted confirmation unit, directly, as a pure function or struct in `loss.py`
  at the level `KeyAccumulator` and `stack_order` are tested at. It is the highest seam
  that can observe the bound itself; span width at the exporter level observes it only through
  the arithmetic and cannot distinguish a wrong bound from a wrong window.
- **New seam needed:** yes, and creating it is half the point of the spec. Today the behaviour is
  reachable only by driving `EventsMonitor._ingest` with a hand-built batch through the
  `Ingested` helper in `tests/test_loss.py`, which mirrors `_ingest`'s body and so has to be kept
  in step with it by hand.
- **What makes a good test here:** feed observations and assert the bound, with no polls and no
  cursors in the test. Then one span-level test that a window opens at the mid-write record's
  `ts_stop` rather than at the previous generation's — the behaviour an operator sees.
- **Prior art:** `tests/test_loss.py::TestARecordReadIncompleteThenComplete`, deleted by the
  redesign. Restore its eight cases against the new seam rather than rewriting them; they
  already state the right things, including that the start alone confirms when the record never
  returns.
- **Cases:**
  1. A record caught mid-write raises the interpreter's bound to its `ts_start` immediately, and
     to its `ts_stop` once it returns complete.
  2. A window on another generation opens at that bound, not at the last collection observed
     finishing.
  3. The bound stays per interpreter: all windows a poll opens for one interpreter still share a
     left edge, and the spans still nest.
  4. Regression guard: counts, pause sums, `Cov` and `F` are unchanged. This spec moves window
     starts and nothing else.

## 6. Out of scope

- **Any other tightening of a window.** In particular the far-end clipping ADR-0015 abandoned, and
  the per-window capacity floor it also rejected. Both stay rejected; §4 explains why this one is
  a different argument rather than a re-run of either.
- **Drawing the mid-write record itself.** `_is_complete` filters it and the next poll emits it
  where it ran. That is correct and untouched.
- **Detecting the store-reordering hazard.** The discard counter is a fingerprint of one shape of
  it, not a detector, and this spec makes it a weaker one. ADR-0015 puts the fix upstream.

## 7. Further notes

Sequencing against spec 0033: independent. This one moves window edges, 0033 adds a lane; neither
reads the other's output. Neither blocks the other, and both are unblocked now.
