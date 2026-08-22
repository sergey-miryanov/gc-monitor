"""What a compressed trace carries, and what it must still mean.

Every other Perfetto test reads through ``perfetto_packets``, which inflates a
batch without saying so. These are the tests that look at the compressed batch itself.

Nothing here asserts a size or a ratio; ADR-0022 says why.
"""

from __future__ import annotations

import zlib
from collections.abc import Iterator
from pathlib import Path

import pytest
from perfetto.protos.perfetto.trace.perfetto_trace_pb2 import Trace, TracePacket
from perfetto.trace_processor import TraceProcessor, TraceProcessorConfig

from gcmon.exporters import PerfettoExporter
from gcmon.exporters.perfetto_proto import TraceField
from gcmon.exporters.protobuf_encoder import encode_bytes_field
from tests.helpers import (
    assert_valid_perfetto_trace,
    create_mock_loss_item,
    create_mock_stats_item,
    perfetto_packets,
)

_PID: int = 4242
_PAUSE_NAME: str = "GC Pause(0)"
_LOSS_CATEGORY: str = "gc.loss"
_PAUSE_CATEGORY: str = "gc.pause(gen=0)"
_COUNTER_NAME: str = "G0 collected"
_COLLECTED: int = 17

# One event per flush, so a run of three writes a trace of three batches
# plus the one ``close()`` writes.
_EVENTS: int = 3
_BATCHES: int = _EVENTS + 1

# A killed run: six collections, each its own batch, cut halfway through
# the fifth. Four batches completed, and the fifth and the closeout did not.
_KILLED_EVENTS: int = 6
_SURVIVING_BATCHES: int = 4

# What the trace processor would raise if it noticed a gcmon trace was
# short: the two track-event counters everything gcmon draws goes through,
# and the one packet-loss counter that is not tied to another data source.
_LOSS_STATS: tuple[str, ...] = (
    "track_event_parser_errors",
    "track_event_tokenizer_errors",
    "clock_sync_failure_undeferrable_packet_loss",
)


def _write_trace(path: Path) -> Path:
    """A run flushed once per event, so its trace spans several batches.

    The last batch is a loss record rather than a collection, so the two kinds
    of slice gcmon draws are split across different batches.
    """
    exporter = PerfettoExporter(
        output_path=path,
        flush_threshold=1,
        cmdline_provider=lambda _pid: None,
    )
    for i in range(_EVENTS - 1):
        exporter.add_event(
            _PID,
            create_mock_stats_item(
                ts_start=1_000_000 * (i + 1),
                ts_stop=1_000_000 * (i + 1) + 500_000,
                collected=_COLLECTED,
            ),
        )
    exporter.add_loss_event(_PID, create_mock_loss_item(ts_start=9_000_000, ts_stop=10_000_000))
    exporter.close()
    return path


def _write_liveness_only_trace(path: Path) -> Path:
    """A run whose target answered every poll and never collected: the only
    path ``finalize_perfetto_packets`` owns alone."""
    exporter = PerfettoExporter(
        output_path=path,
        flush_threshold=100,
        cmdline_provider=lambda _pid: None,
    )
    exporter.add_process_liveness({_PID}, 1_000_000)
    exporter.add_process_liveness({_PID}, 5_000_000)
    exporter.close()
    return path


def _compressed_batches(path: Path) -> list[TracePacket]:
    """The packets as the file carries them, left closed."""
    trace = Trace()
    trace.ParseFromString(path.read_bytes())
    return list(trace.packet)


@pytest.fixture
def trace_processor(tmp_path: Path) -> Iterator[TraceProcessor]:
    path = _write_trace(tmp_path / "batched.pftrace")
    tp = TraceProcessor(trace=str(path), config=TraceProcessorConfig(load_timeout=300))
    try:
        yield tp
    finally:
        tp.close()


class TestTheCompressedBatch:
    """The whole guard between "compressed" and "silently not compressed"."""

    def test_every_packet_in_the_file_is_a_compressed_batch(self, tmp_path: Path) -> None:
        path = _write_trace(tmp_path / "batched.pftrace")

        assert [p.HasField("compressed_packets") for p in _compressed_batches(path)] == [True] * _BATCHES

    def test_one_compressed_batch_per_flush(self, tmp_path: Path) -> None:
        """Each flush, and the closeout ``close()`` writes, leaves one behind."""
        path = _write_trace(tmp_path / "batched.pftrace")

        assert len(_compressed_batches(path)) == _BATCHES

    def test_inflating_the_batches_yields_the_packets(self, tmp_path: Path) -> None:
        path = _write_trace(tmp_path / "batched.pftrace")

        inflated: list[TracePacket] = []
        for batch in _compressed_batches(path):
            inflated.extend(perfetto_packets(zlib.decompress(batch.compressed_packets)))

        assert inflated == assert_valid_perfetto_trace(path)

    def test_a_run_that_never_collected_is_compressed_too(self, tmp_path: Path) -> None:
        path = _write_liveness_only_trace(tmp_path / "liveness_only.pftrace")

        assert [p.HasField("compressed_packets") for p in _compressed_batches(path)] == [True]
        assert assert_valid_perfetto_trace(path)


