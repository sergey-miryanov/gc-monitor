"""Tests for Perfetto binary protobuf exporter."""

import sys
from unittest.mock import MagicMock

from tests.conftest import DEFAULT_PID
from tests.data_helpers import create_instant_msg
from tests.helpers import create_mock_incremental_item, create_mock_stats_item
from tests.proto_decoder import (
    ProtoField,
    decode_message,
    get_field,
    get_fields,
    get_string,
    get_varint,
)

from gcmon.data import GCStatsInfo
from gcmon.exporters import PerfettoExporter
from gcmon.exporters.perfetto_format import (
    TYPE_COUNTER,
    TYPE_INSTANT,
    TYPE_SLICE_BEGIN,
    TYPE_SLICE_END,
    ProcessDescriptorField,
    TraceField,
    TracePacketField,
    TrackDescriptorField,
    TrackEventField,
)


def _read_trace_packets(path) -> list[list[ProtoField]]:
    """Read a Perfetto binary trace file and return list of parsed TracePacket fields."""
    with open(path, "rb") as f:
        data = f.read()
    if not data:
        return []
    trace_fields = decode_message(data)
    return [decode_message(f.value) for f in get_fields(trace_fields, TraceField.PACKET)]


def _get_track_event(fields: list[ProtoField]) -> list[ProtoField] | None:
    te_bytes = get_bytes_at(fields, TracePacketField.TRACK_EVENT)
    if te_bytes:
        return decode_message(te_bytes)
    return None


def _is_track_event(fields: list[ProtoField], event_type: int) -> bool:
    te = _get_track_event(fields)
    if te is not None:
        return any(f.field_number == TrackEventField.TYPE and f.value == event_type for f in te)
    return False


def get_bytes_at(fields: list[ProtoField], field_number: int) -> bytes | None:
    for f in fields:
        if f.field_number == field_number:
            return f.value
    return None


def get_int_at(fields: list[ProtoField], field_number: int) -> int | None:
    for f in fields:
        if f.field_number == field_number:
            return f.value
    return None


def _count_event_type(packet_fields: list[list[ProtoField]], event_type: int) -> int:
    count = 0
    for pf in packet_fields:
        te = _get_track_event(pf)
        if te:
            for f in te:
                if f.field_number == TrackEventField.TYPE and f.value == event_type:
                    count += 1
    return count


def _count_descriptors(packet_fields: list[list[ProtoField]]) -> int:
    return sum(1 for pf in packet_fields if get_bytes_at(pf, TracePacketField.TRACK_DESCRIPTOR) is not None)


