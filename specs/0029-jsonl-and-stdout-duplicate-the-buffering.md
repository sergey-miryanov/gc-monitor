# 0029: Share one buffer-and-flush implementation with the JSONL exporters

- **Status:** **Superseded** by [0036](0036-one-exporter-method-per-record-kind.md), which
  removes the duplication by construction rather than by extraction
- **Kind:** feature (cleanup)
- **Effort:** M
- **Origin:** post-v0.2.0 code review (old spec 18, REQ-3 and REQ-4)
- **Respects:** [ADR-0008](../docs/adr/0008-buffered-exporter-and-encoder-protocol.md) (exporter buffers, encoder serializes), [ADR-0009](../docs/adr/0009-nanoseconds-canonical-time-unit.md) (nanoseconds internally)

## 0. Why this is superseded

The three byte-identical buffering blocks exist because `EventsExporter` has three `add_*`
methods for the JSONL path to implement. 0036 collapses the interface to one `add`, so there is
one block and nothing left to extract; section 4's generic holder is not needed under that design.

Everything else here still holds and 0036 carries it forward verbatim: the JSONL schema does not
change and JSONL does not move onto `TraceEvent` (section 4, and the rejected alternative that goes
with it), the missing `_closed` guard, the `_open_writer` override, and the golden-file test.
Read section 4 before touching the JSONL exporter: it is the fullest statement of why the file format
is load-bearing, and 0036 summarizes rather than replaces it.

## 1. Problem statement

Every time a record type is added to gcmon, it has to be added to `JsonlExporter` three times
over, and the third copy is where the mistake will be. `add_event`, `add_loss_event` and
`add_instant_event` each carry their own byte-identical copy of the same twelve-line
lock/append/threshold/flush dance, none of it shared with the base class that exists to hold
exactly that logic. The loss records that landed with
[ADR-0015](../docs/adr/0015-gc-loss-spans-on-their-own-track.md) added the third copy. The next
record type adds a fourth.

`JsonlExporter.close()` also has no `_closed` guard, so unlike every other exporter it accepts
events after close and buffers them where nothing will ever flush them.

## 2. Solution

Nothing changes for an operator. `--format jsonl` and `--format stdout` write byte-identical
output, with the same schema documented in
[docs/formats.md](../docs/formats.md#jsonl-output), and `gcmon combine` reads that output back
exactly as it does today. What changes is that adding a record type touches one method instead
of three, and closing a JSONL exporter twice is as safe as closing any other.

## 3. User stories

1. As a maintainer adding a record type, I want to write the buffering once, so that a record
   type cannot end up flushed on one path and dropped on another.
2. As an operator with an existing JSONL pipeline, I want the file format to stay exactly as it
   is, so that my parsers and my archived captures keep working.
3. As an operator running `gcmon combine` over JSONL I recorded last month, I want it to read
   back unchanged, so that this is not a migration.
4. As a maintainer, I want `close()` to mean closed on every exporter, so that a
   double-close in a teardown path is not a per-class question.
5. As someone piping `--format stdout` into a log aggregator, I want each line flushed as it is
   today, so that a long-running monitor is not silent between flush thresholds.

## 4. Implementation decisions

**The JSONL record shape does not change, and JSONL does not move onto `TraceEvent`.** This is
the decision the original review item got wrong. `BufferedTraceExporter` buffers `TraceEvent`
and hands them to an `EventEncoder`; `JsonlExporter` buffers `to_mapping(item)`, the raw
record fields (`gen`, `collections`, `ts_start`, `heap_size`, …). Those are not two encodings
of one thing. The JSONL schema is public, documented per-field in
[docs/formats.md](../docs/formats.md#jsonl-output), and read back by
`chrome_trace_io.read_jsonl` to drive `gcmon combine`. Routing JSONL through the `TraceEvent`
model would rewrite every line of every JSONL file gcmon has ever produced and break the
combine reader in the same commit. Rejected.

So the sharing happens one level down, at the buffering, not at the representation:

1. Extract the lock/append/threshold/flush logic out of `BufferedTraceExporter` into a small
   generic holder: one buffer, two locks, one threshold, one `_closed` flag, parameterized by
   the buffered item type. `BufferedTraceExporter` keeps it as a member holding `TraceEvent`;
   `JsonlExporter` holds one over its record mappings.
2. `JsonlExporter`'s three `add_*` methods shrink to "build the mapping, hand it to the
   holder". The flush callback is the existing `_flush`.
3. `close()` goes through the holder and inherits the `_closed` guard. A second call returns
   without touching the file.
4. `StdoutExporter` keeps its `_open_writer` override. It is three lines, it is the one thing
   that genuinely differs between writing to a file and writing to an already-open stream, and
   removing it was only necessary under the rejected `TraceEvent` design.

**Rejected:** making `JsonlExporter` a `BufferedTraceExporter` subclass with a
`JsonlEventEncoder`. That is the same as routing JSONL through `TraceEvent`, above.

**Rejected:** leaving the duplication and adding a test per record type per method. It doubles
the test surface to protect against a copy-paste error that extraction removes outright.

**Open, to settle when picked up:** whether the extracted holder is worth its own module or
lives beside `BufferedTraceExporter`. Settled by how much `JsonlExporter` actually needs: if
the two uses share fewer than ~30 lines, keep it a private base class in
`_buffered_exporter.py` rather than a new file.

## 5. Seams and testing decisions

- **Seam:** the on-disk file, through `tests/exporters/test_jsonl_exporter.py` and the JSONL
  leg of `tests/test_convert_cmd.py`. That is the highest seam available and the correct one:
  the contract this refactor must not break is the file itself, not the class structure.
- **New seam needed:** none. Do **not** assert on `__mro__` or on which class holds the
  buffer; that pins the implementation this spec is trying to make free to change.
- **What makes a good test here:** a golden-file comparison. Capture the JSONL a fixed set of
  records produces today, and assert the refactor reproduces it byte-for-byte, including the
  loss records and the instant events. "The file has three lines and parses as JSON" would
  pass on output with the wrong field names.
- **Prior art:** `tests/test_convert_cmd.py` for the JSONL round-trip through `read_jsonl`; the
  chrome↔perfetto content-equivalence test in `tests/test_convert_cmd_perfetto.py` for the
  shape of an equivalence assertion.
- **Cases:**
  1. GC records, loss records and instant events all reach the file when the buffer never
     reaches the flush threshold and `close()` is what drains it, the path each of the three
     duplicated blocks owns today.
  2. `close()` twice writes the file once; an `add_event` after `close()` does not resurrect
     the file.
  3. Regression guard: byte-identical JSONL for a fixed input, and `gcmon combine` over that
     file produces the same trace as before.

## 6. Out of scope

- Any change to the JSONL schema, including the `ts` unit. Both are public and documented.
- `output_path` on the `EventsExporter` ABC. That is [0028](0028-combined-exporter-reaches-into-sub-exporter-privates.md),
  which is independent and should land first.
- A JSONL *input* format for anything other than `combine`.
- Making `JsonlExporter.output_path` a required argument. It is `Path | None` because
  `StdoutExporter` has no path; that is settled by 0028.
- Compression, rotation, or line buffering policy for `--format stdout`.
