"""Tests for Perfetto binary protobuf exporter."""

from pathlib import Path

from perfetto.protos.perfetto.trace.perfetto_trace_pb2 import (
    Trace,
    TracePacket,
    TrackEvent,
)

from gcmon.data import GCStatsInfo
from gcmon.exporters import PerfettoExporter
from gcmon.exporters.perfetto_format import (
    TrackEventType,
)
from tests.conftest import DEFAULT_PID
from tests.data_helpers import create_instant_msg
from tests.exporters.conftest import ExporterFactory
from tests.helpers import create_mock_incremental_item, create_mock_stats_item

# Name of the synthetic marker emitted on the process track so the
# cmdline description is always visible in the Perfetto UI. Must match
# ``_START_PROCESS_INSTANT_NAME`` in ``gcmon.exporters.perfetto_format``.
_START_PROCESS_MARKER_NAME: str = "Start Process"


def _read_trace_packets(path: Path) -> list[TracePacket]:
    with open(path, "rb") as f:
        data = f.read()
    if not data:
        return []
    trace = Trace()
    trace.ParseFromString(data)
    return list(trace.packet)


def _get_track_event(packet: TracePacket) -> TrackEvent | None:
    if packet.HasField("track_event"):
        return packet.track_event
    return None


def _is_track_event(packet: TracePacket, event_type: int) -> bool:
    track_event = _get_track_event(packet)
    if track_event is not None:
        return track_event.type == event_type
    return False


def _count_event_type(packet_fields: list[TracePacket], event_type: int) -> int:
    count = 0
    for pf in packet_fields:
        track_event = _get_track_event(pf)
        if track_event is not None and track_event.type == event_type:
            count += 1
    return count


def _count_descriptors(packet_fields: list[TracePacket]) -> int:
    return sum(1 for pf in packet_fields if pf.HasField("track_descriptor"))


