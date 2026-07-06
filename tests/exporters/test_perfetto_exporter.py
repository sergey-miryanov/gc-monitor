"""Tests for Perfetto binary protobuf exporter."""

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
from gcmon.poll_status import ProcessLifecycle
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

# Name of the synthetic marker emitted on the process track so the
# cmdline description is always visible in the Perfetto UI. Must match
# ``_START_PROCESS_INSTANT_NAME`` in ``gcmon.exporters.perfetto_format``.
_START_PROCESS_MARKER_NAME: str = "Start Process"


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

    def test_init_with_flush_threshold(self, perfetto_exporter) -> None:
        exporter, _ = perfetto_exporter(threshold=500)

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
        assert pause_ts == 1_500_000_000

    def test_multiple_close_calls(self, mock_stats_item, perfetto_exporter) -> None:
        exporter, path = perfetto_exporter()
        exporter.add_event(DEFAULT_PID, mock_stats_item)
        exporter.close()
        exporter.close()

        packets = _read_trace_packets(path)
        # 1 GC pause slice begin + 1 Processes-track lifetime begin
        # for the single pid.
        assert _count_event_type(packets, TYPE_SLICE_BEGIN) == 2

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
        # First the synthetic "Start Process" marker (emitted on the
        # process track itself so the cmdline description is always
        # visible in the UI), then the user-provided instant event.
        assert names == [_START_PROCESS_MARKER_NAME, "start GC monitor"]

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
        # The marker is emitted only on the first event for the pid.
        assert names == [
            _START_PROCESS_MARKER_NAME, "start GC monitor", "stop GC monitor",
        ]

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
        # collected, uncollectable, candidates, heap_size, duration.
        assert len(counter_tracks) == 5

    def test_cmdline_collected_from_psutil(self, tmp_path):
        calls: list[int] = []

        def _cmdline_provider(pid: int) -> list[str]:
            calls.append(pid)
            return ["python", "-u", "my_script.py"]

        exporter = PerfettoExporter(
            output_path=tmp_path / "test.pb",
            cmdline_provider=_cmdline_provider,
        )
        item = GCStatsInfo(
            gen=0, iid=0, ts_start=1_000, ts_stop=2_000,
            heap_size=1000, collections=1, collected=10,
            uncollectable=0, candidates=5, duration=0.001,
        )
        exporter.add_event(12345, item)
        exporter.close()

        assert calls == [12345]
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

    def test_no_psutil_graceful_degradation(self, tmp_path):
        exporter = PerfettoExporter(
            output_path=tmp_path / "test.pb",
            cmdline_provider=lambda pid: None,
        )
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


def _lifetime_slice_timestamps(path) -> list[tuple[int, int, int]]:
    """Return ``[(type, ts, name_pid), ...]`` for every event on the
    shared ``Processes`` track in *path*. The third tuple element is
    the pid extracted from the slice name (``"Process <pid>"``)."""
    out: list[tuple[int, int, int]] = []
    packets = _read_trace_packets(path)
    for pf in packets:
        te = _get_track_event(pf)
        if te is None:
            continue
        # Only consider events whose name looks like "Process <pid>".
        # The track-uuid check is implicit: the only track with such
        # names is the lifetime track.
        name = get_string(te, TrackEventField.NAME)
        if not (name and name.startswith("Process ")):
            continue
        try:
            pid = int(name.split(" ", 1)[1])
        except ValueError:
            continue
        out.append((get_varint(te, TrackEventField.TYPE), get_int_at(pf, TracePacketField.TIMESTAMP), pid))
    return out


