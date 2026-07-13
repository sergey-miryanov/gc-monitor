"""Tests for BufferedTraceExporter."""

from __future__ import annotations

from pathlib import Path

from gcmon.data import GCStatsInfo
from gcmon.exporters._buffered_exporter import _RSS_TID, BufferedTraceExporter
from gcmon.exporters.encoder import JsonEventEncoder
from gcmon.trace_event import CounterEvent, ProcessMeta, ThreadMeta


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
        assert c.tid == _RSS_TID
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
