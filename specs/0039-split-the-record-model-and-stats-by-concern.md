# 0039: Split the record model and the stats module by concern

- **Status:** Not started
- **Kind:** feature (cleanup)
- **Effort:** S
- **Origin:** code structure review of `src/gcmon`, 2026-08-15
- **Respects:** [ADR-0009](../docs/adr/0009-nanoseconds-canonical-time-unit.md) (names the
  module holding the ns→µs conversion; **amended, not contradicted**),
  [ADR-0015](../docs/adr/0015-gc-loss-spans-on-their-own-track.md) (names the modules holding
  the loss record and the gap accounting; likewise)

## 1. Problem statement

Two modules have accumulated three jobs each, and the cost shows up as import fan-out. `data`
holds the record structs, the JSONL decoder, the unit conversions and the Perfetto arg text,
so the Perfetto encoder imports it to get `ts_to_us`, the control server imports it to build an
instant message, and `stats` imports it to convert seconds to nanoseconds. Three modules with
nothing in common depend on one module for three unrelated reasons, and each of them drags the
whole record model in behind it.

`stats` is the same shape at larger scale: a percentile accumulator that has nothing to do with
GC, nine metric adapters that are a name and a two-field getter apiece, and the streaming
aggregation with its loss and lifetime bookkeeping. The test package already knows this:
`tests/stats/` is split into `test_stats.py`, `test_metrics.py`, `test_streaming_stats.py` and
`test_stats_output.py`, four files along a seam the source does not have.

Nothing an operator sees is wrong. The cost is that every one of these modules is on the import
path of things that need a tenth of it, and a reader looking for the percentile logic opens a
file that starts with GC field guards.

## 2. Solution

No behaviour changes at all; this is a move. What changes is that a module's name predicts its
contents, an importer takes on the dependency it actually wants, and the source layout matches
the test layout that already exists.

## 3. User stories

1. As a maintainer looking for the percentile accumulator, I want it in a file named for it, so
   that I do not read past two hundred lines of GC field handling to reach it.
2. As a maintainer of the Perfetto encoder, I want to import a unit conversion without pulling
   in the record model, so that the dependency graph says what the code actually needs.
3. As a maintainer of the control plane, I want to build an instant message without importing
   the statistics vocabulary, so that a change to the record model does not touch the control
   server's imports.
4. As a maintainer adding a test, I want the source file layout to match `tests/stats/`, so
   that I know which file a new test belongs beside.
5. As someone reading gcmon for the first time, I want the record model in one place and the
   presentation strings somewhere else, so that "what is a record" is answerable without
   reading the Perfetto arg formatting.
6. As a maintainer, I want the ADRs that anchor on these module paths updated in the same
   change, so that a record does not point at a file that no longer exists.

## 4. Implementation decisions

**4.1: `data` splits three ways.**

| New home | Contents | Imported by |
|---|---|---|
| the record model | `GCStatsInfo`, `InstantMsg`, `GenLoss`, `LossMsg`, `from_mapping`, `instant_msg` | monitor, loss, control server, the JSONL reader |
| the unit conversions | `ts_to_us`, `dur_to_ms`, `secs_to_ns` | the JSON encoder, stats, stats output, loss |
| the slice text | `duration_text`, `seen_text`, `lost_collections` | `trace_converter`, and only `trace_converter` |

The slice text is presentation for a `GC Loss` slice's args and has exactly one caller. It goes
beside that caller rather than into a module of its own; `duration_text`'s docstring already
describes itself as "the way the Perfetto UI writes a duration", which is a statement about the
consumer, not about gcmon's data.

**4.2: `stats` becomes a package with three modules**, matching `tests/stats/`: the
accumulator (`Stats`, `get_quantile_value`, the DDSketch fallback), the metric table, and the
streaming aggregation (`StreamingStats`, `LossTotals`, `PauseTotals`, `CumulativeCounters` and
the two key aliases). `stats_output` stays where it is: it is presentation and it already has
one job.

