# ADR-0022: Compress each batch into one `TracePacket.zstd_compressed_packets` field

- **Status:** Accepted
- **Date:** 2026-08-25

## Context

A gcmon trace is large, and most of it is repetition: a fixed set of slice
names, categories and annotation keys, written out again for every slice
drawn. An operator attaching to a production process copies all of it off the
host, attaches all of it to an issue, and waits while the UI loads all of it.

Compressing each batch as gcmon writes it takes most of that out. Perfetto's
wire format has a field for compressed packets, and the trace processor and
the UI both read it.

## Decision

**A batch goes out as one compressed `TracePacket`: `zstd_compressed_packets`,
field 133, where the interpreter has `compression.zstd`, and
`compressed_packets`, field 50, where it does not.** The packet holds the
serialized `TracePacket` entries the encoder already builds. Both field
numbers join `perfetto_proto` as named `IntEnum` members checked against the
generated descriptor, under
[ADR-0001](0001-hand-rolled-perfetto-protobuf-encoder.md).

`compression.zstd` is an optional part of a CPython build. The codec resolves
once at import from what the interpreter has, never from a flag and never per
run, and no file records which wrote it. A fallback capture is the more
portable one: it opens on any Perfetto.

The file keeps its `.pftrace` extension and still opens by being dropped on
the Perfetto UI. An operator cannot tell a compressed trace from a plain one
without a hex editor.

A compressed batch carries that one field and no `trusted_packet_sequence_id`
(ADR-0001).

A file may hold plain packets and either compressed form, and a reader accepts
the mixture. gcmon writes one form per file.

**The reader takes both fields.** `tests/helpers.py` is the only thing here
that reads a trace back, and field 50 reaches it from a fallback capture and
from the deflate Perfetto's own tooling writes.

**The compression boundary is the flush boundary.** A batch reaches the file
before the write returns, and nothing is held back for the next one. A coarser
boundary compresses a few percent better and widens what a killed run loses.

**Level 3 for zstd, 6 for deflate, both written out here, and no flag.** Level
3 is zstd's own default today. Pinning it means a change to that default
upstream reaches gcmon when someone here decides it should and not before.
Level 9 costs more CPU than the deflate it replaces and buys a few percent,
and level 19 is orders of magnitude slower and cannot run on a flush.

## Consequences

- The trace an operator copies off a host is a fraction of the size, with
  nothing to set and no pipeline to edit.
- **A zstd trace needs Perfetto v58 or newer.** An older reader does not know
  field 133: it skips the field, loads a trace with no packets and reports no
  error, and only a human looking at the empty timeline notices. That is the
  failure mode [ADR-0001](0001-hand-rolled-perfetto-protobuf-encoder.md)
  exists to keep out, taken in exchange for the size and the CPU. gcmon cannot
  warn: it does not know what will open the file. Documentation is the whole
  mitigation, and a fallback capture opens anywhere.
- **A killed run loses at most one batch.** The file opens and yields the
  batches that completed, with their names, categories and args intact. The
  kill window was one packet before this and is one batch now.
- **A truncated trace still says nothing about what it lost.** The `stats`
  table on a short file reports no error row and no data-loss counter, on this
  encoding and on the plain one alike. This record does not fix it.
- **The trace stops being byte-reproducible across machines.** Compressed
  output depends on the codec build, and the three CI legs do not agree on
  bytes for the same input. Two runs on one machine still produce identical
  files.
- **No test may pin compressed bytes, or assert a size or a ratio.** Sizes
  differ by codec build, so a bound loose enough to pass everywhere is too
  loose to catch a level that changed. The guard is structural: the compressed
  batch is there, and it inflates to the packets.
- **The trace stops being greppable.** `strings gcmon.pftrace` says nothing
  now. It was never a documented property.
- Compression is invisible above the file, so a test that hands a path to the
  trace processor is unaffected. The tests that parse a trace themselves go
  through one reader, which inflates the batch.
- **The suite pins its own trace processor.** The `perfetto` package ships
  protos and prebuilt binary from different releases, and its prebuilt does
  not read field 133. Left to the package, the suite would load every zstd
  batch as an empty trace and report no error.
- `perfetto` stays a development dependency, and both codecs come from the
  stdlib: `compression.zstd` and `zlib`.
- **The trace format depends on the interpreter that wrote it.** Two people on
  one gcmon version can produce different files, and neither names the codec
  that wrote it. The reader takes both.

## Alternatives considered

- **Gzip the whole file.** `gzip.open` in place of `open` is a smaller change
  and lands within a percent of the same size. Rejected: gzip refuses a
  truncated file and recovers nothing, and a killed run is when an operator
  most wants what was captured. The bytes survive, but getting them back needs
  a repair script an operator does not have, run on a file that reports itself
  corrupt.
- **One gzip member per flush.** It reads correctly whole and fails
  identically when truncated. Rejected: it costs the "it is just a gzip file"
  simplicity and buys nothing.
- **Deflate everywhere, which every Perfetto generation reads.** It was the
  decision here, and what held it was the empty timeline an older reader
  draws, never the codec: zstd is smaller than deflate 6 on both captures in
  this repo and costs a fraction of the CPU on each. Rejected once v58.2
  shipped a trace processor for field 133 and the suite could pin one. It
  survives as the fallback, not as the format.
- **Writing both fields, so that an old reader takes 50 and a new one takes
  133.** Rejected: a reader that knows both expands both and draws every slice
  twice. Perfetto's advice to set both codecs is about a service choosing one,
  not about a file carrying two.
- **Keeping deflate behind a flag for old readers.** Rejected on the ground
  the flag entry below gives. The fallback covers a build that cannot write
  zstd; a reader that cannot read it is a different problem, and a flag would
  put both encodings on the tested write path for good.
- **A `--compress` or `--compress-level` flag.** Rejected on the ground
  [ADR-0021](0021-write-one-trace-format.md) refused `--input-format`: there
  is one value anyone would set, and every other value would have to be kept
  working and tested.
- **A minimum size below which a batch is written plain.** A one-packet batch
  comes out larger than it went in, and a batch of ten is already well under
  half. Rejected: the branch costs more in untested code than it saves in
  bytes.

## Implementation

- `src/gcmon/exporters/encoder.py` holds the write path a flush and the
  closeout share, the two levels, and the import that resolves the codec.
- `src/gcmon/exporters/perfetto_proto.py` holds
  `TracePacketField.ZSTD_COMPRESSED_PACKETS` and `COMPRESSED_PACKETS`, one per
  branch.
- `tests/perfetto_prebuilt.py` pins the trace processor the suite drives, to a
  build that reads field 133.
- Tests: `tests/exporters/test_perfetto_compression.py` covers the compressed
  batch, the flush boundary, the liveness-only closeout, what a killed run
  still opens and what an interpreter without libzstd writes instead;
  `tests/exporters/test_perfetto_proto.py` checks the field numbers against
  the generated descriptor; `tests/helpers.py` holds the reader every Perfetto
  test reads through, and `tests/test_helpers.py` covers it;
  `tests/benchmarks/test_bench_trace_write.py` measures the write path on the
  CodSpeed job.
