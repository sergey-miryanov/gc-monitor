"""Tests for CombinedTraceExporter and derive_combined_paths."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest

from gcmon.exporters.chrome_trace_exporter import TraceExporter
from gcmon.exporters.combined_exporter import (
    CombinedTraceExporter,
    derive_combined_paths,
)
from gcmon.exporters.perfetto_exporter import PerfettoExporter
from tests.conftest import DEFAULT_PID
from tests.data_helpers import create_instant_msg
from tests.helpers import (
    assert_valid_chrome_trace_format,
    create_mock_incremental_item,
    create_mock_stats_item,
)


class TestDeriveCombinedPaths:
    def test_default(self, tmp_path: Path) -> None:
        chrome, perfetto = derive_combined_paths(tmp_path / "trace")
        assert chrome == tmp_path / "trace.json"
        assert perfetto == tmp_path / "trace.pftrace"

    def test_strips_json_extension(self, tmp_path: Path) -> None:
        chrome, perfetto = derive_combined_paths(tmp_path / "trace.json")
        assert chrome == tmp_path / "trace.json"
        assert perfetto == tmp_path / "trace.pftrace"

    def test_strips_pftrace_extension(self, tmp_path: Path) -> None:
        chrome, perfetto = derive_combined_paths(tmp_path / "trace.pftrace")
        assert chrome == tmp_path / "trace.json"
        assert perfetto == tmp_path / "trace.pftrace"

    def test_strips_unrelated_extension(self, tmp_path: Path) -> None:
        chrome, perfetto = derive_combined_paths(tmp_path / "trace.foo")
        assert chrome == tmp_path / "trace.json"
        assert perfetto == tmp_path / "trace.pftrace"

    def test_preserves_parent_directory(self, tmp_path: Path) -> None:
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        chrome, perfetto = derive_combined_paths(out_dir / "gcmon")
        assert chrome == out_dir / "gcmon.json"
        assert perfetto == out_dir / "gcmon.pftrace"

    def test_no_extension_stem(self, tmp_path: Path) -> None:
        chrome, perfetto = derive_combined_paths(tmp_path / "trace")
        assert chrome.suffix == ".json"
        assert perfetto.suffix == ".pftrace"


class TestCombinedTraceExporter:
    def test_add_event_forwards_to_both(self) -> None:
        chrome = Mock()
        perfetto = Mock()
        combined = CombinedTraceExporter(chrome=chrome, perfetto=perfetto)
        item = create_mock_stats_item()
        combined.add_event(DEFAULT_PID, item)
        chrome.add_event.assert_called_once_with(DEFAULT_PID, item)
        perfetto.add_event.assert_called_once_with(DEFAULT_PID, item)

    def test_add_instant_event_forwards_to_both(self) -> None:
        chrome = Mock()
        perfetto = Mock()
        combined = CombinedTraceExporter(chrome=chrome, perfetto=perfetto)
        msg = create_instant_msg(name="test-event")
        combined.add_instant_event(DEFAULT_PID, msg)
        chrome.add_instant_event.assert_called_once_with(DEFAULT_PID, msg)
        perfetto.add_instant_event.assert_called_once_with(DEFAULT_PID, msg)

    def test_add_rss_sample_forwards_to_both(self) -> None:
        chrome = Mock()
        perfetto = Mock()
        combined = CombinedTraceExporter(chrome=chrome, perfetto=perfetto)
        combined.add_rss_sample(DEFAULT_PID, 4096, 1_000_000)
        chrome.add_rss_sample.assert_called_once_with(DEFAULT_PID, 4096, 1_000_000)
        perfetto.add_rss_sample.assert_called_once_with(DEFAULT_PID, 4096, 1_000_000)

    def test_add_process_liveness_forwards_to_both(self) -> None:
        """The chrome half reaches the base no-op, but the fan-out still
        has to happen or ``--format chrome+perfetto`` would silently
        lose liveness from the perfetto file."""
        chrome = Mock()
        perfetto = Mock()
        combined = CombinedTraceExporter(chrome=chrome, perfetto=perfetto)
        combined.add_process_liveness({DEFAULT_PID, 999}, 1_000_000)
        chrome.add_process_liveness.assert_called_once_with({DEFAULT_PID, 999}, 1_000_000)
        perfetto.add_process_liveness.assert_called_once_with({DEFAULT_PID, 999}, 1_000_000)

    def test_close_calls_both_subexporters(self) -> None:
        chrome = Mock()
        perfetto = Mock()
        combined = CombinedTraceExporter(chrome=chrome, perfetto=perfetto)
        combined.close()
        chrome.close.assert_called_once_with()
        perfetto.close.assert_called_once_with()

    def test_close_continues_after_chrome_failure(self) -> None:
        chrome = Mock()
        chrome.close.side_effect = RuntimeError("chrome boom")
        perfetto = Mock()
        combined = CombinedTraceExporter(chrome=chrome, perfetto=perfetto)
        with pytest.raises(RuntimeError, match="chrome boom"):
            combined.close()
        chrome.close.assert_called_once_with()
        perfetto.close.assert_called_once_with()

    def test_chrome_path_property(self, tmp_path: Path) -> None:
        chrome = TraceExporter(tmp_path / "standalone.json", flush_threshold=100)
        perfetto = PerfettoExporter(tmp_path / "standalone.pftrace", flush_threshold=100)
        combined = CombinedTraceExporter(chrome=chrome, perfetto=perfetto)
        assert combined.chrome_path == tmp_path / "standalone.json"
        assert combined.perfetto_path == tmp_path / "standalone.pftrace"

    def test_meta_dedup_per_file(self, tmp_path: Path) -> None:
        """ProcessMeta/ThreadMeta are emitted exactly once per (pid, iid) in
        each output file (the dedup is per-sub-exporter, not shared)."""
        chrome = TraceExporter(tmp_path / "trace.json", flush_threshold=100)
        perfetto = PerfettoExporter(tmp_path / "trace.pftrace", flush_threshold=100)
        combined = CombinedTraceExporter(chrome=chrome, perfetto=perfetto)

        item = create_mock_stats_item(gen=0, iid=0)
        combined.add_event(DEFAULT_PID, item)
        combined.add_event(DEFAULT_PID, item)
        combined.add_event(DEFAULT_PID, item)
        combined.close()

        data = assert_valid_chrome_trace_format(tmp_path / "trace.json")
        process_metas = [e for e in data if e["ph"] == "M" and e["name"] == "process_name"]
        thread_metas = [e for e in data if e["ph"] == "M" and e["name"] == "thread_name"]
        assert len(process_metas) == 1
        assert isinstance(process_metas[0]["args"], dict)
        assert process_metas[0]["args"]["name"] == f"Process {DEFAULT_PID}"
        assert len(thread_metas) == 1
        assert isinstance(thread_metas[0]["args"], dict)
        assert thread_metas[0]["args"]["name"] == f"Thread {item.iid}"

        pf_bytes = (tmp_path / "trace.pftrace").read_bytes()
        assert len(pf_bytes) > 0

    def test_rss_sample_reaches_both_output_files(self, tmp_path: Path) -> None:
        """An RSS sample emitted through the combined exporter lands in the
        Chrome JSON as a counter event and in the Perfetto file."""
        chrome = TraceExporter(tmp_path / "trace.json", flush_threshold=100)
        perfetto = PerfettoExporter(tmp_path / "trace.pftrace", flush_threshold=100)
        combined = CombinedTraceExporter(chrome=chrome, perfetto=perfetto)

        combined.add_event(DEFAULT_PID, create_mock_stats_item(gen=0, iid=0))
        combined.add_rss_sample(DEFAULT_PID, 4096, 1_000_000)
        combined.close()

        data = assert_valid_chrome_trace_format(tmp_path / "trace.json")
        rss_counters = []
        for event in data:
            args = event.get("args")
            if event["ph"] == "C" and isinstance(args, dict) and args.get("rss") == 4096:
                rss_counters.append(event)
        assert len(rss_counters) == 1
        assert rss_counters[0]["pid"] == DEFAULT_PID

        assert (tmp_path / "trace.pftrace").stat().st_size > 0

    def test_real_subexporters_produce_both_files(self, tmp_path: Path) -> None:
        """End-to-end: real TraceExporter + real PerfettoExporter as sub-exporters
        produce both files with non-zero content."""
        chrome = TraceExporter(tmp_path / "trace.json", flush_threshold=100)
        perfetto = PerfettoExporter(tmp_path / "trace.pftrace", flush_threshold=100)
        combined = CombinedTraceExporter(chrome=chrome, perfetto=perfetto)

        items = [
            create_mock_stats_item(gen=0, iid=0, ts_start=1_500_000_000, ts_stop=1_505_000_000),
            create_mock_incremental_item(gen=1, iid=0, ts_start=1_600_000_000, ts_stop=1_605_000_000),
            create_mock_stats_item(gen=2, iid=1, ts_start=1_700_000_000, ts_stop=1_705_000_000),
        ]
        for item in items:
            combined.add_event(DEFAULT_PID, item)
        combined.close()

        assert (tmp_path / "trace.json").exists()
        assert (tmp_path / "trace.json").stat().st_size > 0
        assert (tmp_path / "trace.pftrace").exists()
        assert (tmp_path / "trace.pftrace").stat().st_size > 0
