"""Integration tests for ``CombinedTraceExporter`` that drive the real
``perfetto.trace_processor`` binary.

Verifies that running the same input through a combined ``(TraceExporter,
PerfettoExporter)`` exporter produces the same SQL-visible content as
running each sub-exporter independently.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from perfetto.trace_processor import TraceProcessor, TraceProcessorConfig

from gcmon.exporters import PerfettoExporter, TraceExporter
from gcmon.exporters.combined_exporter import CombinedTraceExporter
from tests.conftest import DEFAULT_PID
from tests.data_helpers import create_instant_msg
from tests.helpers import create_mock_incremental_item, create_mock_stats_item

_PID_A: int = DEFAULT_PID
_PID_B: int = 67890
_IID_A1: int = 0
_IID_A2: int = 1
_IID_B1: int = 0
_TS_START: int = 1_500_000_000
_TS_DURATION_NS: int = 5_000_000

_FAKE_CMDLINE: tuple[str, ...] = ("python3", "-m", "fake_target")


def _fake_cmdline_provider(pid: int) -> list[str] | None:
    """Returns a known fake cmdline for the two PIDs the trace uses and
    ``None`` for any other PID. Avoids the encoder's psutil lookup, which
    would fail for non-existent PIDs in tests."""
    if pid in (_PID_A, _PID_B):
        return list(_FAKE_CMDLINE)
    return None


def _multi_dimensional_items() -> list:
    """Build a deterministic input exercising multiple pids, generations, iids."""
    return [
        create_mock_stats_item(
            gen=0,
            iid=_IID_A1,
            ts_start=_TS_START,
            ts_stop=_TS_START + _TS_DURATION_NS,
        ),
        create_mock_incremental_item(
            gen=1,
            iid=_IID_A2,
            ts_start=_TS_START + 100_000_000,
            ts_stop=_TS_START + 100_000_000 + _TS_DURATION_NS,
        ),
        create_mock_stats_item(
            gen=0,
            iid=_IID_B1,
            ts_start=_TS_START + 200_000_000,
            ts_stop=_TS_START + 200_000_000 + _TS_DURATION_NS,
        ),
    ]


def _feed(
    items: list,
    pids: list[int],
    chrome: TraceExporter,
    perfetto: PerfettoExporter,
    instant_pid: int,
) -> None:
    """Add the same items + an instant event to both exporters."""
    instant = create_instant_msg(name="start", ts=_TS_START - 1_000_000)
    for item, pid in zip(items, pids, strict=True):
        chrome.add_event(pid, item)
        perfetto.add_event(pid, item)
    chrome.add_instant_event(instant_pid, instant)
    perfetto.add_instant_event(instant_pid, instant)
    chrome.close()
    perfetto.close()


def _feed_combined(
    items: list,
    pids: list[int],
    combined: CombinedTraceExporter,
    instant_pid: int,
) -> None:
    """Add the same items + an instant event to a combined exporter.

    Uses the public ``add_event`` / ``add_instant_event`` / ``close`` API so
    the test exercises the wrapper, not its private sub-exporter attributes.
    """
    instant = create_instant_msg(name="start", ts=_TS_START - 1_000_000)
    for item, pid in zip(items, pids, strict=True):
        combined.add_event(pid, item)
    combined.add_instant_event(instant_pid, instant)
    combined.close()


@contextmanager
def _open_trace(path: Path) -> Iterator[TraceProcessor]:
    tp = TraceProcessor(
        trace=str(path),
        config=TraceProcessorConfig(load_timeout=300),
    )
    try:
        yield tp
    finally:
        tp.close()


def _assert_row_sets_equal(
    standalone: list[tuple],
    combined: list[tuple],
    label: str,
) -> None:
    assert set(standalone) == set(combined), (
        f"{label} differs:\n"
        f"  only standalone ({len(set(standalone) - set(combined))}): "
        f"{sorted(set(standalone) - set(combined))[:5]}\n"
        f"  only combined ({len(set(combined) - set(standalone))}): "
        f"{sorted(set(combined) - set(standalone))[:5]}"
    )


class TestCombinedExporterEquivalenceIntegration:
    """The combined exporter's two output files must be content-equivalent
    to running the corresponding sub-exporters independently on the same
    input. Verified by loading all four files into ``perfetto.trace_processor``
    and comparing the SQL-visible row sets for slices, args, and counter data."""

    def test_equivalence(self, tmp_path: Path) -> None:
        items = _multi_dimensional_items()
        pids = [_PID_A, _PID_A, _PID_B]
        instant_pid = _PID_A

        standalone_dir = tmp_path / "standalone"
        standalone_dir.mkdir()
        combined_dir = tmp_path / "combined"
        combined_dir.mkdir()

        # Standalone exporters.
        standalone_chrome = TraceExporter(
            standalone_dir / "trace.json",
            flush_threshold=100,
        )
        standalone_perfetto = PerfettoExporter(
            standalone_dir / "trace.pftrace",
            flush_threshold=100,
            cmdline_provider=_fake_cmdline_provider,
        )
        _feed(items, pids, standalone_chrome, standalone_perfetto, instant_pid)

        # Combined exporter.
        combined = CombinedTraceExporter(
            chrome=TraceExporter(
                combined_dir / "trace.json",
                flush_threshold=100,
            ),
            perfetto=PerfettoExporter(
                combined_dir / "trace.pftrace",
                flush_threshold=100,
                cmdline_provider=_fake_cmdline_provider,
            ),
        )
        _feed_combined(items, pids, combined, instant_pid)

        standalone_chrome_path = tmp_path / "standalone" / "trace.json"
        standalone_perfetto_path = tmp_path / "standalone" / "trace.pftrace"
        combined_chrome_path = tmp_path / "combined" / "trace.json"
        combined_perfetto_path = tmp_path / "combined" / "trace.pftrace"

        with (
            _open_trace(standalone_chrome_path) as tp_sc,
            _open_trace(standalone_perfetto_path) as tp_sp,
            _open_trace(combined_chrome_path) as tp_cc,
            _open_trace(combined_perfetto_path) as tp_cp,
        ):
            self._assert_equivalence(tp_sc, tp_sp, tp_cc, tp_cp)

    def _assert_equivalence(
        self,
        tp_standalone_chrome: TraceProcessor,
        tp_standalone_perfetto: TraceProcessor,
        tp_combined_chrome: TraceProcessor,
        tp_combined_perfetto: TraceProcessor,
    ) -> None:
        # 1. Slices: same name set, same ts, same dur.
        slice_query = (
            "SELECT s.name, s.ts, s.dur FROM slice s WHERE s.name != 'thread_sort_index' ORDER BY s.ts, s.name"
        )
        sc_slices = [(r.name, r.ts, r.dur) for r in tp_standalone_chrome.query(slice_query)]
        cc_slices = [(r.name, r.ts, r.dur) for r in tp_combined_chrome.query(slice_query)]
        sp_slices = [(r.name, r.ts, r.dur) for r in tp_standalone_perfetto.query(slice_query)]
        cp_slices = [(r.name, r.ts, r.dur) for r in tp_combined_perfetto.query(slice_query)]
        _assert_row_sets_equal(sc_slices, cc_slices, "chrome slices")
        _assert_row_sets_equal(sp_slices, cp_slices, "perfetto slices")

        # 2. Counter track names (whitespace-stripped, since chrome
        # trace processor prepends a space to single-arg counter names).
        sc_tracks = {
            r.name.strip()
            for r in tp_standalone_chrome.query(
                "SELECT name FROM counter_track",
            )
        }
        cc_tracks = {
            r.name.strip()
            for r in tp_combined_chrome.query(
                "SELECT name FROM counter_track",
            )
        }
        sp_tracks = {
            r.name
            for r in tp_standalone_perfetto.query(
                "SELECT name FROM counter_track",
            )
        }
        cp_tracks = {
            r.name
            for r in tp_combined_perfetto.query(
                "SELECT name FROM counter_track",
            )
        }
        assert sc_tracks == cc_tracks, f"chrome counter tracks differ: standalone={sc_tracks}, combined={cc_tracks}"
        assert sp_tracks == cp_tracks, f"perfetto counter tracks differ: standalone={sp_tracks}, combined={cp_tracks}"

        # 3. Counter data: (counter_track.name.strip(), ts) -> value.
        def _counter_data(tp: TraceProcessor) -> dict[tuple[str, int], float]:
            rows = tp.query("SELECT ct.name, c.ts, c.value FROM counter c JOIN counter_track ct ON c.track_id = ct.id")
            return {(r.name.strip(), r.ts): r.value for r in rows}

        sc_data = _counter_data(tp_standalone_chrome)
        cc_data = _counter_data(tp_combined_chrome)
        sp_data = _counter_data(tp_standalone_perfetto)
        cp_data = _counter_data(tp_combined_perfetto)
        _assert_row_sets_equal(
            list(sc_data.items()),
            list(cc_data.items()),
            "chrome counter data",
        )
        _assert_row_sets_equal(
            list(sp_data.items()),
            list(cp_data.items()),
            "perfetto counter data",
        )
