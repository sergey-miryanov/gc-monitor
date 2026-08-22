# 0057: Compress the packets a trace writes

- **Status:** Not started
- **Kind:** feature (efficiency)
- **Effort:** S
- **Origin:** raised 2026-08-22, against
  [0056](0056-intern-the-strings-a-trace-repeats.md) §4.7, which rejected compression as buying
  "the file size and nothing else"; reversed on measurement
- **Respects:** [ADR-0001](../docs/adr/0001-hand-rolled-perfetto-protobuf-encoder.md) (hand-rolled
  encoder, every field number a named `IntEnum`, `perfetto` stays dev-only; the deflate is stdlib
  `zlib` and adds no runtime dependency),
  [ADR-0008](../docs/adr/0008-buffered-exporter-and-encoder-protocol.md) (the exporter buffers, the
  encoder serializes), [ADR-0014](../docs/adr/0014-perfetto-integration-test-strategy.md) (assert
  what the trace means, through the trace processor),
  [ADR-0021](../docs/adr/0021-write-one-trace-format.md) (one trace format, and no flag to pick
  another)
- **Ordered before** [0056](0056-intern-the-strings-a-trace-repeats.md), whose problem statement
  this partly supersedes; see section 7

## 1. Problem statement

A gcmon trace is large and almost entirely repetition. The pyperformance capture in this repo is
127.4 MB of protobuf holding 1.6 million slices drawn from a fixed set of names, categories and
annotation keys. An operator attaching to a production process copies all of it off the host,
attaches all of it to an issue, and waits while the UI loads all of it.

Deflate on the same bytes, at the batch granularity gcmon already writes in:

| trace | size | compressed | ratio |
| :-- | --: | --: | --: |
| `output/loss_variants.pftrace` | 267 KB | 28.6 KB | 9.3x |
| `output/loss_pauses.pftrace` | 7.4 MB | 0.83 MB | 8.9x |
| `scripts/analysis/cyclotron/cyclotron.pftrace` | 36.6 MB | 5.5 MB | 6.6x |
| `scripts/analysis/pyperformance/trace.pftrace` | 127.4 MB | 19.1 MB | 6.7x |

Nothing about the trace changes to get this. The Perfetto format carries a compressed form and the
trace processor reads it; gcmon writes the uncompressed one because nobody asked the question.

0056 measures the same repetition and answers it by interning, which halves the file. Both were
measured against an uncompressed baseline that will not exist once this lands. Section 7 says what
that leaves of 0056.

## 2. Solution

The same trace. Same slices, same names, same categories, same args, same counters, same tracks,
same SQL keys, same file extension. It is roughly eight times smaller.

There is nothing to run before opening it and no flag to remember: a trace is compressed, or gcmon
did not write it. A run killed part way still opens, and loses at most the **batch** it was
writing when it died.

## 3. User stories

1. As an operator attaching to a production process, I want the trace small enough to copy off the
   host without thinking about it, so that moving the file is not the slow part of an
   investigation.
2. As someone opening a capture in the Perfetto UI, I want every slice, arg, counter and track to
   read exactly as it does today, so that traces from either side of this change are comparable.
3. As someone querying a trace with Perfetto SQL, I want every key in `docs/perfetto-sql.md` to
   keep working unedited, so that no saved query has to be revisited.
4. As an operator whose run was killed part way, I want the truncated file to open and show me
   what it captured, so that a run that did not finish is still worth reading.
5. As a CI job archiving traces, I want the saving without setting anything, so that no pipeline
   has to be edited to get it.
6. As an operator, I want the file to still be called `gcmon.pftrace` and still open by being
   dropped on `ui.perfetto.dev`, so that nothing I already know stops being true.
7. As an operator monitoring a latency-sensitive target, I want the compression not to compete
   with the process gcmon is watching, so that observing a run does not change it.
8. As a gcmon maintainer, I want a trace that silently stopped being compressed to fail a test, so
   that this does not become the fifth bug of ADR-0001's kind.

## 4. Implementation decisions

### 4.1 The wrapper is `TracePacket.compressed_packets`

Field 50, deflate, carrying a serialized sequence of `TracePacket` entries -- exactly what
`ProtobufEventEncoder` already builds with `encode_bytes_field(TraceField.PACKET, entry)`. It
joins `perfetto_proto.py` as `TracePacketField.COMPRESSED_PACKETS`, under ADR-0001's rule that
every field number is a named `IntEnum` member checked against the generated descriptor.

The file keeps its extension, its magic and its identity. An operator cannot tell a compressed
trace from a plain one without a hex editor, which is what makes section 4.3's "no flag" tenable.

A file may hold both compressed and plain packets; the reader accepts a mixture. gcmon will not
write one, but the format allows it, so a later change is not boxed in.

### 4.2 One compressed packet per batch

The compression boundary is the flush boundary. `write_events` deflates the batch it already
built; `close()` deflates the closeout packets `finalize_perfetto_packets` returns. No second
batching concept, no state held across flushes, and no bytes left unwritten that `write_events`
was told to persist.

