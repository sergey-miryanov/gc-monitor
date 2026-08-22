# 0058: Write the batches with zstd

- **Status:** Blocked (the `perfetto` package shipping a v58 trace processor; the newest on PyPI
  on 2026-08-22 is 0.57.2)
- **Kind:** feature (efficiency)
- **Effort:** S
- **Origin:** raised 2026-08-22, out of the alternative
  [ADR-0022](../docs/adr/0022-compress-each-batch-of-packets.md) postponed and told the next person
  to take again. Perfetto v58.2 shipped the field this needs, and the measurements below were
  taken against a v58.2 trace processor
- **Respects:** [ADR-0001](../docs/adr/0001-hand-rolled-perfetto-protobuf-encoder.md) (hand-rolled
  encoder, every field number a named `IntEnum` checked against the generated descriptor,
  `perfetto` stays dev-only; `compression.zstd` is stdlib and adds no runtime dependency),
  [ADR-0008](../docs/adr/0008-buffered-exporter-and-encoder-protocol.md) (the exporter buffers,
  the encoder serializes), [ADR-0014](../docs/adr/0014-perfetto-integration-test-strategy.md)
  (assert what the trace means, through the trace processor),
  [ADR-0021](../docs/adr/0021-write-one-trace-format.md) (one trace format, and no flag to pick
  another), [ADR-0022](../docs/adr/0022-compress-each-batch-of-packets.md) (one compressed packet
  per batch, on the flush boundary, no flag)

## 1. Problem statement

A gcmon trace is 12% to 14% larger than the same events compress to, and gcmon spends about three
times the CPU producing it. The codec accounts for both.

Measured on the two large captures in this repo, cut into the 250-packet batches the encoder
flushes in, with CPU as a multiple of what deflate 6 costs on the same run:

| codec | `loss_pauses` | `cyclotron` | CPU |
| :-- | --: | --: | --: |
| deflate 6, what gcmon writes | 8.94x | 6.50x | 1.0x |
| zstd 3 | 10.22x | 7.30x | 0.28-0.33x |
| zstd 9 | 10.53x | 7.46x | 1.3-1.7x |
| zstd 19 | 11.45x | 8.09x | 68-84x |

zstd at its default level beats deflate 6 on both axes, and that deflate is zlib-ng, the fast one.
On a stock zlib the gap is wider.

An operator sees the size. The CPU matters because compression runs on the flush, while the target
is working.

## 2. Solution

gcmon writes the same trace, smaller again and for less CPU than it spends today: the same slices,
names, categories, args, counters, tracks, SQL keys and file extension, with nothing to run before
opening it and no flag to remember, exactly as ADR-0022 left it.

**An operator on a Perfetto older than v58 can no longer read the trace.** That reader does not
refuse the file: it skips the field it does not know and shows an empty timeline.

## 3. User stories

1. As an operator attaching to a production process, I want the trace smaller again, so that
   moving the file off the host stays out of the way of the investigation.
2. As an operator monitoring a latency-sensitive target, I want gcmon to spend less CPU on the
   flush than it does today, so that observing a run disturbs it less than it already did.
3. As someone opening a capture in the Perfetto UI, I want every slice, arg, counter and track to
   read exactly as it does now, so that traces from either side of this change are comparable.
4. As someone querying with Perfetto SQL, I want every key in `docs/perfetto-sql.md` to keep
   working unedited.
5. **As someone still on a Perfetto older than v58, I want to be told that this file needs a newer
   one**, so that I do not read an empty timeline as a run that captured nothing.
6. As someone holding a trace captured before this change, I want it to keep opening, so that an
   archived capture does not stop being readable.
7. As a gcmon maintainer, I want a trace that silently went back to deflate, or that names the
   wrong field, to fail a test rather than a human opening the UI.

## 4. Implementation decisions

### 4.1 The field is `TracePacket.zstd_compressed_packets`, field 133

Pinned to Perfetto `v58.2`, where it reads `bytes zstd_compressed_packets = 133;`. It joins
`perfetto_proto` as `TracePacketField.ZSTD_COMPRESSED_PACKETS`, under ADR-0001's rule that every
field number is checked against the generated descriptor.

The payload is what field 50 carries today: a serialized sequence of `TracePacket` entries, the
whole batch, compressed. Only the codec and the field number change. The batch boundary, the one
compressed batch per flush, the absence of a flag, and the file's extension are ADR-0022's and
stay.

### 4.2 Level 3, which is zstd's default

The table in section 1 is the argument. Level 9 costs more CPU than the deflate 6 it replaces and
buys 2% to 3% over level 3; level 19 is two orders of magnitude slower and cannot run on a flush.

**Rejected: carrying the level over from deflate.** 6 is a sensible deflate level and an arbitrary
zstd one.

**Rejected: a flag to choose the codec or the level.** ADR-0021 and ADR-0022 refused a flag on the
same ground: there is one value anyone would set, and the rest have to be kept working and tested.

### 4.3 The reader keeps both branches

`tests/helpers.py`'s reader inflates field 50 today. It gains field 133 and **keeps field 50**, so
a capture taken before this change still reads, and so does a file mixing the two. gcmon writes
one.