**4.3: The metric table is whatever [0035](0035-derive-every-gc-sub-phase-from-one-table.md)
leaves.** If 0035 has landed, this module is the derivation from the phase table and is a few
lines; if it has not, it is the nine `Metric` classes moved verbatim. Either way this spec does
not change what a metric is. **0035 should land first**; it deletes most of what would
otherwise be moved.

**4.4: Public re-exports are preserved.** `gcmon/__init__.py` exports a fixed `__all__`; every
name in it keeps working from `gcmon` directly. `gcmon.data` and `gcmon.stats` keep re-exporting
their old contents for one release, then go.

**4.5: Three ADR implementation notes are amended in the same change.** ADR-0009 names the
module holding the ns→µs conversion; ADR-0015 names the modules holding the loss record and the
gap accounting. The ADR README's rule is explicit: amend a record when a name it anchors on
moves. This is a rename inside the implementation section of each, not a change of decision.

**Rejected: leave `data` alone and only split `stats`.** The unit conversions are the reason the
Perfetto encoder imports the record model, and they are three functions. Splitting the larger
module while leaving the smaller cross-layer dependency in place gets the less useful half.

**Rejected: one module per struct.** `GenLoss` and `LossMsg` are one record type in two pieces
and are read together everywhere; separating them would be layout for its own sake.

## 5. Seams and testing decisions

- **Seam:** the existing suite, unchanged except for imports. There is no behaviour here to
  observe, so the highest available seam is "every test that passed still passes, with no test
  body edited". A test body that has to change is evidence something moved that should not have.
- **New seam needed:** none, and none is wanted. Do not add tests asserting that a module
  exports a given name; that pins the layout this spec is choosing, and the next reorganization
  would have to delete them.
- **What makes a good test here:** nothing new. The value is in what is *not* required: if this
  move needs a new test, it was not a move. The one thing worth checking mechanically is that
  the public surface is unchanged: `gcmon.__all__` still resolves, every name in it importable
  from `gcmon` directly.
- **Prior art:** `tests/stats/` is already the four-way split this creates in the source;
  `tests/test_data.py` covers the record model and the conversions together and splits along the
  same line.
- **Cases:**
  1. The full suite passes with import lines updated and no test body changed.
  2. Every name in `gcmon.__all__` imports from `gcmon` directly, as it does today.
  3. Regression guard: `gcmon run` over a fixture produces byte-identical output on all five
     formats. A pure move that changes a byte has moved something else too.

## 6. Out of scope

- Re-keying `_running_rings`, the question 0046 left open. It is
  [0051](0051-key-the-running-rings-by-pid.md) now. Section 5 promises a move with no test body
  edited, and the re-key rewrites six assertions that read the flat shape, so carrying it here
  would blunt the one tripwire this spec has.
- Which package these modules end up in. This splits by concern; placing the results into
  layers is [0041](0041-give-the-package-explicit-layers.md), which should land after and which
  this makes tractable.
- The phase table. [0035](0035-derive-every-gc-sub-phase-from-one-table.md) owns it and should
  land first.
- `chrome_trace_io`, which has the same grab-bag shape (JSONL read, JSONL write, Chrome parse,
  two normalizers, combine orchestration). Most of it is claimed by
  [0037](0037-one-meta-emission-path-for-live-and-combined-traces.md) and 0035; splitting what
  survives is worth doing then, with the remainder in view.
- Splitting `perfetto_format`, which [0030](0030-exporter-hygiene-batch.md) already named and
  deferred.
- Any change to the JSONL schema, the `--stats` table or the Perfetto arg strings. All three are
  public and this moves code, not output.

## 7. Further notes

Sequence matters more than usual here: 0035 → 0039 → 0041. Doing 0039 first means moving nine
`Metric` classes that 0035 deletes; doing 0041 first means moving files twice.
