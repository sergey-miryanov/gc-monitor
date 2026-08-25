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

**A batch goes out as one `TracePacket.zstd_compressed_packets`, field 133.**
It holds the serialized `TracePacket` entries the encoder already builds,
compressed. The field number joins `perfetto_proto` as a named `IntEnum`
member checked against the generated descriptor, under
[ADR-0001](0001-hand-rolled-perfetto-protobuf-encoder.md).

The file keeps its `.pftrace` extension and still opens by being dropped on
the Perfetto UI. An operator cannot tell a compressed trace from a plain one
without a hex editor.

A compressed batch carries that one field and no `trusted_packet_sequence_id`
(ADR-0001).

A file may hold plain packets and either compressed form, and a reader accepts
the mixture. gcmon writes only field 133.

**The reader keeps the deflated field, 50.** Not for an archive: compression
had not shipped when the codec changed, so no capture in anyone's hands is
deflated. What the branch guards is the suite, the only thing here that reads
a trace back, and the deflate Perfetto's own tooling writes.

**The compression boundary is the flush boundary.** A batch reaches the file
before the write returns, and nothing is held back for the next one. A coarser
boundary compresses a few percent better and widens what a killed run loses.

**Level 3, zstd's own default, and no flag.** Level 9 costs more CPU than the
deflate it replaces and buys a few percent. Level 19 is orders of magnitude
slower and cannot run on a flush.

**Deflate, field 50, where `compression.zstd` is missing.** It is an optional
part of a CPython build, and gcmon writes a trace on an interpreter that lacks
it rather than refusing to. The codec is resolved once at import from what the
interpreter has, never from a flag or an option, so no run picks it and no
file records which was used. The fallback file is the more portable one: it
opens on any Perfetto.

## Consequences

- The trace an operator copies off a host is a fraction of the size, with
  nothing to set and no pipeline to edit.
- **A Perfetto older than v58 shows an empty timeline.** It does not know
  field 133, so it skips it, loads a trace with no packets and reports no
  error: only a human looking at the empty timeline notices. That is the
  failure mode [ADR-0001](0001-hand-rolled-perfetto-protobuf-encoder.md)
  exists to keep out, taken here in exchange for the size and the CPU. gcmon
  cannot warn, because it does not know what will open the file, so the
  minimum version is documented and nothing else.
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
  not read field 133. Left to the package the suite would load every batch as
  an empty trace and report no error, which is the one failure this encoding
  has.
- `perfetto` stays a development dependency, and the codec comes from the
  stdlib's `compression.zstd`.
- **The trace format depends on the interpreter that wrote it**, which is the
  price of the fallback. Two people on one gcmon version can produce different
  files, and nothing in either says which codec wrote it: reading it back is
  what tells them apart, and the reader takes both. Accepted because gcmon is
  installed beside the process it watches
  ([ADR-0001](0001-hand-rolled-perfetto-protobuf-encoder.md)), which is where
  a minimal build turns up, and refusing to write a trace there is worse than
  writing the older encoding.
- **Only a zstd trace needs Perfetto v58.** The requirement documented for
  operators is the one the common build produces; a fallback capture opens
  anywhere, and the empty timeline cannot happen for it.

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
- **Deflate, field 50, which every Perfetto generation reads.** It was the
  decision here, and what held it was the empty timeline an older reader
  draws, never the codec: zstd is smaller than deflate 6 on both captures in
  this repo and costs a fraction of the CPU on each. Rejected once v58.2
  shipped a trace processor that reads field 133 and the suite could pin one.
- **Writing both fields, so that an old reader takes 50 and a new one takes
  133.** Rejected: a reader that knows both expands both and draws every slice
  twice. Perfetto's advice to set both codecs is about a service choosing one,
  not about a file carrying two.
- **Keeping deflate behind a flag for old readers.** Rejected on the ground
  below, and both encodings would have to be kept working.
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
  closeout share, the compression level and the import that resolves the
  codec.
- `src/gcmon/exporters/perfetto_proto.py` holds
  `TracePacketField.ZSTD_COMPRESSED_PACKETS`. `COMPRESSED_PACKETS` sits beside
  it with no caller left: the reader matches the descriptor's field names, not
  these constants (ADR-0001), so the check against the descriptor is the whole
  of what field 50's member still does.
- `tests/perfetto_prebuilt.py` pins the trace processor the suite drives, to a
  build that reads field 133.
- Tests: `tests/exporters/test_perfetto_compression.py` covers the compressed
  batch, the flush boundary, the liveness-only closeout, what a killed run
  still opens and what a child interpreter without libzstd writes instead;
  `tests/exporters/test_perfetto_proto.py` checks the field numbers against
  the generated descriptor; `tests/helpers.py` holds the reader every Perfetto
  test reads through, and `tests/test_helpers.py` covers it;
  `tests/benchmarks/test_bench_trace_write.py` measures the write path on the
  CodSpeed job.
