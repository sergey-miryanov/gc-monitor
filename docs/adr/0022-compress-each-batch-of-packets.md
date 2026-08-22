# ADR-0022: Compress each batch into one `TracePacket.compressed_packets` field

- **Status:** Accepted
- **Date:** 2026-08-22

## Context

A gcmon trace is large, and most of it is repetition: a fixed set of slice names, categories and
annotation keys, written out again for every slice drawn. An operator attaching to a production
process copies all of it off the host, attaches all of it to an issue, and waits while the UI
loads all of it.

Deflating each batch as gcmon writes it makes a capture six to nine times smaller. Perfetto's
wire format has a field for deflated packets, and the trace processor and the UI both read it.

## Decision

**A batch goes out as one `TracePacket.compressed_packets`, field 50.** It holds the serialized
`TracePacket` entries the encoder already builds, deflated. The field number joins
`perfetto_proto` as a named `IntEnum` member checked against the generated descriptor, under
[ADR-0001](0001-hand-rolled-perfetto-protobuf-encoder.md).

The file keeps its `.pftrace` extension and still opens by being dropped on the Perfetto UI. An
operator cannot tell a compressed trace from a plain one without a hex editor.

A compressed batch carries that one field and no `trusted_packet_sequence_id` (ADR-0001).

A file may hold both compressed and plain packets, and a reader accepts the mixture. gcmon writes
only the compressed form.

**The compression boundary is the flush boundary.** A batch reaches the file before the write
returns, and nothing is held back for the next one. A coarser boundary compresses a few percent
better and widens what a killed run loses.

**Deflate level 6, and no flag.** Level 9 costs half again the CPU to shave a percent off the
file. Level 1 gives up a sixth of the size to save CPU nothing is short of.

## Consequences

- The trace an operator copies off a host is six to nine times smaller, with nothing to set and
  no pipeline to edit.
- **A killed run loses at most one batch.** The file opens and yields the batches that
  completed, with their names, categories and args intact. The kill window was one packet before
  this and is one batch now.
- **A truncated trace still says nothing about what it lost.** The `stats` table on a short file
  reports no error row and no data-loss counter, on this encoding and on the plain one alike.
  This record does not fix it.
- **The trace stops being byte-reproducible across machines.** Deflate output depends on the zlib
  build, and the three CI legs do not agree on bytes for the same input. Two runs on one machine
  still produce identical files.
- **No test may pin compressed bytes, or assert a size or a ratio.** Sizes differ by zlib build,
  so a bound loose enough to pass everywhere is too loose to catch a level that changed. The
  guard is structural: the compressed batch is there, and it inflates to the packets.
- **The trace stops being greppable.** `strings gcmon.pftrace` says nothing now. It was never a
  documented property.
- Compression is invisible above the file, so a test that hands a path to the trace processor is
  unaffected. The tests that parse a trace themselves go through one reader, which inflates the
  batch.
- `perfetto` stays a development dependency, and deflate comes from the stdlib's `zlib`.

## Alternatives considered

- **Gzip the whole file.** `gzip.open` in place of `open` is a smaller change and lands within a
  percent of the same size. Rejected: gzip refuses a truncated file and recovers nothing, and a
  killed run is when an operator most wants what was captured. The bytes survive, but getting them
  back needs a repair script an operator does not have, run on a file that reports itself
  corrupt.
- **One gzip member per flush.** It reads correctly whole and fails identically when truncated.
  Rejected: it costs the "it is just a gzip file" simplicity and buys nothing.
- **Zstd, postponed rather than rejected.** Perfetto v58 added
  `TracePacket.zstd_compressed_packets`, field 133, and it compresses a trace better than deflate
  for less CPU. What holds it off is the reader that predates it: a pre-v58 trace processor does
  not know field 133, so it skips it, loads a trace with no packets and reports no error. The
  trace opens and shows nothing, and only a human looking at an empty timeline notices. That is
  the failure mode [ADR-0001](0001-hand-rolled-perfetto-protobuf-encoder.md) exists to keep out.
  Nothing degrades gracefully either: writing both fields draws every slice twice on a reader that
  knows both. Deflate is what every generation reads.
  [Spec 0058](../../specs/0058-write-the-batches-with-zstd.md) carries the switch and its trigger.
- **A `--compress` or `--compress-level` flag.** Rejected on the ground
  [ADR-0021](0021-write-one-trace-format.md) refused `--input-format`: there is one value anyone
  would set, and every other value would have to be kept working and tested.
- **A minimum size below which a batch is written plain.** A one-packet batch comes out larger
  than it went in, and a batch of ten is already well under half. Rejected: the branch costs more
  in untested code than it saves in bytes.

## Implementation

- `src/gcmon/exporters/encoder.py` holds the write path a flush and the closeout share, and the
  deflate level.
- `src/gcmon/exporters/perfetto_proto.py` holds `TracePacketField.COMPRESSED_PACKETS`.
- Tests: `tests/exporters/test_perfetto_compression.py` covers the compressed batch, the flush
  boundary, the liveness-only closeout and what a killed run still opens;
  `tests/exporters/test_perfetto_proto.py` checks the field number against the generated
  descriptor; `tests/helpers.py` holds the reader every Perfetto test reads through, and
  `tests/test_helpers.py` covers it; `tests/benchmarks/test_bench_trace_write.py` measures the
  write path on the CodSpeed job.