class TestTheTraceStillMeansWhatItMeant:
    """A trace split over several batches reads as one trace."""

    def test_the_slice_names_resolve(self, trace_processor: TraceProcessor) -> None:
        names = {row.name for row in trace_processor.query("SELECT name FROM slice")}

        assert _PAUSE_NAME in names

    def test_the_categories_resolve(self, trace_processor: TraceProcessor) -> None:
        categories = {
            str(row.category) for row in trace_processor.query("SELECT category FROM slice WHERE category IS NOT NULL")
        }

        assert categories == {_LOSS_CATEGORY, _PAUSE_CATEGORY}

    def test_the_slice_args_resolve(self, trace_processor: TraceProcessor) -> None:
        rows = list(
            trace_processor.query(
                "SELECT a.int_value AS value FROM slice s "
                "JOIN args a ON s.arg_set_id = a.arg_set_id "
                f"WHERE s.name = '{_PAUSE_NAME}' AND a.key = 'debug.collected'"
            )
        )

        assert {row.value for row in rows} == {_COLLECTED}

    def test_the_counter_tracks_resolve(self, trace_processor: TraceProcessor) -> None:
        names = {row.name for row in trace_processor.query("SELECT name FROM counter_track")}

        assert _COUNTER_NAME in names

    def test_the_counter_values_resolve(self, trace_processor: TraceProcessor) -> None:
        rows = list(
            trace_processor.query(
                "SELECT c.value AS value FROM counter c "
                "JOIN counter_track t ON c.track_id = t.id "
                f"WHERE t.name = '{_COUNTER_NAME}'"
            )
        )

        assert rows
        assert {row.value for row in rows} == {float(_COLLECTED)}


def _write_pauses(path: Path, count: int) -> Path:
    """A run of *count* collections, flushed one per batch."""
    exporter = PerfettoExporter(
        output_path=path,
        flush_threshold=1,
        cmdline_provider=lambda _pid: None,
    )
    for i in range(count):
        exporter.add_event(
            _PID,
            create_mock_stats_item(
                ts_start=1_000_000 * (i + 1),
                ts_stop=1_000_000 * (i + 1) + 100_000,
                collected=_COLLECTED,
            ),
        )
    exporter.close()
    return path


def _framed(path: Path) -> list[bytes]:
    """The file cut back into the batches it was written in, so that an offset
    taken from them is one the writer wrote at."""
    raw = path.read_bytes()
    pieces = [
        encode_bytes_field(
            TraceField.PACKET,
            TracePacket(compressed_packets=batch.compressed_packets).SerializeToString(),
        )
        for batch in _compressed_batches(path)
    ]
    assert b"".join(pieces) == raw
    return pieces


def _kill(path: Path, after: int) -> Path:
    """Cut *path* halfway through the batch following the first *after*, the
    way a killed run leaves a file that was mid-write."""
    pieces = _framed(path)
    cut = sum(len(piece) for piece in pieces[:after]) + len(pieces[after]) // 2
    killed = path.with_name("killed.pftrace")
    killed.write_bytes(path.read_bytes()[:cut])
    return killed


def _pause_timestamps(path: Path) -> list[int]:
    tp = TraceProcessor(trace=str(path), config=TraceProcessorConfig(load_timeout=300))
    try:
        return [int(row.ts) for row in tp.query(f"SELECT ts FROM slice WHERE name = '{_PAUSE_NAME}' ORDER BY ts")]
    finally:
        tp.close()


class TestAKilledRun:
    """Why a compressed batch and not a gzipped file (ADR-0022): the file opens, and
    the kill window is one batch."""

    def test_a_truncated_trace_opens_and_yields_the_batches_that_completed(self, tmp_path: Path) -> None:
        whole = _write_pauses(tmp_path / "whole.pftrace", _KILLED_EVENTS)
        killed = _kill(whole, _SURVIVING_BATCHES)

        assert _pause_timestamps(killed) == _pause_timestamps(whole)[:_SURVIVING_BATCHES]

    def test_the_slices_that_survived_still_carry_their_args(self, tmp_path: Path) -> None:
        """Recovered is not the same as readable: a batch that completed has
        to resolve the way it would have in a file that was never cut."""
        killed = _kill(_write_pauses(tmp_path / "whole.pftrace", _KILLED_EVENTS), _SURVIVING_BATCHES)

        tp = TraceProcessor(trace=str(killed), config=TraceProcessorConfig(load_timeout=300))
        try:
            rows = list(
                tp.query(
                    "SELECT s.category AS category, a.int_value AS collected FROM slice s "
                    "JOIN args a ON s.arg_set_id = a.arg_set_id "
                    f"WHERE s.name = '{_PAUSE_NAME}' AND a.key = 'debug.collected'"
                )
            )
        finally:
            tp.close()

        assert len(rows) == _SURVIVING_BATCHES
        assert {(str(row.category), int(row.collected)) for row in rows} == {(_PAUSE_CATEGORY, _COLLECTED)}

    def test_a_truncated_trace_says_nothing_about_what_it_lost(self, tmp_path: Path) -> None:
        """Recorded, not endorsed: a short file looks complete on this
        encoding and on the plain one alike (ADR-0022).

        Asking for each counter by name turns a rename upstream into a
        missing row. A query over every non-info stat would redden on an
        unrelated counter and read as though truncation had started being
        reported.
        """
        killed = _kill(_write_pauses(tmp_path / "whole.pftrace", _KILLED_EVENTS), _SURVIVING_BATCHES)

        tp = TraceProcessor(trace=str(killed), config=TraceProcessorConfig(load_timeout=300))
        try:
            named = ", ".join(f"'{name}'" for name in _LOSS_STATS)
            raised = {
                str(row.name): int(row.value)
                for row in tp.query(f"SELECT name, value FROM stats WHERE name IN ({named})")
            }
        finally:
            tp.close()

        assert raised == dict.fromkeys(_LOSS_STATS, 0)