**Measured cost of choosing the flush boundary over a coarser one.** At 250 packets per batch the
pyperformance capture compresses 6.7x; at 1000 it compresses 8.0x. The finer batching gives up
about 3 MB of 127 MB to keep section 4.5's truncation property at one batch.

**Rejected: a minimum size below which a batch is written plain.** A 1-packet batch expands to
119% of itself; 3 packets already shrink to 93%, 10 to 40%, 100 to 12%. The branch would cost more
in untested code than it saves in bytes.

### 4.3 Level 6, and no flag

Measured at 250 packets per batch:

| level | `loss_pauses` | `trace.pftrace` | deflate, whole 127.4 MB capture |
| --: | --: | --: | --: |
| 1 | 6.8x | 5.6x | 0.28 s |
| 6 | 8.9x | 6.7x | 0.82 s |
| 9 | 9.0x | 6.8x | 1.19 s |

Level 9 buys 1% for 45% more CPU. Level 1 gives up a sixth of the size for CPU that is not
contended: 0.82 s is the whole cost of compressing the largest capture in this repo, spread across
the run that produced it.

**Rejected: a `--compress` or `--compress-level` flag.** ADR-0021 rejected `--input-format` on the
ground that a question with one real answer is not a question, and a second encoding has to be
kept alive in order to be tested. Section 5 depends on there being exactly one thing to assert.

Story 7 is the one claim here that arithmetic cannot settle -- 0.82 s of bulk throughput is not an
observation of the writer under load. Section 5 makes it a benchmark rather than an assertion.

### 4.4 One write path

`write_events` and `close()` hold near-identical copies of the same block: pick `"wb"` or `"ab"`
off `_has_written`, open, loop, frame each entry, flush. Both move to one private method:

```python
def _write_batch(self, descriptors: Sequence[bytes], packets: Sequence[bytes]) -> None:
```

It owns the append mode, the framing and the deflate, so compression has one home rather than two.

The descriptors-before-packets order that ADR-0008 relies on stops being two loops in the order
someone happened to write them and becomes the parameter order of a signature.

### 4.5 Rejected: gzip the file

`gzip.open` instead of `open` is a smaller change and lands within 1% of the same size. It fails
story 4. Measured on `output/loss_pauses.pftrace`, killed at 95% of the file:

| encoding | opens after the kill? | slices recovered, of 25,721 |
| :-- | :-- | --: |
| plain `.pftrace` | yes | 24,448 |
| `compressed_packets`, 250-packet batches | yes | 24,344 |
| whole-file gzip | **no** | 0 |

The truncated `.gz` is refused outright. Its bytes are not lost -- inflating what there is and
trimming to the last complete packet recovered 24,390 slices -- but that is a repair script an
operator does not have, on a file that reports itself corrupt.

Appending one gzip member per flush was measured too. It reads correctly whole and fails
identically when truncated, so it costs the "it is just a gzip file" simplicity and buys nothing.

`compressed_packets` widens the kill window from one packet to one batch: 104 slices on the
capture above. That is the price of the 8.9x, and it is the whole of the price.

**Both losses are silent, before and after this change.** The `stats` table on a truncated plain
trace and on a truncated compressed one reports no error row and no data-loss counter. This spec
does not fix that and does not make it worse; it is recorded here so that the next person to look
finds it already known.

### 4.6 Rejected: zstd

Python 3.15 ships `compression.zstd`, and it compresses these traces better than deflate. The
trace processor refuses the result: `Trace parse failure (Unknown trace type provided (ERR:fmt))`.
Perfetto has no zstd trace type. Written down because the stdlib module is new and the next person
will reach for it.

### 4.7 What stops being true

**The trace stops being byte-reproducible across machines.** Deflate output depends on the zlib
build: this project's interpreter links zlib-ng 1.3.1, CI runs `ubuntu-latest`, `macos-latest` and
`windows-latest`, and the three do not agree on bytes for the same input. Two runs on one machine
still produce identical files. Nothing in the repo hashes or byte-compares a trace now that 0055
has taken the Chrome encoder's `b"[]\n"` assertion with it, so nothing breaks -- but section 5 has
to stop pinning compressed bytes, and no future test may start.

**The trace stops being greppable.** `strings gcmon.pftrace` says nothing after this. It was never
a documented property and no test relies on it.

## 5. Seams and testing decisions

- **Seam:** the trace processor, through `tests/helpers.py::assert_valid_perfetto_trace` for the
  wire level and SQL for meaning. Compression is invisible above the file, so every test that
  hands a path to `TraceProcessor` is already correct and stays untouched.
- **New seam needed:** none for reading -- `assert_valid_perfetto_trace` gains the inflate. It is
  the project-wide reader, it already parses through Perfetto's generated schema so that a wrong
  field number fails there (ADR-0001), and its ten callers stop caring. The sites that parse a
  trace themselves route through it: `MonitoredRun.packets` in
  `tests/monitoring/test_monitored_run_trace.py`, `_packets` in `tests/test_convert_cmd.py`, and
  the `ParseFromString` calls in `tests/exporters/test_combine.py`,
  `test_exporter_thread_safety.py` and `test_perfetto_exporter.py`. A new
  `tests/exporters/test_perfetto_compression.py` takes the behaviours that exist only because of
  this change.