### 4.4 What an older reader does, and what to do about it

A pre-v58 trace processor does not refuse the file. It does not know field 133, so it skips it,
loads a trace with no packets and reports no error: the trace opens, shows nothing, and only a
human looking at an empty timeline notices. ADR-0001 exists to keep that failure mode out. Take it
here in exchange for the size and the CPU.

Nothing gcmon writes reaches an operator's own copy of Perfetto. The mitigation is documentation: a
`### Breaking changes` line naming the minimum version, and the same minimum beside the word
`compressed` where `docs/formats.md` names the format.

**Rejected: writing both fields, so that an old reader takes 50 and a new one takes 133.** A reader
that knows both expands both and draws every slice twice. Perfetto's advice to set both codecs is
about a service choosing one, not about a file carrying two.

**Rejected: keeping deflate behind a flag for old readers.** Section 4.2's ground, and both
encodings would have to be kept working.

### 4.5 The gate, and why this is Blocked rather than Not started

Both halves arrive together in the `perfetto` development dependency:

- The **generated descriptor** has to carry `zstd_compressed_packets` for ADR-0001's check to run
  at all. On 0.57.2 there is nothing to check the number against.
- The **trace processor** the suite drives has to read field 133. On 0.57.2 every trace-processor
  test would query an empty trace: the ones asserting a count would go red, and any asserting only
  that a file loads would pass while meaning nothing.

Perfetto v58.2 has published prebuilts for `linux-amd64`, `mac-amd64`, `mac-arm64` and
`windows-amd64`, so all three CI legs are covered once the Python package picks one up. Take this
spec when `pip index versions perfetto` shows 0.58 or later; the bump in `pyproject.toml` is part
of the change.

**Rejected: pinning `bin_path` to a downloaded v58.2 binary to unblock early.** It leaves the
descriptor check unrunnable, and it puts a manually fetched binary on the critical path of every
CI leg.

## 5. Seams and testing decisions

- **Seam:** the trace processor, through `tests/helpers.py`'s reader for the wire level and SQL for
  meaning. Unchanged from ADR-0022: compression is invisible above the file, so every test that
  hands a path to `TraceProcessor` stays untouched.
- **New seam needed:** none. `tests/exporters/test_perfetto_compression.py` already owns the
  behaviours that exist because a batch is compressed, and each of its cases carries over.
- **What makes a good test here:** assert the name, category, arg and counter a slice resolves to,
  and assert that the batch sits on field 133. **Never assert a size or a ratio**, for
  the reason ADR-0022 gives.
- **Prior art:** `tests/exporters/test_perfetto_compression.py` for all of it, and
  `tests/exporters/test_perfetto_proto.py` for the descriptor check.
- **Cases:**
  1. Every top-level packet is a `ZSTD_COMPRESSED_PACKETS` batch, one per flush, and inflating
     them yields the packets. The existing cases move from field 50 to field 133.
  2. A run flushed over at least two batches resolves every slice name, category, arg and counter
     through the trace processor.
  3. A file truncated mid-batch opens and yields the batches that completed. The kill window
     belongs to the batch boundary, not the codec, so it has to survive the change.
  4. The liveness-only `close()` path produces a valid file.
  5. `TracePacketField.ZSTD_COMPRESSED_PACKETS` matches the generated descriptor.
  6. **A trace written with field 50 still reads.** The one new case. It guards the branch section
     4.3 keeps: once the encoder stops writing deflate, nothing else in the suite produces a
     deflate trace.

## 6. Out of scope

- **Reading a Perfetto trace back.** Still outside the encoder's remit (ADR-0001, ADR-0021). The
  inflate stays a test helper.
- **A flag, for the codec or for the level.** Section 4.2.
- **Changing the batch boundary.** ADR-0022 settled it on the truncation property, which this does
  not touch.
- **Compressing JSONL captures.** As 0057 left it: it would change what a reader has to do, where
  this changes only the writer.
- **Making a truncated trace announce itself.** Still the known gap ADR-0022 records.
- **Telling an operator at runtime that their Perfetto is too old.** gcmon does not know what will
  open the file. That leaves documentation, section 4.4.

## 7. Further notes

**ADR.** Amend [ADR-0022](../docs/adr/0022-compress-each-batch-of-packets.md) rather than write a
new record. Its decision is the shape: one compressed packet per batch, on the flush boundary, with
no flag. This change leaves that shape alone and rewrites two lines of it, the codec and the field
number. The amendment turns the zstd alternative into the decision and leaves deflate behind as
what the reader still accepts.

**CHANGELOG.** One line under `### Features` for the size, and one under `### Breaking changes`
naming the minimum Perfetto version. The second is the whole of what section 4.4 can do.

**Re-measure on the way past.** Section 1 was measured on one machine, against zlib-ng. What has
to hold before this lands is the ordering, zstd 3 beating deflate 6 on both axes, and not the
figures themselves.

**0056 is measured against deflate.** Its section 1 was re-measured against a compressed baseline
when 0057 landed. If this lands first, that baseline moves again, by the 12% to 14% above.
