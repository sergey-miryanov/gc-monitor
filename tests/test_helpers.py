"""Tests for the shared test helpers in ``tests/helpers.py``.

Only ``perfetto_packets`` is covered: it is the one helper carrying logic of
its own rather than a factory or an assertion, and every Perfetto test in the
suite reads its traces through it.
"""

from __future__ import annotations

import zlib
from compression import zstd
from pathlib import Path

import pytest
from perfetto.protos.perfetto.trace.perfetto_trace_pb2 import Trace, TracePacket

from tests.helpers import assert_valid_perfetto_trace, perfetto_packets


def _plain(timestamps: list[int]) -> bytes:
    """A trace carrying one packet per timestamp, written plain."""
    content: bytes = Trace(packet=[TracePacket(timestamp=ts) for ts in timestamps]).SerializeToString()
    return content


def _compressed(timestamps: list[int]) -> bytes:
    """The same packets, deflated into one compressed batch."""
    batch = TracePacket(compressed_packets=zlib.compress(_plain(timestamps)))
    content: bytes = Trace(packet=[batch]).SerializeToString()
    return content


def _zstd(timestamps: list[int]) -> bytes:
    """The same packets, compressed with zstd into one batch."""
    batch = TracePacket(zstd_compressed_packets=zstd.compress(_plain(timestamps)))
    content: bytes = Trace(packet=[batch]).SerializeToString()
    return content


class TestPerfettoPackets:
    def test_a_plain_trace_reads(self) -> None:
        assert [p.timestamp for p in perfetto_packets(_plain([1, 2, 3]))] == [1, 2, 3]

    def test_a_compressed_trace_reads_as_the_plain_one(self) -> None:
        timestamps = [10, 20, 30]

        assert perfetto_packets(_compressed(timestamps)) == perfetto_packets(_plain(timestamps))

    def test_a_zstd_trace_reads_as_the_plain_one(self) -> None:
        timestamps = [10, 20, 30]

        assert perfetto_packets(_zstd(timestamps)) == perfetto_packets(_plain(timestamps))

    def test_a_compressed_batch_is_flattened_where_it_stood(self) -> None:
        """The format allows a file to mix the two encodings, so order is by
        position in the file and not by encoding."""
        content = _plain([1]) + _compressed([2, 3]) + _zstd([4, 5]) + _plain([6])

        assert [p.timestamp for p in perfetto_packets(content)] == [1, 2, 3, 4, 5, 6]

    def test_several_batches_read_in_file_order(self) -> None:
        """What a run flushed over more than one batch leaves behind."""
        content = _zstd([1, 2]) + _zstd([3, 4])

        assert [p.timestamp for p in perfetto_packets(content)] == [1, 2, 3, 4]

    def test_an_empty_trace_carries_no_packets(self) -> None:
        assert perfetto_packets(b"") == []


class TestAssertValidPerfettoTrace:
    def test_a_compressed_file_validates(self, tmp_path: Path) -> None:
        path = tmp_path / "gcmon.pftrace"
        path.write_bytes(_compressed([1, 2]))

        assert [p.timestamp for p in assert_valid_perfetto_trace(path)] == [1, 2]

    def test_a_missing_file_fails(self, tmp_path: Path) -> None:
        with pytest.raises(AssertionError, match="does not exist"):
            assert_valid_perfetto_trace(tmp_path / "absent.pftrace")

    def test_an_empty_file_fails(self, tmp_path: Path) -> None:
        path = tmp_path / "empty.pftrace"
        path.write_bytes(b"")

        with pytest.raises(AssertionError, match="is empty"):
            assert_valid_perfetto_trace(path)

    def test_a_file_carrying_no_packets_fails(self, tmp_path: Path) -> None:
        """Well-formed protobuf holding one field ``Trace`` does not know, so
        the file is neither empty nor a trace with packets in it."""
        path = tmp_path / "packetless.pftrace"
        path.write_bytes(bytes([0xB8, 0x3E, 0x01]))

        with pytest.raises(AssertionError, match="carries no packets"):
            assert_valid_perfetto_trace(path)
