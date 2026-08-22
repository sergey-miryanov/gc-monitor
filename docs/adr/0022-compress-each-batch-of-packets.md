# ADR-0022: Compress each batch into one `TracePacket.compressed_packets` field

- **Status:** Accepted
- **Date:** 2026-08-22

## Context

A gcmon trace is large and almost entirely repetition: a fixed set of slice names, categories and
annotation keys, written out again for every slice drawn. An operator attaching to a production
process copies all of it off the host, attaches all of it to an issue, and waits while the UI
loads all of it.

Deflate on the same bytes, at the batch granularity gcmon already writes in, takes a capture to
between a sixth and a ninth of its size. Nothing about the trace has to change to get that. The
Perfetto format carries a compressed form and the trace processor reads it.

## Decision

**The wrapper is `TracePacket.compressed_packets`, field 50.** It carries the deflated sequence of
serialized `TracePacket` entries the encoder already builds for a plain trace. The field number
joins `perfetto_proto` as a named `IntEnum` member checked against the generated descriptor, under
[ADR-0001](0001-hand-rolled-perfetto-protobuf-encoder.md).

The file keeps its extension, its magic and its identity. An operator cannot tell a compressed
trace from a plain one without a hex editor. There is nothing to run before opening it.

The wrapper carries that one field and no `trusted_packet_sequence_id`; ADR-0001's rule is scoped
to the packets a batch holds.

A file may hold both compressed and plain packets, and a reader accepts the mixture. gcmon writes
only the compressed form.

**One compressed packet per batch.** The compression boundary is the flush boundary. No state is
held across flushes, and no bytes a caller asked to persist are left unwritten when the write
returns. A coarser boundary compresses better, by a few percent on a large capture, and widens
what a killed run loses.

**Deflate level 6, and no flag.** Level 9 buys about a percent of size for about half again the
CPU. Level 1 gives up about a sixth of the size for CPU that is not contended.
[ADR-0021](0021-write-one-trace-format.md) rejected `--input-format` on the ground that a question
with one real answer is not a question, and that a second encoding has to be kept alive in order
to be tested. Both hold here.

## Consequences

- The trace an operator copies off a host is roughly eight times smaller, on every run, with
  nothing to set and no pipeline to edit.
- **A killed run loses at most one batch.** The file opens, and yields the batches that
  completed, with their names, categories and args intact. The kill window was one packet before
  this and is one batch now.
- **A truncated trace still says nothing about what it lost.** The `stats` table on a short file
  reports no error row and no data-loss counter, on this encoding and on the plain one alike.
  This record does not fix it.
- **The trace stops being byte-reproducible across machines.** Deflate output depends on the zlib
  build, and the three CI legs do not agree on bytes for the same input. Two runs on one machine
  still produce identical files. **No test may pin compressed bytes, or assert a size or a
  ratio**: a bound tight enough to catch "compression stopped working" is a bound loose enough to
  be red on somebody's laptop. The guard is that the wrapper is there and inflates to the
  packets.
- **The trace stops being greppable.** `strings gcmon.pftrace` says nothing after this. It was
  never a documented property.
- Compression is invisible above the file, so a test that hands a path to the trace processor is
  unaffected. One reader inflates a wrapper for the tests that parse a trace themselves.
- `perfetto` stays a development dependency. The deflate is stdlib `zlib`.

## Alternatives considered

- **Gzip the whole file.** `gzip.open` in place of `open` is a smaller change and lands within a
  percent of the same size. Rejected: a truncated `.gz` is refused outright and recovers nothing,
  and a run that was killed is exactly when an operator most wants what was captured. The bytes
  are not lost, but recovering them is a repair script an operator does not have, on a file that
  reports itself corrupt.
- **One gzip member per flush.** Reads correctly whole and fails identically when truncated.
  Rejected: it costs the "it is just a gzip file" simplicity and buys nothing.
- **Zstd.** Perfetto v58 added `TracePacket.zstd_compressed_packets`, field 133, and it
  compresses a trace better than deflate for less CPU. Rejected on what a reader that predates it
  does: field 133 is a field a pre-v58 trace processor does not know, so it skips it, loads a
  trace with no packets and reports no error. The trace opens, shows nothing, and only a human
  looking at an empty timeline notices, which is the failure mode
  [ADR-0001](0001-hand-rolled-perfetto-protobuf-encoder.md) exists to keep out. No file degrades
  gracefully either: writing both fields draws every slice twice on a reader that knows each.
  Deflate is read by both. [Spec 0058](../../specs/0058-write-the-batches-with-zstd.md) carries
  the switch and what it waits on.
- **A `--compress` or `--compress-level` flag.** Rejected on ADR-0021's ground, above.
- **A minimum size below which a batch is written plain.** A one-packet batch comes out slightly
  larger than it went in, and a batch of ten is already well under half. Rejected: the branch
  costs more in untested code than it saves in bytes.

## Implementation

- `src/gcmon/exporters/encoder.py` holds the one write path both a flush and the closeout go
  through, and the deflate level.
- `src/gcmon/exporters/perfetto_proto.py` holds `TracePacketField.COMPRESSED_PACKETS`.
- Tests: `tests/exporters/test_perfetto_compression.py` covers the wrapper, the batch boundary,
  the liveness-only closeout and what a killed run still opens;
  `tests/exporters/test_perfetto_proto.py` checks the field number against the generated
  descriptor; `tests/helpers.py` holds the reader every Perfetto test reads through, and
  `tests/test_helpers.py` covers it; `tests/benchmarks/test_bench_trace_write.py` measures the
  write path on the CodSpeed job.