class TestPerfettoExporter:
    def test_init(self, perfetto_exporter) -> None:
        exporter, _ = perfetto_exporter()
        assert exporter.get_event_count() == 0

    def test_init_with_flush_threshold(self, perfetto_exporter) -> None:
        exporter, _ = perfetto_exporter(threshold=500)
        assert exporter.get_event_count() == 0

    def _verify_event_structure(self, path, num_items: int) -> None:
        packets = _read_trace_packets(path)
        assert len(packets) > 0

        slice_begins = _count_event_type(packets, TYPE_SLICE_BEGIN)
        slice_ends = _count_event_type(packets, TYPE_SLICE_END)
        counters = _count_event_type(packets, TYPE_COUNTER)

        assert slice_begins >= num_items
        assert slice_ends >= num_items
        assert counters >= num_items * 4

    def test_flushes_at_threshold(self, mock_stats_item, perfetto_exporter) -> None:
        exporter, path = perfetto_exporter(threshold=10)
        for _ in range(10):
            exporter.add_event(DEFAULT_PID, mock_stats_item)
        assert path.exists()
        exporter.close()
        self._verify_event_structure(path, 10)

    def test_flush_multiple_times(self, mock_stats_item, perfetto_exporter) -> None:
        exporter, path = perfetto_exporter(threshold=5)
        for _ in range(15):
            exporter.add_event(DEFAULT_PID, mock_stats_item)
        assert path.exists()
        exporter.close()
        self._verify_event_structure(path, 15)

    def test_close_writes_file(self, mock_stats_item, perfetto_exporter) -> None:
        exporter, path = perfetto_exporter()
        exporter.add_event(DEFAULT_PID, mock_stats_item)
        exporter.close()
        assert path.exists()
        assert path.stat().st_size > 0

        packets = _read_trace_packets(path)
        assert len(packets) > 0

        # Verify pause slice
        hit = False
        for pf in packets:
            te = _get_track_event(pf)
            if te and get_varint(te, TrackEventField.TYPE) == TYPE_SLICE_BEGIN:
                name = get_string(te, TrackEventField.NAME)
                if name == "GC Pause (gen=0)":
                    hit = True
                    break
        assert hit, "GC Pause (gen=0) not found"

        # Verify descriptors present
        assert _count_descriptors(packets) >= 2

    def test_close_writes_all_events(self, mock_stats_item, perfetto_exporter) -> None:
        exporter, path = perfetto_exporter(threshold=5)
        for _ in range(15):
            exporter.add_event(DEFAULT_PID, mock_stats_item)
        exporter.close()
        self._verify_event_structure(path, 15)

    def test_add_event_count(self, mock_stats_item, perfetto_exporter) -> None:
        exporter, _ = perfetto_exporter()
        exporter.add_event(DEFAULT_PID, mock_stats_item)
        assert exporter.get_event_count() == 1

    def test_timestamp_conversion(self, mock_stats_item, perfetto_exporter) -> None:
        exporter, path = perfetto_exporter()
        exporter.add_event(DEFAULT_PID, mock_stats_item)
        exporter.close()

        packets = _read_trace_packets(path)
        pause_ts = None
        for pf in packets:
            te = _get_track_event(pf)
            if te and get_varint(te, TrackEventField.TYPE) == TYPE_SLICE_BEGIN:
                pause_ts = get_int_at(pf, TracePacketField.TIMESTAMP)
                break
        assert pause_ts == 1_500_000

    def test_multiple_close_calls(self, mock_stats_item, perfetto_exporter) -> None:
        exporter, path = perfetto_exporter()
        exporter.add_event(DEFAULT_PID, mock_stats_item)
        exporter.close()
        exporter.close()

        packets = _read_trace_packets(path)
        assert _count_event_type(packets, TYPE_SLICE_BEGIN) == 1

    def test_different_generation_events(self, perfetto_exporter) -> None:
        exporter, path = perfetto_exporter()
        for gen in range(3):
            item = create_mock_stats_item(gen=gen)
            exporter.add_event(DEFAULT_PID, item)
        exporter.close()

        packets = _read_trace_packets(path)
        names = set()
        for pf in packets:
            te = _get_track_event(pf)
            if te and get_varint(te, TrackEventField.TYPE) == TYPE_SLICE_BEGIN:
                name = get_string(te, TrackEventField.NAME)
                if name and "GC Pause" in name:
                    names.add(name)
        assert names == {"GC Pause (gen=0)", "GC Pause (gen=1)", "GC Pause (gen=2)"}

    def test_add_instant_event_writes_instant_event(self, perfetto_exporter) -> None:
        exporter, path = perfetto_exporter()
        instant = create_instant_msg(name="start GC monitor", ts=1_500_000_000)
        exporter.add_instant_event(DEFAULT_PID, instant)
        exporter.close()

        packets = _read_trace_packets(path)
        names = []
        for pf in packets:
            te = _get_track_event(pf)
            if te and get_varint(te, TrackEventField.TYPE) == TYPE_INSTANT:
                name = get_string(te, TrackEventField.NAME)
                if name:
                    names.append(name)
        assert names == ["start GC monitor"]

    def test_add_instant_event_not_counted_in_get_event_count(self, perfetto_exporter) -> None:
        exporter, _ = perfetto_exporter()
        instant = create_instant_msg(name="event", ts=1_000_000_000)
        exporter.add_instant_event(DEFAULT_PID, instant)
        assert exporter.get_event_count() == 0

    def test_multiple_add_instant_event(self, perfetto_exporter) -> None:
        exporter, path = perfetto_exporter()
        for name in ("start GC monitor", "stop GC monitor"):
            exporter.add_instant_event(DEFAULT_PID, create_instant_msg(name=name, ts=1_500_000_000))
        exporter.close()

        packets = _read_trace_packets(path)
        names = []
        for pf in packets:
            te = _get_track_event(pf)
            if te and get_varint(te, TrackEventField.TYPE) == TYPE_INSTANT:
                name = get_string(te, TrackEventField.NAME)
                if name:
                    names.append(name)
        assert names == ["start GC monitor", "stop GC monitor"]

    def test_events_have_valid_timestamps(self, mock_stats_item, perfetto_exporter) -> None:
        exporter, path = perfetto_exporter()
        exporter.add_event(DEFAULT_PID, mock_stats_item)
        exporter.close()

        packets = _read_trace_packets(path)
        for pf in packets:
            ts = get_int_at(pf, TracePacketField.TIMESTAMP)
            if ts is not None:
                assert ts >= 1_500_000

    def test_close_with_no_events(self, perfetto_exporter) -> None:
        exporter, path = perfetto_exporter()
        exporter.close()
        assert not path.exists() or path.stat().st_size == 0

    def test_descriptors_written_before_events(self, perfetto_exporter) -> None:
        exporter, path = perfetto_exporter()
        exporter.add_event(DEFAULT_PID, create_mock_stats_item())
        exporter.close()

        packets = _read_trace_packets(path)
        assert get_bytes_at(packets[0], TracePacketField.TRACK_DESCRIPTOR) is not None

    def test_multiple_processes(self, perfetto_exporter) -> None:
        exporter, path = perfetto_exporter()
        item = create_mock_stats_item()
        exporter.add_event(100, item)
        exporter.add_event(200, item)
        exporter.close()

        packets = _read_trace_packets(path)
        descriptors = sum(1 for pf in packets if get_bytes_at(pf, TracePacketField.TRACK_DESCRIPTOR) is not None)
        assert descriptors >= 4

    def test_incremental_item_emits_subphases(self, perfetto_exporter) -> None:
        exporter, path = perfetto_exporter()
        item = create_mock_incremental_item()
        exporter.add_event(DEFAULT_PID, item)
        exporter.close()

        packets = _read_trace_packets(path)
        begin_names = set()
        for pf in packets:
            te = _get_track_event(pf)
            if te and get_varint(te, TrackEventField.TYPE) == TYPE_SLICE_BEGIN:
                name = get_string(te, TrackEventField.NAME)
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

    def test_counter_events_per_metric(self, perfetto_exporter) -> None:
        exporter, path = perfetto_exporter()
        exporter.add_event(DEFAULT_PID, create_mock_stats_item())
        exporter.close()

        packets = _read_trace_packets(path)
        counter_tracks = set()
        for pf in packets:
            te = _get_track_event(pf)
            if te and get_varint(te, TrackEventField.TYPE) == TYPE_COUNTER:
                uuid = get_varint(te, TrackEventField.TRACK_UUID)
                if uuid is not None:
                    counter_tracks.add(uuid)
        assert len(counter_tracks) == 4

    def test_cmdline_collected_from_psutil(self, tmp_path, monkeypatch):
        mock_process = MagicMock()
        mock_process.cmdline.return_value = ["python", "-u", "my_script.py"]

        mock_psutil = MagicMock()
        mock_psutil.Process.return_value = mock_process
        mock_psutil.Error = Exception

        monkeypatch.setitem(sys.modules, "psutil", mock_psutil)

        exporter = PerfettoExporter(output_path=tmp_path / "test.pb")
        item = GCStatsInfo(
            gen=0, iid=0, ts_start=1_000, ts_stop=2_000,
            heap_size=1000, collections=1, collected=10,
            uncollectable=0, candidates=5, duration=0.001,
        )
        exporter.add_event(12345, item)
        exporter.close()

        mock_psutil.Process.assert_called_with(12345)
        trace_data = (tmp_path / "test.pb").read_bytes()
        assert len(trace_data) > 0

        packets = _read_trace_packets(tmp_path / "test.pb")
        found_cmdline = False
        found_description = False
        for pf in packets:
            td_bytes = get_bytes_at(pf, TracePacketField.TRACK_DESCRIPTOR)
            if td_bytes:
                td_fields = decode_message(td_bytes)
                if get_string(td_fields, TrackDescriptorField.DESCRIPTION) == "python -u my_script.py":
                    found_description = True
                proc_bytes = get_bytes_at(td_fields, TrackDescriptorField.PROCESS)
                if proc_bytes:
                    proc_fields = decode_message(proc_bytes)
                    cmdline_entries = get_fields(proc_fields, ProcessDescriptorField.CMDLINE)
                    if cmdline_entries:
                        assert cmdline_entries[0].value == b"python"
                        assert cmdline_entries[1].value == b"-u"
                        assert cmdline_entries[2].value == b"my_script.py"
                        found_cmdline = True
        assert found_cmdline, "cmdline not found in trace"
        assert found_description, "track description should be set when cmdline is present"

    def test_no_psutil_graceful_degradation(self, tmp_path, monkeypatch):
        monkeypatch.setitem(sys.modules, "psutil", None)

        exporter = PerfettoExporter(output_path=tmp_path / "test.pb")
        item = GCStatsInfo(
            gen=0, iid=0, ts_start=1_000, ts_stop=2_000,
            heap_size=1000, collections=1, collected=10,
            uncollectable=0, candidates=5, duration=0.001,
        )
        exporter.add_event(12345, item)
        exporter.close()

        trace_data = (tmp_path / "test.pb").read_bytes()
        assert len(trace_data) > 0

        packets = _read_trace_packets(tmp_path / "test.pb")
        for pf in packets:
            td_bytes = get_bytes_at(pf, TracePacketField.TRACK_DESCRIPTOR)
            if td_bytes:
                td_fields = decode_message(td_bytes)
                assert get_field(td_fields, TrackDescriptorField.DESCRIPTION) is None, \
                    "description should be absent when cmdline is unavailable"
                proc_bytes = get_bytes_at(td_fields, TrackDescriptorField.PROCESS)
                if proc_bytes:
                    proc_fields = decode_message(proc_bytes)
                    cmdline_entries = get_fields(proc_fields, ProcessDescriptorField.CMDLINE)
                    assert cmdline_entries == [], "cmdline should be absent when psutil is unavailable"

    def test_slice_begin_end_matched(self, perfetto_exporter) -> None:
        exporter, path = perfetto_exporter()
        for _ in range(5):
            exporter.add_event(DEFAULT_PID, create_mock_stats_item())
        exporter.close()

        packets = _read_trace_packets(path)
        begins = _count_event_type(packets, TYPE_SLICE_BEGIN)
        ends = _count_event_type(packets, TYPE_SLICE_END)
        assert begins == ends