class TestPerfettoExporter:
    def test_init(self, perfetto_exporter: ExporterFactory) -> None:
        exporter, path = perfetto_exporter()
        assert isinstance(exporter, PerfettoExporter)
        assert exporter._flush_threshold == 100
        assert exporter._buffer == []
        assert exporter._output_path == path

    def test_init_with_flush_threshold(self, perfetto_exporter: ExporterFactory) -> None:
        exporter, path = perfetto_exporter(threshold=500)
        assert isinstance(exporter, PerfettoExporter)
        assert exporter._flush_threshold == 500
        assert exporter._buffer == []
        assert exporter._output_path == path

    def _verify_event_structure(self, path: Path, num_items: int) -> None:
        packets = _read_trace_packets(path)
        assert len(packets) > 0

        slice_begins = _count_event_type(packets, TrackEventType.SLICE_BEGIN)
        slice_ends = _count_event_type(packets, TrackEventType.SLICE_END)
        counters = _count_event_type(packets, TrackEventType.COUNTER)

        assert slice_begins >= num_items
        assert slice_ends >= num_items
        assert counters >= num_items * 4

    def test_flushes_at_threshold(self, mock_stats_item: GCStatsInfo, perfetto_exporter: ExporterFactory) -> None:
        exporter, path = perfetto_exporter(threshold=10)
        for _ in range(10):
            exporter.add_event(DEFAULT_PID, mock_stats_item)
        assert path.exists()
        exporter.close()
        self._verify_event_structure(path, 10)

    def test_flush_multiple_times(self, mock_stats_item: GCStatsInfo, perfetto_exporter: ExporterFactory) -> None:
        exporter, path = perfetto_exporter(threshold=5)
        for _ in range(15):
            exporter.add_event(DEFAULT_PID, mock_stats_item)
        assert path.exists()
        exporter.close()
        self._verify_event_structure(path, 15)

    def test_close_writes_file(self, mock_stats_item: GCStatsInfo, perfetto_exporter: ExporterFactory) -> None:
        exporter, path = perfetto_exporter()
        exporter.add_event(DEFAULT_PID, mock_stats_item)
        exporter.close()
        assert path.exists()
        assert path.stat().st_size > 0

        packets = _read_trace_packets(path)
        assert len(packets) > 0

        # Verify pause slice
        hit = False
        for packet in packets:
            track_event = _get_track_event(packet)
            if track_event and track_event.type == TrackEvent.Type.TYPE_SLICE_BEGIN:
                name = track_event.name
                if name == "GC Pause (gen=0)":
                    hit = True
                    break
        assert hit, "GC Pause (gen=0) not found"

        # Verify descriptors present
        assert _count_descriptors(packets) >= 2

    def test_close_writes_all_events(self, mock_stats_item: GCStatsInfo, perfetto_exporter: ExporterFactory) -> None:
        exporter, path = perfetto_exporter(threshold=5)
        for _ in range(15):
            exporter.add_event(DEFAULT_PID, mock_stats_item)
        exporter.close()
        self._verify_event_structure(path, 15)

    def test_timestamp_conversion(self, mock_stats_item: GCStatsInfo, perfetto_exporter: ExporterFactory) -> None:
        exporter, path = perfetto_exporter()
        exporter.add_event(DEFAULT_PID, mock_stats_item)
        exporter.close()

        packets = _read_trace_packets(path)
        pause_ts = None
        for packet in packets:
            track_event = _get_track_event(packet)
            if track_event and track_event.type == TrackEvent.Type.TYPE_SLICE_BEGIN:
                pause_ts = packet.timestamp
                break
        assert pause_ts == 1_500_000_000

    def test_multiple_close_calls(self, mock_stats_item: GCStatsInfo, perfetto_exporter: ExporterFactory) -> None:
        exporter, path = perfetto_exporter()
        exporter.add_event(DEFAULT_PID, mock_stats_item)
        exporter.close()
        exporter.close()

        packets = _read_trace_packets(path)
        # 1 GC pause slice begin + 1 Processes-track lifetime begin
        # for the single pid.
        assert _count_event_type(packets, TrackEventType.SLICE_BEGIN) == 2

    def test_different_generation_events(self, perfetto_exporter: ExporterFactory) -> None:
        exporter, path = perfetto_exporter()
        for gen in range(3):
            item = create_mock_stats_item(gen=gen)
            exporter.add_event(DEFAULT_PID, item)
        exporter.close()

        packets = _read_trace_packets(path)
        names = set()
        for packet in packets:
            track_event = _get_track_event(packet)
            if track_event and track_event.type == TrackEvent.Type.TYPE_SLICE_BEGIN:
                name = track_event.name
                if name and "GC Pause" in name:
                    names.add(name)
        assert names == {"GC Pause (gen=0)", "GC Pause (gen=1)", "GC Pause (gen=2)"}

    def test_add_instant_event_writes_instant_event(self, perfetto_exporter: ExporterFactory) -> None:
        exporter, path = perfetto_exporter()
        instant = create_instant_msg(name="start GC monitor", ts=1_500_000_000)
        exporter.add_instant_event(DEFAULT_PID, instant)
        exporter.close()

        packets = _read_trace_packets(path)
        names: list[str] = []
        for packet in packets:
            track_event = _get_track_event(packet)
            if track_event and track_event.type == TrackEvent.Type.TYPE_INSTANT:
                name = track_event.name
                if name:
                    names.append(name)
        # First the synthetic "Start Process" marker (emitted on the
        # process track itself so the cmdline description is always
        # visible in the UI), then the user-provided instant event.
        assert names == [_START_PROCESS_MARKER_NAME, "start GC monitor"]

    def test_multiple_add_instant_event(self, perfetto_exporter: ExporterFactory) -> None:
        exporter, path = perfetto_exporter()
        for ev_name in ("start GC monitor", "stop GC monitor"):
            exporter.add_instant_event(DEFAULT_PID, create_instant_msg(name=ev_name, ts=1_500_000_000))
        exporter.close()

        packets = _read_trace_packets(path)
        names: list[str] = []
        for packet in packets:
            track_event = _get_track_event(packet)
            if track_event and track_event.type == TrackEvent.Type.TYPE_INSTANT:
                event_name = track_event.name
                if event_name:
                    names.append(event_name)
        # The marker is emitted only on the first event for the pid.
        assert names == [
            _START_PROCESS_MARKER_NAME,
            "start GC monitor",
            "stop GC monitor",
        ]

    def test_events_have_valid_timestamps(
        self, mock_stats_item: GCStatsInfo, perfetto_exporter: ExporterFactory
    ) -> None:
        exporter, path = perfetto_exporter()
        exporter.add_event(DEFAULT_PID, mock_stats_item)
        exporter.close()

        packets = _read_trace_packets(path)
        for packet in packets:
            ts = packet.timestamp
            if ts:
                assert ts >= 1_500_000

    def test_close_with_no_events(self, perfetto_exporter: ExporterFactory) -> None:
        exporter, path = perfetto_exporter()
        exporter.close()
        assert not path.exists() or path.stat().st_size == 0

    def test_descriptors_written_before_events(self, perfetto_exporter: ExporterFactory) -> None:
        exporter, path = perfetto_exporter()
        exporter.add_event(DEFAULT_PID, create_mock_stats_item())
        exporter.close()

        packets = _read_trace_packets(path)
        assert packets[0].HasField("track_descriptor")

    def test_multiple_processes(self, perfetto_exporter: ExporterFactory) -> None:
        exporter, path = perfetto_exporter()
        item = create_mock_stats_item()
        exporter.add_event(100, item)
        exporter.add_event(200, item)
        exporter.close()

        packets = _read_trace_packets(path)
        descriptors = sum(1 for p in packets if p.HasField("track_descriptor"))
        assert descriptors >= 4

    def test_incremental_item_emits_subphases(self, perfetto_exporter: ExporterFactory) -> None:
        exporter, path = perfetto_exporter()
        item = create_mock_incremental_item()
        exporter.add_event(DEFAULT_PID, item)
        exporter.close()

        packets = _read_trace_packets(path)
        begin_names = set()
        for packet in packets:
            track_event = _get_track_event(packet)
            if track_event and track_event.type == TrackEvent.Type.TYPE_SLICE_BEGIN:
                name = track_event.name
                if name:
                    begin_names.add(name)
        expected = {
            "GC Pause (gen=0)",
            "Mark Alive (gen=0)",
            "Fill increment (gen=0)",
            "Deduce Unreachable (gen=0)",
            "Handle Weakrefs Callbacks (gen=0)",
            "Finalize Garbage (gen=0)",
            "Handle Resurrected (gen=0)",
            "Clear Weakrefs (gen=0)",
            "Delete Garbage (gen=0)",
        }
        assert expected.issubset(begin_names)

    def test_counter_events_per_metric(self, perfetto_exporter: ExporterFactory) -> None:
        exporter, path = perfetto_exporter()
        exporter.add_event(DEFAULT_PID, create_mock_stats_item())
        exporter.close()

        packets = _read_trace_packets(path)
        counter_tracks = set()
        for packet in packets:
            track_event = _get_track_event(packet)
            if track_event and track_event.type == TrackEvent.Type.TYPE_COUNTER:
                uuid = track_event.track_uuid
                if uuid:
                    counter_tracks.add(uuid)
        # collected, uncollectable, candidates, heap_size, duration.
        assert len(counter_tracks) == 5

    def test_cmdline_collected_from_psutil(self, tmp_path: Path) -> None:
        calls: list[int] = []

        def _cmdline_provider(pid: int) -> list[str]:
            calls.append(pid)
            return ["python", "-u", "my_script.py"]

        exporter = PerfettoExporter(
            output_path=tmp_path / "test.pb",
            cmdline_provider=_cmdline_provider,
        )
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
        exporter.add_event(12345, item)
        exporter.close()

        assert calls == [12345]
        trace_data = (tmp_path / "test.pb").read_bytes()
        assert len(trace_data) > 0

        packets = _read_trace_packets(tmp_path / "test.pb")
        found_cmdline = False
        found_description = False
        for packet in packets:
            if packet.HasField("track_descriptor"):
                td = packet.track_descriptor
                if td.description == "python -u my_script.py":
                    found_description = True
                if td.HasField("process"):
                    proc = td.process
                    if proc.cmdline:
                        assert proc.cmdline[0] == "python"
                        assert proc.cmdline[1] == "-u"
                        assert proc.cmdline[2] == "my_script.py"
                        found_cmdline = True
        assert found_cmdline, "cmdline not found in trace"
        assert found_description, "track description should be set when cmdline is present"

    def test_no_psutil_graceful_degradation(self, tmp_path: Path) -> None:
        exporter = PerfettoExporter(
            output_path=tmp_path / "test.pb",
            cmdline_provider=lambda pid: None,
        )
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
        exporter.add_event(12345, item)
        exporter.close()

        trace_data = (tmp_path / "test.pb").read_bytes()
        assert len(trace_data) > 0

        packets = _read_trace_packets(tmp_path / "test.pb")
        for packet in packets:
            if packet.HasField("track_descriptor"):
                td = packet.track_descriptor
                assert not td.HasField("description"), "description should be absent when cmdline is unavailable"
                if td.HasField("process"):
                    proc = td.process
                    assert len(proc.cmdline) == 0, "cmdline should be absent when psutil is unavailable"

    def test_slice_begin_end_matched(self, perfetto_exporter: ExporterFactory) -> None:
        exporter, path = perfetto_exporter()
        for _ in range(5):
            exporter.add_event(DEFAULT_PID, create_mock_stats_item())
        exporter.close()

        packets = _read_trace_packets(path)
        begins = _count_event_type(packets, TrackEventType.SLICE_BEGIN)
        ends = _count_event_type(packets, TrackEventType.SLICE_END)
        assert begins == ends