class TestPerfettoExporterProcessLifecycle:
    """End-to-end tests for the monitor-driven STARTED / DIED timestamps
    flowing through ``PerfettoExporter`` into the ``Processes`` track
    slice BEGIN/END."""

    def test_started_ts_drives_slice_begin_when_set(
        self, perfetto_exporter,
    ) -> None:
        exporter, path = perfetto_exporter()
        pid = 12345
        item = create_mock_stats_item(ts_start=5_000_000, ts_stop=6_000_000)
        # The monitor would have recorded STARTED at this monotonic ts
        # *before* the first event arrives.
        exporter.mark_process_lifecycle(pid, ProcessLifecycle.STARTED, 100)
        exporter.add_event(pid, item)
        exporter.close()

        pairs = _lifetime_slice_timestamps(path)
        begins = [(ts, p) for kind, ts, p in pairs if kind == TYPE_SLICE_BEGIN]
        ends = [(ts, p) for kind, ts, p in pairs if kind == TYPE_SLICE_END]
        assert begins == [(100, pid)]
        # END falls back to the last non-counter non-meta event ts.
        assert ends == [(6_000_000, pid)]

    def test_died_ts_drives_slice_end_when_set(
        self, perfetto_exporter,
    ) -> None:
        exporter, path = perfetto_exporter()
        pid = 12345
        item = create_mock_stats_item(ts_start=5_000_000, ts_stop=6_000_000)
        exporter.add_event(pid, item)
        # Monitor detected the process as dead after the events.
        exporter.mark_process_lifecycle(pid, ProcessLifecycle.DIED, 9_999_000)
        exporter.close()

        pairs = _lifetime_slice_timestamps(path)
        begins = [(ts, p) for kind, ts, p in pairs if kind == TYPE_SLICE_BEGIN]
        ends = [(ts, p) for kind, ts, p in pairs if kind == TYPE_SLICE_END]
        # BEGIN falls back to the first non-meta event ts.
        assert begins == [(5_000_000, pid)]
        assert ends == [(9_999_000, pid)]

    def test_started_and_died_together(
        self, perfetto_exporter,
    ) -> None:
        exporter, path = perfetto_exporter()
        pid = 12345
        item = create_mock_stats_item(ts_start=5_000_000, ts_stop=6_000_000)
        exporter.mark_process_lifecycle(pid, ProcessLifecycle.STARTED, 1_000)
        exporter.add_event(pid, item)
        exporter.mark_process_lifecycle(pid, ProcessLifecycle.DIED, 9_000_000)
        exporter.close()

        pairs = _lifetime_slice_timestamps(path)
        begins = [(ts, p) for kind, ts, p in pairs if kind == TYPE_SLICE_BEGIN]
        ends = [(ts, p) for kind, ts, p in pairs if kind == TYPE_SLICE_END]
        assert begins == [(1_000, pid)]
        assert ends == [(9_000_000, pid)]

    def test_process_descriptor_start_timestamp_uses_started(
        self, perfetto_exporter,
    ) -> None:
        exporter, path = perfetto_exporter()
        pid = 12345
        item = create_mock_stats_item(ts_start=5_000_000, ts_stop=6_000_000)
        exporter.mark_process_lifecycle(pid, ProcessLifecycle.STARTED, 1_000)
        exporter.add_event(pid, item)
        exporter.close()

        # Find the process track descriptor and inspect
        # start_timestamp_ns on its embedded ProcessDescriptor.
        packets = _read_trace_packets(path)
        found = False
        for pf in packets:
            td_bytes = get_bytes_at(pf, TracePacketField.TRACK_DESCRIPTOR)
            if td_bytes is None:
                continue
            td_fields = decode_message(td_bytes)
            proc_bytes = get_bytes_at(td_fields, TrackDescriptorField.PROCESS)
            if proc_bytes is None:
                continue
            proc_fields = decode_message(proc_bytes)
            pid_in_desc = get_varint(proc_fields, ProcessDescriptorField.PID)
            if pid_in_desc != pid:
                continue
            assert get_varint(proc_fields, ProcessDescriptorField.START_TIMESTAMP_NS) == 1_000
            found = True
        assert found, "process descriptor for pid 12345 not found"

    def test_started_ts_takes_precedence_when_set_before_close(
        self, perfetto_exporter,
    ) -> None:
        """If events are enqueued first but not yet flushed (default
        ``flush_threshold`` keeps them buffered), and STARTED / DIED are
        reported before ``close()``, the lifetime slice is bracketed by
        the monitor-reported timestamps. The buffered events flush
        after the lifecycle state was already updated under the same
        ``_io_lock``, so STARTED is honored even though it arrived
        after ``add_event``."""
        exporter, path = perfetto_exporter()
        pid = 12345
        item = create_mock_stats_item(ts_start=5_000_000, ts_stop=6_000_000)
        exporter.add_event(pid, item)
        exporter.mark_process_lifecycle(pid, ProcessLifecycle.STARTED, 1_000)
        exporter.mark_process_lifecycle(pid, ProcessLifecycle.DIED, 9_000_000)
        exporter.close()

        pairs = _lifetime_slice_timestamps(path)
        begins = [(ts, p) for kind, ts, p in pairs if kind == TYPE_SLICE_BEGIN]
        ends = [(ts, p) for kind, ts, p in pairs if kind == TYPE_SLICE_END]
        assert begins == [(1_000, pid)]
        assert ends == [(9_000_000, pid)]

    def test_slice_begin_uses_earlier_of_started_and_first_event(
        self, perfetto_exporter,
    ) -> None:
        """When the monitor-reported STARTED ts is later than the
        first event ts (e.g. because the first poll happened after
        some buffered GC events were already in flight, or because
        a manual call reported a delayed STARTED for testing), the
        lifetime slice BEGIN uses the earlier of the two so the
        first event is never clipped by the slice boundary."""
        exporter, path = perfetto_exporter()
        pid = 12345
        # First event ts = 1_000_000, started ts = 5_000_000 (later).
        item = create_mock_stats_item(ts_start=1_000_000, ts_stop=2_000_000)
        exporter.mark_process_lifecycle(pid, ProcessLifecycle.STARTED, 5_000_000)
        exporter.add_event(pid, item)
        exporter.close()

        pairs = _lifetime_slice_timestamps(path)
        begins = [(ts, p) for kind, ts, p in pairs if kind == TYPE_SLICE_BEGIN]
        assert begins == [(1_000_000, pid)]

    def test_slice_begin_uses_started_when_earlier_than_first_event(
        self, perfetto_exporter,
    ) -> None:
        """Symmetric case: when the monitor-reported STARTED ts is
        earlier than the first event ts, the slice BEGIN still uses
        the earlier of the two (which is the started ts)."""
        exporter, path = perfetto_exporter()
        pid = 12345
        # First event ts = 5_000_000, started ts = 1_000_000 (earlier).
        item = create_mock_stats_item(ts_start=5_000_000, ts_stop=6_000_000)
        exporter.mark_process_lifecycle(pid, ProcessLifecycle.STARTED, 1_000_000)
        exporter.add_event(pid, item)
        exporter.close()

        pairs = _lifetime_slice_timestamps(path)
        begins = [(ts, p) for kind, ts, p in pairs if kind == TYPE_SLICE_BEGIN]
        assert begins == [(1_000_000, pid)]

    def test_slice_end_uses_later_of_died_and_last_event(
        self, perfetto_exporter,
    ) -> None:
        """When the monitor-reported DIED ts is later than the last
        event ts, the slice END uses the later of the two so the
        slice covers up to the detected death rather than clipping
        at the last observed GC event."""
        exporter, path = perfetto_exporter()
        pid = 12345
        # Last event ts_stop = 6_000_000, died ts = 9_000_000 (later).
        item = create_mock_stats_item(ts_start=5_000_000, ts_stop=6_000_000)
        exporter.add_event(pid, item)
        exporter.mark_process_lifecycle(pid, ProcessLifecycle.DIED, 9_000_000)
        exporter.close()

        pairs = _lifetime_slice_timestamps(path)
        ends = [(ts, p) for kind, ts, p in pairs if kind == TYPE_SLICE_END]
        assert ends == [(9_000_000, pid)]

    def test_slice_end_uses_last_event_when_later_than_died(
        self, perfetto_exporter,
    ) -> None:
        """Symmetric case: when the last event ts is later than the
        DIED ts (events kept arriving after the death was detected,
        e.g. via a buffered flush), the slice END uses the later of
        the two so the slice covers the last GC event."""
        exporter, path = perfetto_exporter()
        pid = 12345
        # Last event ts_stop = 8_000_000, died ts = 6_000_000 (earlier).
        item = create_mock_stats_item(ts_start=5_000_000, ts_stop=8_000_000)
        exporter.add_event(pid, item)
        exporter.mark_process_lifecycle(pid, ProcessLifecycle.DIED, 6_000_000)
        exporter.close()

        pairs = _lifetime_slice_timestamps(path)
        ends = [(ts, p) for kind, ts, p in pairs if kind == TYPE_SLICE_END]
        assert ends == [(8_000_000, pid)]

    def test_overlapping_pids_previous_end_clipped_to_next_begin(
        self, perfetto_exporter,
    ) -> None:
        """When two pids have overlapping event ranges, the
        encoder's BEGIN/END layout would normally cause the Perfetto
        trace processor to collapse the second pid's slice to the
        overlap duration (the trace processor pairs each BEGIN with
        the closest END whose ts is at or after the BEGIN.ts, so the
        previous pid's END would steal the next pid's BEGIN when the
        ranges overlap). The fix clips the previous pid's END to one
        nanosecond before the next pid's BEGIN so each BEGIN pairs
        with its own END."""
        exporter, path = perfetto_exporter()
        # Pid 100: events spanning 1_000_000 -> 5_000_000.
        # Pid 200: first event at 3_000_000 (overlaps pid 100's range).
        exporter.add_event(100, create_mock_stats_item(ts_start=1_000_000, ts_stop=5_000_000))
        exporter.add_event(200, create_mock_stats_item(ts_start=3_000_000, ts_stop=7_000_000))
        exporter.close()

        pairs = _lifetime_slice_timestamps(path)
        ends = {p: ts for kind, ts, p in pairs if kind == TYPE_SLICE_END}
        # Pid 100's END is clipped from 5_000_000 to 2_999_999 (one
        # nanosecond before pid 200's BEGIN at 3_000_000). Pid 200's
        # END is unchanged at 7_000_000.
        assert ends == {100: 2_999_999, 200: 7_000_000}
