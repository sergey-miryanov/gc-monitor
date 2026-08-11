"""Tests for BufferedTraceExporter."""

from __future__ import annotations

from pathlib import Path

import pytest

from gcmon.data import GCStatsInfo
from gcmon.exporters._buffered_exporter import BufferedTraceExporter
from gcmon.exporters.chrome_trace_exporter import TraceExporter
from gcmon.exporters.encoder import JsonEventEncoder
from gcmon.exporters.exporter import EventsExporter
from gcmon.exporters.jsonl_exporter import JsonlExporter
from gcmon.exporters.stdout_exporter import StdoutExporter
from gcmon.trace_event import RSS_TID, BeginEvent, CounterEvent, EndEvent, ProcessMeta, ThreadMeta, loss_tid
from tests.data_helpers import create_instant_msg
from tests.helpers import create_mock_loss_item, create_mock_stats_item


class TestBuildMetaGuard:
    """``_build_meta`` with ``iid=-1`` must skip ThreadMeta."""

    def _make_exporter(self, tmp_path: Path) -> BufferedTraceExporter:
        return BufferedTraceExporter(
            JsonEventEncoder(),
            tmp_path / "test.json",
            flush_threshold=1000,
        )

    def test_negative_iid_skips_thread_meta(self, tmp_path: Path) -> None:
        exporter = self._make_exporter(tmp_path)
        exporter.add_rss_sample(100, 4096, 1_000_000)
        assert any(isinstance(e, ProcessMeta) and e.pid == 100 for e in exporter._buffer)
        assert not any(isinstance(e, ThreadMeta) for e in exporter._buffer)

    def test_multiple_rss_samples_no_duplicate_process_meta(self, tmp_path: Path) -> None:
        exporter = self._make_exporter(tmp_path)
        exporter.add_rss_sample(100, 4096, 1_000_000)
        exporter.add_rss_sample(100, 8192, 2_000_000)
        metas = [e for e in exporter._buffer if isinstance(e, ProcessMeta)]
        assert len(metas) == 1

    def test_non_negative_iid_emits_thread_meta(self, tmp_path: Path) -> None:
        exporter = self._make_exporter(tmp_path)
        item = GCStatsInfo(
            gen=0,
            iid=0,
            ts_start=1_000,
            ts_stop=2_000,
            heap_size=1000,
            collections=1,
            collected=10,
            uncollectable=0,
            candidates=5,
            duration=0.001,
        )
        exporter.add_event(100, item)
        assert any(isinstance(e, ThreadMeta) and e.tid == 0 for e in exporter._buffer)


class TestAddRssSample:
    def test_emits_counter_event_with_correct_shape(self, tmp_path: Path) -> None:
        exporter = BufferedTraceExporter(
            JsonEventEncoder(),
            tmp_path / "test.json",
            flush_threshold=1000,
        )
        exporter.add_rss_sample(100, 4096, 1_000_000)
        counters = [e for e in exporter._buffer if isinstance(e, CounterEvent)]
        assert len(counters) == 1
        c = counters[0]
        assert c.pid == 100
        assert c.tid == RSS_TID
        assert c.name == "rss"
        assert c.args == {"rss": 4096}
        assert c.ts == 1_000_000

    def test_multiple_pids_produce_separate_meta(self, tmp_path: Path) -> None:
        exporter = BufferedTraceExporter(
            JsonEventEncoder(),
            tmp_path / "test.json",
            flush_threshold=1000,
        )
        exporter.add_rss_sample(100, 4096, 1_000_000)
        exporter.add_rss_sample(200, 8192, 2_000_000)
        events = exporter._buffer
        pids_in_meta = {e.pid for e in events if isinstance(e, ProcessMeta)}
        assert pids_in_meta == {100, 200}