- **What makes a good test here:** assert the name, category, arg and counter a slice resolves to,
  and assert structurally that a wrapper is present. **Never assert a size or a ratio.** zlib-ng
  and stock zlib disagree on output bytes, so a bound tight enough to catch "compression stopped
  working" is a bound loose enough to be red on somebody's laptop. Case 4 catches that failure
  without a number.
- **Prior art:** `tests/exporters/test_perfetto_loss_track.py` for the SQL assertions;
  `tests/test_convert_cmd_perfetto.py` for the oracle comparing a decoded trace against the
  `list[TraceEvent]` it was built from, which is the regression guard this change leans on.
- **Cases:**
  1. A run flushed over at least two batches carries one wrapper per batch, and every slice name,
     category, arg and counter resolves through the trace processor. The `perfetto_exporter`
     fixture already takes a threshold.
  2. A file truncated mid-batch opens, and yields the batches that completed. This pins section
     4.5's reason for choosing `compressed_packets` over gzip; without it that decision is
     unrecorded in the suite.
  3. The liveness-only `close()` path -- a run whose target never collected -- produces a valid
     compressed file. It is the only path `finalize_perfetto_packets` owns alone.
  4. The wrapper is there: `TracePacketField.COMPRESSED_PACKETS` present at the top level, and
     inflating it yields the packets. This is the whole guard between "compressed" and "silently
     not compressed".
  5. `TracePacketField.COMPRESSED_PACKETS` matches the generated descriptor, in
     `tests/exporters/test_perfetto_proto.py` beside its neighbours.
  6. Regression guard: `tests/test_convert_cmd_perfetto.py`'s oracle, unchanged in intent, reading
     through the helper.

`tests/monitoring/test_monitored_run_trace.py` pins a whole run as decoded `TracePacket` text in
`tests/fixtures/monitored_run_perfetto_trace.txt`. It pins the **inflated** stream: the diff its
docstring instructs a reviewer to read stays readable, and the fixture stays identical on all
three CI legs. Its regeneration entry point, `python -m tests.monitoring.test_monitored_run_trace`,
writes the inflated form too, or that instruction stops being true.

**Benchmark.** `tests/benchmarks/` and the CodSpeed job that runs `-m benchmark` already exist. One
benchmark over the encoder's write path lands beside `test_bench_trace_conversion.py` and turns
story 7 from arithmetic into a measurement CI keeps honest.

## 6. Out of scope

- **Compressing JSONL captures.** They compress about as well, 8.5x on
  `output/synthetic_capture.jsonl`, and they are what piles up on a host. But gcmon reads JSONL
  back -- `read_jsonl`, `convert`, `combine`, and the `jsonl -> jsonl` path ADR-0021 kept -- so it
  is a reader change where this is a writer change, and it would want whole-file gzip rather than
  in-format framing. It shares no mechanism with this spec.
- **Interning the strings a trace repeats.** 0056, and section 7.
- **A flag to turn compression off.** Section 4.3.
- **Making a truncated trace announce itself.** Section 4.5 records that a short file opens looking
  complete, on both encodings. Fixing it is not this change.
- **Reading a Perfetto trace back.** Still outside the encoder's remit (ADR-0001, ADR-0021). The
  inflate added in section 5 is a test helper, not a decoder gcmon ships.

## 7. Further notes

**What this leaves of 0056.** Its section 1 states the file is "about twice the size it needs to
be". After this lands that is false: interning a compressed trace takes it from 19.1 MB to 17.7 MB
on the pyperformance capture, 6% rather than 48%. Measured by rewriting all four traces at the
wire level -- every `TrackEvent.name`, `TrackEvent.categories` and `DebugAnnotation.name`,
recursing into `dict_entries`, replaced by its `*_iid` varint plus the table cost -- and
compressing the result. The gain over compression alone is 6% to 13% on the three large captures.

0056 keeps two claims this does not touch: interning halves the bytes the writer produces, and
halves the strings the trace processor resolves at load. Neither is measured. **Re-open 0056
against a compressed baseline and measure those two**, rather than against a file that will no
longer exist. Its status line is marked superseded-pending until then.

**ADR.** Write ADR-0022 for these decisions: the wrapper and why not gzip or zstd, the batch
boundary, the level, and the two properties section 4.7 gives up. 0056's section 7 claimed
ADR-0022 for interning; that becomes **ADR-0023**, since 0055 took 0021 and this takes 0022.

**CONTEXT.md** gains **Batch**, after **Event**, in *What the target writes, what gcmon writes*.
It stops being a flush-buffer detail here and becomes the answer to "my run was killed, what did I
lose?".

**CHANGELOG.** One line under `### Features` in the live `## WIP` block: the trace file is roughly
eight times smaller. Nothing under `### Breaking changes` -- nothing gcmon documents depended on
the file being plain bytes, and section 4.7's losses are internal.

**docs/formats.md** gains one sentence saying a Perfetto trace is compressed and needs nothing done
to it before opening. **docs/perfetto-sql.md** is untouched, which is the point.