class TestAddProcessLivenessIsPerfettoOnly:
    """Liveness is a ``Processes``-track concern, so every format but
    Perfetto reaches the base no-op on ``EventsExporter`` and comes out
    byte-identical to a run that never reported any. See ADR-0011."""

    def _write(self, path: Path, exporter: EventsExporter, *, with_liveness: bool) -> bytes:
        exporter.add_event(100, create_mock_stats_item())
        if with_liveness:
            exporter.add_process_liveness({100, 200}, 1_400_000_000)
        exporter.add_instant_event(100, create_instant_msg(name="marker", ts=1_600_000_000))
        if with_liveness:
            exporter.add_process_liveness({100, 200}, 1_800_000_000)
        exporter.close()
        return path.read_bytes()

    def test_chrome_output_is_unchanged(self, tmp_path: Path) -> None:
        quiet = tmp_path / "quiet.json"
        loud = tmp_path / "loud.json"
        assert self._write(quiet, TraceExporter(quiet, flush_threshold=1000), with_liveness=False) == self._write(
            loud, TraceExporter(loud, flush_threshold=1000), with_liveness=True
        )

    def test_jsonl_output_is_unchanged(self, tmp_path: Path) -> None:
        quiet = tmp_path / "quiet.jsonl"
        loud = tmp_path / "loud.jsonl"
        assert self._write(quiet, JsonlExporter(quiet, flush_threshold=1000), with_liveness=False) == self._write(
            loud, JsonlExporter(loud, flush_threshold=1000), with_liveness=True
        )

    def test_stdout_output_is_unchanged(self, capsys: pytest.CaptureFixture[str]) -> None:
        exporter = StdoutExporter(flush_threshold=1000)
        exporter.add_event(100, create_mock_stats_item())
        exporter.close()
        without = capsys.readouterr().out

        exporter = StdoutExporter(flush_threshold=1000)
        exporter.add_event(100, create_mock_stats_item())
        exporter.add_process_liveness({100, 200}, 1_400_000_000)
        exporter.close()
        assert capsys.readouterr().out == without


class TestAddLossEvent:
    def _make_exporter(self, tmp_path: Path) -> BufferedTraceExporter:
        return BufferedTraceExporter(JsonEventEncoder(), tmp_path / "test.json", flush_threshold=1000)

    def test_the_bar_is_the_window(self, tmp_path: Path) -> None:
        """The whole interval gcmon could not observe, not the 200 ns of GC
        known to be somewhere inside it."""
        exporter = self._make_exporter(tmp_path)

        exporter.add_loss_event(
            100, create_mock_loss_item(iid=0, gen=0, ts_start=1_000, ts_stop=2_000, lost_count=1, lost_pause_ns=200)
        )

        begin = next(e for e in exporter._buffer if isinstance(e, BeginEvent))
        end = next(e for e in exporter._buffer if isinstance(e, EndEvent))
        assert (begin.name, begin.ts) == ("GC Loss(0)", 1_000)
        assert end.ts == 2_000

    def test_it_lands_on_the_loss_track(self, tmp_path: Path) -> None:
        exporter = self._make_exporter(tmp_path)

        exporter.add_loss_event(
            100, create_mock_loss_item(iid=1, gen=0, ts_start=1_000, ts_stop=2_000, lost_count=1, lost_pause_ns=200)
        )

        assert {e.tid for e in exporter._buffer if isinstance(e, BeginEvent)} == {loss_tid(1)}

    def test_it_does_not_share_the_track_with_gc_slices(self, tmp_path: Path) -> None:
        """One interpreter, two rows: a reconstructed span is easier to find
        on a row that holds nothing else."""
        exporter = self._make_exporter(tmp_path)

        exporter.add_event(100, create_mock_stats_item(iid=0))
        exporter.add_loss_event(
            100, create_mock_loss_item(iid=0, gen=0, ts_start=1, ts_stop=2, lost_count=1, lost_pause_ns=1)
        )

        assert {e.tid for e in exporter._buffer if isinstance(e, BeginEvent)} == {0, loss_tid(0)}

    def test_the_loss_track_is_not_declared_as_a_thread(self, tmp_path: Path) -> None:
        """The same negative-tid guard RSS relies on. `perfetto_format`
        describes this track off the slices instead."""
        exporter = self._make_exporter(tmp_path)

        exporter.add_loss_event(
            100, create_mock_loss_item(iid=0, gen=0, ts_start=1, ts_stop=2, lost_count=1, lost_pause_ns=1)
        )

        assert any(isinstance(e, ProcessMeta) for e in exporter._buffer)
        assert not any(isinstance(e, ThreadMeta) for e in exporter._buffer)

    def test_two_interpreters_get_two_loss_tracks(self, tmp_path: Path) -> None:
        exporter = self._make_exporter(tmp_path)

        exporter.add_loss_event(
            100, create_mock_loss_item(iid=0, gen=0, ts_start=1, ts_stop=2, lost_count=1, lost_pause_ns=1)
        )
        exporter.add_loss_event(
            100, create_mock_loss_item(iid=1, gen=0, ts_start=1, ts_stop=2, lost_count=1, lost_pause_ns=1)
        )

        assert {e.tid for e in exporter._buffer if isinstance(e, BeginEvent)} == {loss_tid(0), loss_tid(1)}

    def test_the_loss_and_rss_sentinels_do_not_collide(self, tmp_path: Path) -> None:
        exporter = self._make_exporter(tmp_path)

        exporter.add_rss_sample(100, 4096, 1_000)
        exporter.add_loss_event(
            100, create_mock_loss_item(iid=0, gen=0, ts_start=1, ts_stop=2, lost_count=1, lost_pause_ns=1)
        )

        assert loss_tid(0) != RSS_TID
