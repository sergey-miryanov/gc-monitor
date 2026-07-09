"""Tests for Perfetto protobuf message builders and conversion."""

from gcmon.data import GCStatsInfo
from gcmon.exporters.perfetto_format import (
    TYPE_COUNTER,
    TYPE_INSTANT,
    TYPE_SLICE_BEGIN,
    TYPE_SLICE_END,
    CounterDescriptorField,
    DebugAnnotationField,
    PerfettoTrackState,
    ProcessDescriptorField,
    ThreadDescriptorField,
    TraceField,
    TracePacketField,
    TrackDescriptorField,
    TrackEventField,
    build_trace,
    build_trace_packet,
    build_track_descriptor,
    build_track_event,
    convert_trace_events_to_perfetto,
    finalize_perfetto_packets,
)
from gcmon.exporters.trace_converter import convert_item_to_trace_format
from gcmon.trace_event import counter_event, instant_event, process_meta, thread_meta
from tests.proto_decoder import (
    decode_message,
    get_bytes,
    get_double,
    get_field,
    get_fields,
    get_string,
    get_varint,
)

# Name of the synthetic marker emitted on the process track so the
# cmdline description is always visible in the Perfetto UI. Must match
# ``_START_PROCESS_INSTANT_NAME`` in ``gcmon.exporters.perfetto_format``.
_START_PROCESS_MARKER_NAME: str = "Start Process"

# Name of the shared top-level Perfetto track that holds one slice per
# pid spanning the first-to-last non-meta event timestamps for that
# pid. Must match ``_PROCESS_LIFETIME_TRACK_NAME`` in
# ``gcmon.exporters.perfetto_format``.
_PROCESS_LIFETIME_TRACK_NAME: str = "Processes"


def _convert_item(
    pid: int,
    item: GCStatsInfo,
    state: PerfettoTrackState,
    sequence_id: int = 1,
) -> tuple[list[bytes], list[bytes]]:
    gc_events = convert_item_to_trace_format(pid, item)
    meta = [
        process_meta(pid, f"Process {pid}"),
        thread_meta(pid, item.iid, f"Thread {item.iid}"),
    ]
    descriptors, packets = convert_trace_events_to_perfetto(
        meta + gc_events,
        state,
        sequence_id,
    )
    packets.extend(finalize_perfetto_packets(state, sequence_id))
    return descriptors, packets


class TestPerfettoTrackState:
    def test_init_empty(self) -> None:
        state = PerfettoTrackState()
        assert not state.has_pid(123)
        assert not state.has_tid(123, 0)
        assert not state.has_counter_track(123, 0, "G0", "collected")

    def test_pid_tracking(self) -> None:
        state = PerfettoTrackState()
        assert not state.has_pid(100)
        state.mark_pid(100)
        assert state.has_pid(100)
        assert not state.has_pid(200)

    def test_tid_tracking(self) -> None:
        state = PerfettoTrackState()
        assert not state.has_tid(100, 0)
        state.mark_tid(100, 0)
        assert state.has_tid(100, 0)
        assert not state.has_tid(100, 1)
        assert not state.has_tid(200, 0)

    def test_process_track_uuid(self) -> None:
        state = PerfettoTrackState()
        uuid = state.get_process_track_uuid(12345)
        assert uuid == 1

    def test_thread_track_uuid(self) -> None:
        state = PerfettoTrackState()
        uuid = state.get_thread_track_uuid(12345, 0)
        assert uuid == 1

    def test_thread_track_uuid_different_iid(self) -> None:
        state = PerfettoTrackState()
        uuid0 = state.get_thread_track_uuid(12345, 0)
        uuid1 = state.get_thread_track_uuid(12345, 1)
        assert uuid0 != uuid1

    def test_counter_track_uuid_sequential(self) -> None:
        state = PerfettoTrackState()
        uuid0 = state.get_or_create_counter_track_uuid(100, 0, "G0", "collected")
        uuid1 = state.get_or_create_counter_track_uuid(100, 0, "G0", "heap_size")
        assert uuid0 == 1
        assert uuid1 == 2

    def test_counter_track_uuid_idempotent(self) -> None:
        state = PerfettoTrackState()
        uuid1 = state.get_or_create_counter_track_uuid(100, 0, "G0", "collected")
        uuid2 = state.get_or_create_counter_track_uuid(100, 0, "G0", "collected")
        assert uuid1 == uuid2

    def test_has_counter_track(self) -> None:
        state = PerfettoTrackState()
        assert not state.has_counter_track(100, 0, "G0", "collected")
        state.get_or_create_counter_track_uuid(100, 0, "G0", "collected")
        assert state.has_counter_track(100, 0, "G0", "collected")
        assert not state.has_counter_track(100, 0, "G1", "collected")


class TestProcessLifetimeState:
    """State accessors for the shared ``Processes`` track."""

    def test_track_uuid_lazy_and_idempotent(self) -> None:
        state = PerfettoTrackState()
        assert not state.has_process_lifetime_track()
        uuid1 = state.get_or_create_process_lifetime_track_uuid()
        assert state.has_process_lifetime_track()
        uuid2 = state.get_or_create_process_lifetime_track_uuid()
        assert uuid1 == uuid2

    def test_track_uuid_distinct_from_process_uuid(self) -> None:
        state = PerfettoTrackState()
        proc_uuid = state.get_process_track_uuid(100)
        lifetime_uuid = state.get_or_create_process_lifetime_track_uuid()
        assert lifetime_uuid != proc_uuid

    def test_open_is_idempotent(self) -> None:
        state = PerfettoTrackState()
        assert not state.has_process_lifetime(100)
        state.mark_process_lifetime_opened(100)
        assert state.has_process_lifetime(100)
        state.mark_process_lifetime_opened(100)  # no-op
        assert state.has_process_lifetime(100)
        assert not state.has_process_lifetime(200)

    def test_end_ts_update_overwrites(self) -> None:
        state = PerfettoTrackState()
        state.update_process_lifetime_end_ts(100, 1_000)
        state.update_process_lifetime_end_ts(100, 2_000)
        state.update_process_lifetime_end_ts(100, 1_500)
        ends = dict(state.pop_process_lifetime_ends())
        assert ends == {100: 1_500}

    def test_pop_returns_sorted_by_end_then_pid(self) -> None:
        state = PerfettoTrackState()
        # Pids intentionally in pid-asc order, end-ts intentionally in
        # reverse order so that the sort key is non-trivial.
        state.update_process_lifetime_end_ts(200, 1_000)
        state.update_process_lifetime_end_ts(100, 2_000)
        state.update_process_lifetime_end_ts(300, 1_000)
        ends = state.pop_process_lifetime_ends()
        # Expected order: (1_000, 200), (1_000, 300), (2_000, 100).
        assert ends == [(200, 1_000), (300, 1_000), (100, 2_000)]

    def test_pop_clears_end_ts_but_keeps_opened(self) -> None:
        state = PerfettoTrackState()
        state.update_process_lifetime_end_ts(100, 1_000)
        state.mark_process_lifetime_opened(100)
        state.pop_process_lifetime_ends()
        assert state.has_process_lifetime(100)
        assert state.pop_process_lifetime_ends() == []


class TestBuildTrackDescriptor:
    def test_process_descriptor(self) -> None:
        data = build_track_descriptor(uuid=100, name="Process 100", pid=100)
        fields = decode_message(data)
        assert get_varint(fields, TrackDescriptorField.UUID) == 100
        assert get_string(fields, TrackDescriptorField.NAME) == "Process 100"
        assert get_field(fields, TrackDescriptorField.THREAD) is None
        assert get_field(fields, TrackDescriptorField.PARENT_UUID) is None
        assert get_field(fields, TrackDescriptorField.COUNTER) is None
        proc_desc_bytes = get_bytes(fields, TrackDescriptorField.PROCESS)
        assert proc_desc_bytes is not None
        proc_fields = decode_message(proc_desc_bytes)
        assert get_varint(proc_fields, ProcessDescriptorField.PID) == 100
        assert get_string(proc_fields, ProcessDescriptorField.PROCESS_NAME) == "Process 100"

    def test_process_descriptor_with_cmdline(self) -> None:
        data = build_track_descriptor(
            uuid=100,
            name="Process 100",
            pid=100,
            cmdline=["python", "-u", "script.py", "--arg1"],
            description="python -u script.py --arg1",
        )
        fields = decode_message(data)
        assert get_string(fields, TrackDescriptorField.DESCRIPTION) == "python -u script.py --arg1"
        proc_desc_bytes = get_bytes(fields, TrackDescriptorField.PROCESS)
        assert proc_desc_bytes is not None
        proc_fields = decode_message(proc_desc_bytes)
        assert get_varint(proc_fields, ProcessDescriptorField.PID) == 100
        assert get_string(proc_fields, ProcessDescriptorField.PROCESS_NAME) == "Process 100"
        cmdline_entries = get_fields(proc_fields, ProcessDescriptorField.CMDLINE)
        assert len(cmdline_entries) == 4
        assert cmdline_entries[0].value == b"python"
        assert cmdline_entries[1].value == b"-u"
        assert cmdline_entries[2].value == b"script.py"
        assert cmdline_entries[3].value == b"--arg1"

    def test_process_descriptor_no_cmdline_when_none(self) -> None:
        data = build_track_descriptor(uuid=100, name="Process 100", pid=100)
        fields = decode_message(data)
        assert get_field(fields, TrackDescriptorField.DESCRIPTION) is None
        proc_desc_bytes = get_bytes(fields, TrackDescriptorField.PROCESS)
        assert proc_desc_bytes is not None
        proc_fields = decode_message(proc_desc_bytes)
        assert get_fields(proc_fields, ProcessDescriptorField.CMDLINE) == []

    def test_process_descriptor_no_cmdline_when_empty(self) -> None:
        data = build_track_descriptor(uuid=100, name="Process 100", pid=100, cmdline=[])
        fields = decode_message(data)
        assert get_field(fields, TrackDescriptorField.DESCRIPTION) is None
        proc_desc_bytes = get_bytes(fields, TrackDescriptorField.PROCESS)
        assert proc_desc_bytes is not None
        proc_fields = decode_message(proc_desc_bytes)
        assert get_fields(proc_fields, ProcessDescriptorField.CMDLINE) == []

    def test_thread_descriptor(self) -> None:
        data = build_track_descriptor(
            uuid=200,
            name="Thread 0",
            pid=100,
            tid=0,
            parent_uuid=100,
            sibling_order_rank=0,
        )
        fields = decode_message(data)
        assert get_varint(fields, TrackDescriptorField.UUID) == 200
        assert get_string(fields, TrackDescriptorField.NAME) == "Thread 0"
        assert get_varint(fields, TrackDescriptorField.PARENT_UUID) == 100
        assert get_varint(fields, TrackDescriptorField.SIBLING_ORDER_RANK) == 0
        thread_desc_bytes = get_bytes(fields, TrackDescriptorField.THREAD)
        assert thread_desc_bytes is not None
        thread_fields = decode_message(thread_desc_bytes)
        assert get_varint(thread_fields, ThreadDescriptorField.PID) == 100
        assert get_varint(thread_fields, ThreadDescriptorField.TID) == 0

    def test_counter_descriptor(self) -> None:
        data = build_track_descriptor(uuid=300, name="G0 collected", parent_uuid=200, is_counter=True)
        fields = decode_message(data)
        assert get_varint(fields, TrackDescriptorField.UUID) == 300
        assert get_string(fields, TrackDescriptorField.NAME) == "G0 collected"
        assert get_varint(fields, TrackDescriptorField.PARENT_UUID) == 200
        assert get_bytes(fields, TrackDescriptorField.COUNTER) == b""

    def test_counter_descriptor_with_share_key(self) -> None:
        data = build_track_descriptor(
            uuid=300,
            name="G0 collected",
            parent_uuid=200,
            is_counter=True,
            y_axis_share_key="collected",
        )
        fields = decode_message(data)
        assert get_varint(fields, TrackDescriptorField.UUID) == 300
        assert get_string(fields, TrackDescriptorField.NAME) == "G0 collected"
        assert get_varint(fields, TrackDescriptorField.PARENT_UUID) == 200
        counter_bytes = get_bytes(fields, TrackDescriptorField.COUNTER)
        assert counter_bytes is not None
        assert counter_bytes != b""
        counter_fields = decode_message(counter_bytes)
        assert get_string(counter_fields, CounterDescriptorField.Y_AXIS_SHARE_KEY) == "collected"

    def test_process_descriptor_with_start_timestamp_ns(self) -> None:
        data = build_track_descriptor(
            uuid=100,
            name="Process 100",
            pid=100,
            start_timestamp_ns=1_700_000_000_123_456_789,
        )
        fields = decode_message(data)
        proc_bytes = get_bytes(fields, TrackDescriptorField.PROCESS)
        assert proc_bytes is not None
        proc_fields = decode_message(proc_bytes)
        assert get_varint(proc_fields, ProcessDescriptorField.START_TIMESTAMP_NS) == 1_700_000_000_123_456_789

    def test_process_descriptor_without_start_timestamp_ns(self) -> None:
        """No start_timestamp_ns is written when the kwarg is omitted
        (default ``None``). The field must be absent from the bytes."""
        data = build_track_descriptor(uuid=100, name="Process 100", pid=100)
        fields = decode_message(data)
        proc_bytes = get_bytes(fields, TrackDescriptorField.PROCESS)
        assert proc_bytes is not None
        proc_fields = decode_message(proc_bytes)
        assert get_field(proc_fields, ProcessDescriptorField.START_TIMESTAMP_NS) is None

    def test_thread_descriptor_ignores_start_timestamp_ns(self) -> None:
        """``start_timestamp_ns`` is only valid on a process
        descriptor. A thread descriptor built with the kwarg must NOT
        emit it (the field is wrapped in a sub-message that we only
        emit for process descriptors)."""
        data = build_track_descriptor(
            uuid=200,
            name="Thread 0",
            pid=100,
            tid=0,
            parent_uuid=100,
            start_timestamp_ns=1_000,
        )
        fields = decode_message(data)
        thread_bytes = get_bytes(fields, TrackDescriptorField.THREAD)
        assert thread_bytes is not None
        thread_fields = decode_message(thread_bytes)
        assert get_field(thread_fields, ProcessDescriptorField.START_TIMESTAMP_NS) is None


class TestBuildCounterDescriptor:
    """Wire-level tests for ``build_track_descriptor``'s
    ``y_axis_share_key`` kwarg and the resulting ``CounterDescriptor``
    submessage payload at ``TrackDescriptor.counter`` (field 8)."""

    def test_y_axis_share_key_emitted_at_field_8(self) -> None:
        data = build_track_descriptor(
            uuid=300,
            name="G0 collected",
            parent_uuid=200,
            is_counter=True,
            y_axis_share_key="collected",
        )
        fields = decode_message(data)
        counter_bytes = get_bytes(fields, TrackDescriptorField.COUNTER)
        assert counter_bytes is not None
        assert counter_bytes != b""
        counter_fields = decode_message(counter_bytes)
        assert get_string(counter_fields, CounterDescriptorField.Y_AXIS_SHARE_KEY) == "collected"

    def test_no_y_axis_share_key_emits_empty_submessage(self) -> None:
        data = build_track_descriptor(
            uuid=300,
            name="G0 collected",
            parent_uuid=200,
            is_counter=True,
        )
        fields = decode_message(data)
        assert get_bytes(fields, TrackDescriptorField.COUNTER) == b""

    def test_y_axis_share_key_ignored_for_non_counter_track(self) -> None:
        data = build_track_descriptor(
            uuid=300,
            name="Track With Key",
            parent_uuid=200,
            is_counter=False,
            y_axis_share_key="ignored",
        )
        fields = decode_message(data)
        assert get_field(fields, TrackDescriptorField.COUNTER) is None

    def test_only_share_key_field_is_set_no_other_counter_fields(self) -> None:
        data = build_track_descriptor(
            uuid=300,
            name="G0 duration",
            parent_uuid=200,
            is_counter=True,
            y_axis_share_key="duration",
        )
        fields = decode_message(data)
        counter_bytes = get_bytes(fields, TrackDescriptorField.COUNTER)
        assert counter_bytes is not None
        counter_fields = decode_message(counter_bytes)
        assert len(counter_fields) == 1
        assert get_field(counter_fields, CounterDescriptorField.TYPE) is None
        assert get_field(counter_fields, CounterDescriptorField.CATEGORIES) is None
        assert get_field(counter_fields, CounterDescriptorField.UNIT) is None
        assert get_field(counter_fields, CounterDescriptorField.UNIT_MULTIPLIER) is None
        assert get_field(counter_fields, CounterDescriptorField.IS_INCREMENTAL) is None
        assert get_field(counter_fields, CounterDescriptorField.UNIT_NAME) is None
        assert get_string(counter_fields, CounterDescriptorField.Y_AXIS_SHARE_KEY) == "duration"

    def test_y_axis_share_key_empty_string_treated_as_none(self) -> None:
        data = build_track_descriptor(
            uuid=300,
            name="G0 collected",
            parent_uuid=200,
            is_counter=True,
            y_axis_share_key="",
        )
        fields = decode_message(data)
        assert get_bytes(fields, TrackDescriptorField.COUNTER) == b""


class TestBuildTracePacket:
    def test_empty_packet(self) -> None:
        data = build_trace_packet(1)
        fields = decode_message(data)
        assert get_varint(fields, TracePacketField.SEQUENCE_ID) == 1

    def test_with_timestamp(self) -> None:
        data = build_trace_packet(1, timestamp=1_500_000_000)
        fields = decode_message(data)
        assert get_varint(fields, TracePacketField.SEQUENCE_ID) == 1
        assert get_varint(fields, TracePacketField.TIMESTAMP) == 1_500_000_000

    def test_with_track_event(self) -> None:
        event = b"\x08\x01"
        data = build_trace_packet(1, track_event=event)
        fields = decode_message(data)
        assert get_varint(fields, TracePacketField.SEQUENCE_ID) == 1
        assert get_bytes(fields, TracePacketField.TRACK_EVENT) == event

    def test_with_track_descriptor(self) -> None:
        desc = b"\x0a\x05hello"
        data = build_trace_packet(1, track_descriptor=desc)
        fields = decode_message(data)
        assert get_varint(fields, TracePacketField.SEQUENCE_ID) == 1
        assert get_bytes(fields, TracePacketField.TRACK_DESCRIPTOR) == desc

    def test_with_all_fields(self) -> None:
        event = b"\x08\x01"
        desc = b"\x0a\x05hello"
        data = build_trace_packet(42, timestamp=1000, track_event=event, track_descriptor=desc)
        fields = decode_message(data)
        assert get_varint(fields, TracePacketField.SEQUENCE_ID) == 42
        assert get_varint(fields, TracePacketField.TIMESTAMP) == 1000
        assert get_bytes(fields, TracePacketField.TRACK_EVENT) == event
        assert get_bytes(fields, TracePacketField.TRACK_DESCRIPTOR) == desc


class TestBuildTrackEvent:
    def test_slice_begin(self) -> None:
        data = build_track_event(type=TYPE_SLICE_BEGIN, track_uuid=100, name="test")
        fields = decode_message(data)
        assert get_varint(fields, TrackEventField.TYPE) == TYPE_SLICE_BEGIN
        assert get_varint(fields, TrackEventField.TRACK_UUID) == 100
        assert get_string(fields, TrackEventField.NAME) == "test"

    def test_slice_end(self) -> None:
        data = build_track_event(type=TYPE_SLICE_END, track_uuid=100)
        fields = decode_message(data)
        assert get_varint(fields, TrackEventField.TYPE) == TYPE_SLICE_END
        assert get_varint(fields, TrackEventField.TRACK_UUID) == 100
        assert get_field(fields, TrackEventField.NAME) is None

    def test_instant(self) -> None:
        data = build_track_event(type=TYPE_INSTANT, track_uuid=100, name="marker")
        fields = decode_message(data)
        assert get_varint(fields, TrackEventField.TYPE) == TYPE_INSTANT
        assert get_varint(fields, TrackEventField.TRACK_UUID) == 100
        assert get_string(fields, TrackEventField.NAME) == "marker"

    def test_counter(self) -> None:
        data = build_track_event(type=TYPE_COUNTER, track_uuid=100, counter_value=42)
        fields = decode_message(data)
        assert get_varint(fields, TrackEventField.TYPE) == TYPE_COUNTER
        assert get_varint(fields, TrackEventField.TRACK_UUID) == 100
        assert get_varint(fields, TrackEventField.COUNTER_VALUE) == 42

    def test_with_categories(self) -> None:
        data = build_track_event(
            type=TYPE_SLICE_BEGIN,
            track_uuid=100,
            name="test",
            categories=["cat1", "cat2"],
        )
        fields = decode_message(data)
        cats = get_fields(fields, TrackEventField.CATEGORIES)
        assert len(cats) == 2
        assert cats[0].value == b"cat1"
        assert cats[1].value == b"cat2"

    def test_with_debug_annotations(self) -> None:
        ann1 = b"\x0a\x03key\x10\x2a"
        ann2 = b"\x0a\x05other\x10\x64"
        data = build_track_event(
            type=TYPE_SLICE_BEGIN,
            track_uuid=100,
            name="test",
            debug_annotations=[ann1, ann2],
        )
        fields = decode_message(data)
        anns = get_fields(fields, TrackEventField.DEBUG_ANNOTATIONS)
        assert len(anns) == 2
        assert anns[0].value == ann1
        assert anns[1].value == ann2


class TestBuildTrace:
    def test_empty_trace(self) -> None:
        data = build_trace([])
        assert data == b""

    def test_single_packet(self) -> None:
        packet = b"\x40\x01"
        data = build_trace([packet])
        fields = decode_message(data)
        assert get_bytes(fields, TraceField.PACKET) == packet

    def test_multiple_packets(self) -> None:
        p1 = b"\x40\x01"
        p2 = b"\x40\x02"
        data = build_trace([p1, p2])
        fields = decode_message(data)
        packets = get_fields(fields, TraceField.PACKET)
        assert len(packets) == 2
        assert packets[0].value == p1
        assert packets[1].value == p2


class TestConvertItemToPerfettoPackets:
    def test_cmdline_emitted_once_per_pid(self) -> None:
        state = PerfettoTrackState()
        state.set_cmdline(100, ["python", "script.py"])
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
        desc1, _ = _convert_item(100, item, state, sequence_id=1)

        found_cmdline = False
        found_description = False
        for desc_bytes in desc1:
            fields = decode_message(desc_bytes)
            td_bytes = get_bytes(fields, TracePacketField.TRACK_DESCRIPTOR)
            if td_bytes:
                td_fields = decode_message(td_bytes)
                if get_string(td_fields, TrackDescriptorField.DESCRIPTION) == "python script.py":
                    found_description = True
                proc_bytes = get_bytes(td_fields, TrackDescriptorField.PROCESS)
                if proc_bytes:
                    proc_fields = decode_message(proc_bytes)
                    cmdline_entries = get_fields(proc_fields, ProcessDescriptorField.CMDLINE)
                    if cmdline_entries:
                        assert len(cmdline_entries) == 2
                        assert cmdline_entries[0].value == b"python"
                        assert cmdline_entries[1].value == b"script.py"
                        found_cmdline = True
        assert found_cmdline
        assert found_description, "description should be set when cmdline is present"

        desc2, _ = _convert_item(
            100,
            GCStatsInfo(
                gen=1,
                iid=0,
                ts_start=3_000,
                ts_stop=4_000,
                heap_size=2000,
                collections=2,
                collected=20,
                uncollectable=0,
                candidates=10,
                duration=0.002,
            ),
            state,
            sequence_id=1,
        )

        for desc_bytes in desc2:
            fields = decode_message(desc_bytes)
            td_bytes = get_bytes(fields, TracePacketField.TRACK_DESCRIPTOR)
            if td_bytes:
                td_fields = decode_message(td_bytes)
                proc_bytes = get_bytes(td_fields, TrackDescriptorField.PROCESS)
                assert proc_bytes is None

    def test_basic_item_emits_descriptors(self) -> None:
        state = PerfettoTrackState()
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
        descriptors, _ = _convert_item(100, item, state, sequence_id=1)
        assert len(descriptors) >= 2
        assert state.has_pid(100)
        assert state.has_tid(100, 0)

    def test_thread_track_has_sibling_order_rank_zero(self) -> None:
        state = PerfettoTrackState()
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
        descriptors, _ = _convert_item(100, item, state, sequence_id=1)
        proc_uuid = state.get_process_track_uuid(100)
        thread_uuid = state.get_thread_track_uuid(100, 0)
        thread_found = False
        for desc_bytes in descriptors:
            fields = decode_message(desc_bytes)
            td_bytes = get_bytes(fields, TracePacketField.TRACK_DESCRIPTOR)
            if td_bytes:
                td_fields = decode_message(td_bytes)
                uuid = get_varint(td_fields, TrackDescriptorField.UUID)
                if uuid == thread_uuid:
                    assert get_varint(td_fields, TrackDescriptorField.PARENT_UUID) == proc_uuid
                    assert get_varint(td_fields, TrackDescriptorField.SIBLING_ORDER_RANK) == 0
                    assert get_varint(td_fields, TrackDescriptorField.CHILD_ORDERING) is None
                    thread_found = True
        assert thread_found

    def test_counter_tracks_parented_to_counter_group(self) -> None:
        state = PerfettoTrackState()
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
        descriptors, _ = _convert_item(100, item, state, sequence_id=1)
        proc_uuid = state.get_process_track_uuid(100)
        group_uuid = state.get_or_create_counter_group_track_uuid(100, 0)
        assert group_uuid != proc_uuid
        group_seen = False
        per_metric_parent: dict[str, int] = {}
        for desc_bytes in descriptors:
            fields = decode_message(desc_bytes)
            td_bytes = get_bytes(fields, TracePacketField.TRACK_DESCRIPTOR)
            if td_bytes:
                td_fields = decode_message(td_bytes)
                counter_bytes = get_bytes(td_fields, TrackDescriptorField.COUNTER)
                uuid = get_varint(td_fields, TrackDescriptorField.UUID)
                if counter_bytes is not None:
                    parent_uuid = get_varint(td_fields, TrackDescriptorField.PARENT_UUID)
                    track_name = get_string(td_fields, TrackDescriptorField.NAME)
                    per_metric_parent[track_name] = parent_uuid
                elif uuid == group_uuid:
                    group_seen = True
                    assert get_varint(td_fields, TrackDescriptorField.PARENT_UUID) == proc_uuid
                    assert get_varint(td_fields, TrackDescriptorField.CHILD_ORDERING) == 3
        assert group_seen, "GC Counters group track descriptor was not emitted"
        # heap_size is a top-level counter: parented directly to the process.
        assert per_metric_parent["heap_size"] == proc_uuid
        # Per-gen counters are parented to the GC Counters group.
        for name, parent_uuid in per_metric_parent.items():
            if name != "heap_size":
                assert parent_uuid == group_uuid, f"{name!r} should parent to group"

    def test_basic_item_emits_pause_slice(self) -> None:
        state = PerfettoTrackState()
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
        _, packets = _convert_item(100, item, state, sequence_id=1)
        # Three packets are emitted before the GC pause slice: the
        # synthetic "Start Process" marker on the process track, then
        # the "Process 100" slice begin on the shared "Processes" track,
        # then the GC pause slice begin on the thread track. Find the
        # GC pause slice by name to disambiguate.
        assert len(packets) >= 3
        lifetime_uuid = state.get_or_create_process_lifetime_track_uuid()

        def _packet_name(p: bytes) -> str | None:
            pf = decode_message(p)
            te_bytes = get_bytes(pf, TracePacketField.TRACK_EVENT)
            if not te_bytes:
                return None
            tef = decode_message(te_bytes)
            return get_string(tef, TrackEventField.NAME)

        begin_packet = next(
            p
            for p in packets
            if (
                lambda f: (
                    get_varint(f, TrackEventField.TYPE) == TYPE_SLICE_BEGIN
                    and get_varint(f, TrackEventField.TRACK_UUID) != lifetime_uuid
                    and _packet_name(p) == "GC Pause (gen=0)"
                )
            )(decode_message(get_bytes(decode_message(p), TracePacketField.TRACK_EVENT) or b""))
        )
        first_packet_fields = decode_message(begin_packet)
        assert get_varint(first_packet_fields, TracePacketField.TIMESTAMP) == 1_000
        track_event_bytes = get_bytes(first_packet_fields, TracePacketField.TRACK_EVENT)
        assert track_event_bytes is not None
        te_fields = decode_message(track_event_bytes)
        assert get_varint(te_fields, TrackEventField.TYPE) == TYPE_SLICE_BEGIN
        assert get_string(te_fields, TrackEventField.NAME) == "GC Pause (gen=0)"

    def test_basic_item_emits_counter_events(self) -> None:
        state = PerfettoTrackState()
        item = GCStatsInfo(
            gen=0,
            iid=0,
            ts_start=1_000,
            ts_stop=2_000,
            heap_size=1000,
            collections=1,
            collected=10,
            uncollectable=2,
            candidates=5,
            duration=0.001,
        )
        _, packets = _convert_item(100, item, state, sequence_id=1)
        counter_packets = []
        for p in packets:
            fields = decode_message(p)
            te_bytes = get_bytes(fields, TracePacketField.TRACK_EVENT)
            if te_bytes:
                te_fields = decode_message(te_bytes)
                if get_varint(te_fields, TrackEventField.TYPE) == TYPE_COUNTER:
                    counter_packets.append((fields, te_fields))
        assert len(counter_packets) == 5
        values = [get_varint(track_event, TrackEventField.COUNTER_VALUE) for _, track_event in counter_packets]
        assert 10 in values
        assert 2 in values
        assert 5 in values
        assert 1000 in values
        # The `duration` value is encoded as a double (DOUBLE_COUNTER_VALUE,
        # field 44), not as a varint counter_value. Verify it is present.
        double_values = [
            get_double(track_event, TrackEventField.DOUBLE_COUNTER_VALUE) for _, track_event in counter_packets
        ]
        assert 0.001 in double_values

    def test_counter_descriptor_emitted_once(self) -> None:
        state = PerfettoTrackState()
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
        desc1, _ = _convert_item(100, item, state, sequence_id=1)
        desc2, _ = _convert_item(100, item, state, sequence_id=1)
        assert len(desc1) > 0
        assert len(desc2) == 0

    def test_invalid_timestamps_produces_events(self) -> None:
        state = PerfettoTrackState()
        item = GCStatsInfo(
            gen=0,
            iid=0,
            ts_start=2_000,
            ts_stop=1_000,
            heap_size=1000,
            collections=1,
            collected=10,
            uncollectable=0,
            candidates=5,
            duration=0.001,
        )
        descriptors, packets = _convert_item(100, item, state, sequence_id=1)
        assert len(descriptors) >= 2
        assert len(packets) >= 2

    def test_equal_timestamps_produces_events(self) -> None:
        state = PerfettoTrackState()
        item = GCStatsInfo(
            gen=0,
            iid=0,
            ts_start=1_000,
            ts_stop=1_000,
            heap_size=1000,
            collections=1,
            collected=10,
            uncollectable=0,
            candidates=5,
            duration=0.0,
        )
        descriptors, packets = _convert_item(100, item, state, sequence_id=1)
        assert len(descriptors) >= 2
        assert len(packets) >= 2

    def test_incremental_item_emits_subphases(self) -> None:
        state = PerfettoTrackState()
        item = GCStatsInfo(
            gen=1,
            iid=0,
            ts_start=3_000,
            ts_stop=4_000,
            heap_size=2048,
            collections=10,
            collected=100,
            uncollectable=1,
            candidates=20,
            duration=0.01,
            increment_size=500,
            alive_size=300,
            ts_mark_alive_start=3_000,
            ts_mark_alive_stop=3_100,
            ts_fill_increment_start=3_100,
            ts_fill_increment_stop=3_200,
            ts_deduce_unreachable_start=3_200,
            ts_deduce_unreachable_stop=3_300,
            ts_handle_weakref_callbacks_start=3_300,
            ts_handle_weakref_callbacks_stop=3_400,
            ts_finalize_garbage_stop=3_500,
            finalized_garbage_count=42,
            ts_handle_resurrected_stop=3_600,
            ts_clear_weakrefs_stop=3_700,
            clear_weakrefs_count=7,
            ts_delete_garbage_start=3_800,
            ts_delete_garbage_stop=3_900,
            deleted_garbage_count=13,
        )
        _, packets = _convert_item(100, item, state, sequence_id=1)
        slice_begins = []
        for p in packets:
            fields = decode_message(p)
            te_bytes = get_bytes(fields, TracePacketField.TRACK_EVENT)
            if te_bytes:
                te_fields = decode_message(te_bytes)
                if get_varint(te_fields, TrackEventField.TYPE) == TYPE_SLICE_BEGIN:
                    slice_begins.append(get_string(te_fields, TrackEventField.NAME))
        assert "GC Pause (gen=1)" in slice_begins
        assert "Mark Alive (gen=1)" in slice_begins
        assert "Fill increment (gen=1)" in slice_begins
        assert "Deduce Unreachable (gen=1)" in slice_begins
        assert "Handle Weakrefs Callbacks (gen=1)" in slice_begins
        assert "Finalize Garbage (gen=1)" in slice_begins
        assert "Handle Resurrected (gen=1)" in slice_begins
        assert "Clear Weakrefs (gen=1)" in slice_begins
        assert "Delete Garbage (gen=1)" in slice_begins

    def test_uncollectable_counter_omitted_when_zero(self) -> None:
        state = PerfettoTrackState()
        item = GCStatsInfo(
            gen=0,
            iid=0,
            ts_start=1_000,
            ts_stop=2_000,
            heap_size=1000,
            collections=5,
            collected=10,
            uncollectable=0,
            candidates=3,
            duration=0.001,
        )
        _, packets = _convert_item(100, item, state, sequence_id=1)
        counter_uuids: set[int] = set()
        for p in packets:
            fields = decode_message(p)
            te_bytes = get_bytes(fields, TracePacketField.TRACK_EVENT)
            if te_bytes is None:
                continue
            te_fields = decode_message(te_bytes)
            if get_varint(te_fields, TrackEventField.TYPE) != TYPE_COUNTER:
                continue
            uuid = get_varint(te_fields, TrackEventField.TRACK_UUID)
            if uuid is not None:
                counter_uuids.add(uuid)
        # collected, candidates, heap_size, duration — no uncollectable counter.
        assert len(counter_uuids) == 4

    def test_uncollectable_counter_emitted_when_nonzero(self) -> None:
        state = PerfettoTrackState()
        item = GCStatsInfo(
            gen=0,
            iid=0,
            ts_start=1_000,
            ts_stop=2_000,
            heap_size=1000,
            collections=5,
            collected=10,
            uncollectable=2,
            candidates=3,
            duration=0.001,
        )
        _, packets = _convert_item(100, item, state, sequence_id=1)
        counter_uuids: set[int] = set()
        for p in packets:
            fields = decode_message(p)
            te_bytes = get_bytes(fields, TracePacketField.TRACK_EVENT)
            if te_bytes is None:
                continue
            te_fields = decode_message(te_bytes)
            if get_varint(te_fields, TrackEventField.TYPE) != TYPE_COUNTER:
                continue
            uuid = get_varint(te_fields, TrackEventField.TRACK_UUID)
            if uuid is not None:
                counter_uuids.add(uuid)
        # collected, uncollectable, candidates, heap_size, duration.
        assert len(counter_uuids) == 5

    def test_duration_counter_in_gc_metrics_group(self) -> None:
        state = PerfettoTrackState()
        item = GCStatsInfo(
            gen=0,
            iid=0,
            ts_start=1_000,
            ts_stop=2_000,
            heap_size=1000,
            collections=5,
            collected=10,
            uncollectable=2,
            candidates=3,
            duration=0.42,
        )
        descriptors_packets, packets = _convert_item(100, item, state, sequence_id=1)
        # Find the per-gen `G0 duration` counter track UUID. The duration is
        # now split by generation (one `G{gen} duration` track per (pid, iid))
        # so a shared `duration` track is no longer emitted.
        duration_track_uuid: int | None = None
        for p in packets:
            fields = decode_message(p)
            te_bytes = get_bytes(fields, TracePacketField.TRACK_EVENT)
            if te_bytes is None:
                continue
            te_fields = decode_message(te_bytes)
            if (
                get_varint(te_fields, TrackEventField.TYPE) == TYPE_COUNTER
                and get_double(te_fields, TrackEventField.DOUBLE_COUNTER_VALUE) == 0.42
            ):
                duration_track_uuid = get_varint(
                    te_fields,
                    TrackEventField.TRACK_UUID,
                )
                break
        assert duration_track_uuid is not None

        # Find the matching TrackDescriptor and assert rank=4 (per-gen rank
        # for `duration` in the new layout) plus parent resolves to a track
        # named "GC Metrics".
        descriptors: dict[int, tuple[int | None, int | None, str | None]] = {}
        for p in descriptors_packets:
            fields = decode_message(p)
            td_bytes = get_bytes(fields, TracePacketField.TRACK_DESCRIPTOR)
            if td_bytes is None:
                continue
            td_fields = decode_message(td_bytes)
            uuid = get_varint(td_fields, TrackDescriptorField.UUID)
            if uuid is None:
                continue
            descriptors[uuid] = (
                get_varint(td_fields, TrackDescriptorField.PARENT_UUID),
                get_varint(td_fields, TrackDescriptorField.SIBLING_ORDER_RANK),
                get_string(td_fields, TrackDescriptorField.NAME),
            )
        assert duration_track_uuid in descriptors
        parent, rank, _ = descriptors[duration_track_uuid]
        assert rank == 4
        assert parent is not None
        assert descriptors[parent][2] == "GC Metrics"

    def _make_full_incremental_item(self) -> GCStatsInfo:
        return GCStatsInfo(
            gen=1,
            iid=0,
            ts_start=3_000,
            ts_stop=4_000,
            heap_size=2048,
            collections=10,
            collected=100,
            uncollectable=1,
            candidates=20,
            duration=0.01,
            increment_size=500,
            alive_size=300,
            ts_mark_alive_start=3_000,
            ts_mark_alive_stop=3_100,
            ts_fill_increment_start=3_100,
            ts_fill_increment_stop=3_200,
            ts_deduce_unreachable_start=3_200,
            ts_deduce_unreachable_stop=3_300,
            ts_handle_weakref_callbacks_start=3_300,
            ts_handle_weakref_callbacks_stop=3_400,
            ts_finalize_garbage_stop=3_500,
            finalized_garbage_count=42,
            ts_handle_resurrected_stop=3_600,
            ts_clear_weakrefs_stop=3_700,
            clear_weakrefs_count=7,
            ts_delete_garbage_start=3_800,
            ts_delete_garbage_stop=3_900,
            deleted_garbage_count=13,
        )

    def _annotations_for_slice(
        self,
        packets: list[bytes],
        slice_name: str,
    ) -> list[tuple[str | None, int | None]]:
        for p in packets:
            fields = decode_message(p)
            te_bytes = get_bytes(fields, TracePacketField.TRACK_EVENT)
            if not te_bytes:
                continue
            te_fields = decode_message(te_bytes)
            if get_varint(te_fields, TrackEventField.TYPE) != TYPE_SLICE_BEGIN:
                continue
            if get_string(te_fields, TrackEventField.NAME) != slice_name:
                continue
            anns = get_fields(te_fields, TrackEventField.DEBUG_ANNOTATIONS)
            out: list[tuple[str | None, int | None]] = []
            for ann in anns:
                ann_fields = decode_message(ann.value)  # type: ignore[arg-type]
                out.append(
                    (
                        get_string(ann_fields, DebugAnnotationField.NAME),
                        get_varint(ann_fields, DebugAnnotationField.INT_VALUE),
                    )
                )
            return out
        raise AssertionError(f"slice {slice_name!r} not found in packets")

    def test_finalize_garbage_substep_has_count_annotation(self) -> None:
        state = PerfettoTrackState()
        _, packets = _convert_item(100, self._make_full_incremental_item(), state, sequence_id=1)
        anns = self._annotations_for_slice(packets, "Finalize Garbage (gen=1)")
        assert ("finalized_garbage_count", 42) in anns
        assert all(name not in ("deleted_garbage_count", "clear_weakrefs_count") for name, _ in anns)

    def test_clear_weakrefs_substep_has_count_annotation(self) -> None:
        state = PerfettoTrackState()
        _, packets = _convert_item(100, self._make_full_incremental_item(), state, sequence_id=1)
        anns = self._annotations_for_slice(packets, "Clear Weakrefs (gen=1)")
        assert ("clear_weakrefs_count", 7) in anns
        assert all(name not in ("finalized_garbage_count", "deleted_garbage_count") for name, _ in anns)

    def test_delete_garbage_substep_has_count_annotation(self) -> None:
        state = PerfettoTrackState()
        _, packets = _convert_item(100, self._make_full_incremental_item(), state, sequence_id=1)
        anns = self._annotations_for_slice(packets, "Delete Garbage (gen=1)")
        assert ("deleted_garbage_count", 13) in anns
        assert all(name not in ("finalized_garbage_count", "clear_weakrefs_count") for name, _ in anns)

    def test_deduce_unreachable_substep_has_candidates_annotation(self) -> None:
        state = PerfettoTrackState()
        item = self._make_full_incremental_item()
        _, packets = _convert_item(100, item, state, sequence_id=1)
        anns = self._annotations_for_slice(packets, "Deduce Unreachable (gen=1)")
        assert ("candidates", item.candidates) in anns
        assert ("generation", 1) in anns

    def test_zero_duration_subphase_skipped(self) -> None:
        state = PerfettoTrackState()
        item = GCStatsInfo(
            gen=1,
            iid=0,
            ts_start=3_000,
            ts_stop=4_000,
            heap_size=2048,
            collections=10,
            collected=100,
            uncollectable=1,
            candidates=20,
            duration=0.01,
            increment_size=500,
            alive_size=300,
            ts_mark_alive_start=3_000,
            ts_mark_alive_stop=3_000,
            ts_fill_increment_start=3_100,
            ts_fill_increment_stop=3_200,
        )
        _, packets = _convert_item(100, item, state, sequence_id=1)
        slice_names = []
        for p in packets:
            fields = decode_message(p)
            te_bytes = get_bytes(fields, TracePacketField.TRACK_EVENT)
            if te_bytes:
                te_fields = decode_message(te_bytes)
                if get_varint(te_fields, TrackEventField.TYPE) == TYPE_SLICE_BEGIN:
                    slice_names.append(get_string(te_fields, TrackEventField.NAME))
        assert "Mark Alive (gen=1)" not in slice_names
        assert "Fill increment (gen=1)" in slice_names

    def test_multiple_threads(self) -> None:
        state = PerfettoTrackState()
        item0 = GCStatsInfo(
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
        item1 = GCStatsInfo(
            gen=0,
            iid=1,
            ts_start=1_000,
            ts_stop=2_000,
            heap_size=1000,
            collections=1,
            collected=10,
            uncollectable=0,
            candidates=5,
            duration=0.001,
        )
        desc0, _ = _convert_item(100, item0, state, sequence_id=1)
        desc1, _ = _convert_item(100, item1, state, sequence_id=1)
        assert len(desc0) >= 2
        assert len(desc1) >= 1
        assert state.has_tid(100, 0)
        assert state.has_tid(100, 1)

    def test_debug_annotation_name_wire_format(self) -> None:
        state = PerfettoTrackState()
        item = GCStatsInfo(
            gen=0,
            iid=0,
            ts_start=1_000,
            ts_stop=2_000,
            heap_size=1000,
            collections=5,
            collected=10,
            uncollectable=2,
            candidates=3,
            duration=0.001,
        )
        _, packets = _convert_item(100, item, state, sequence_id=1)
        # Three packets precede the GC pause slice begin: the synthetic
        # "Start Process" marker, the "Process 100" slice begin on the
        # shared "Processes" track, and any other warm-up events.
        # Identify the GC pause slice by its name.
        lifetime_uuid = state.get_or_create_process_lifetime_track_uuid()
        begin_packet = next(
            p
            for p in packets
            if (
                lambda f: (
                    get_varint(f, TrackEventField.TYPE) == TYPE_SLICE_BEGIN
                    and get_varint(f, TrackEventField.TRACK_UUID) != lifetime_uuid
                    and get_string(f, TrackEventField.NAME) == "GC Pause (gen=0)"
                )
            )(decode_message(get_bytes(decode_message(p), TracePacketField.TRACK_EVENT) or b""))
        )
        first_packet_fields = decode_message(begin_packet)
        te_bytes = get_bytes(first_packet_fields, TracePacketField.TRACK_EVENT)
        assert te_bytes is not None
        te_fields = decode_message(te_bytes)
        anns = get_fields(te_fields, TrackEventField.DEBUG_ANNOTATIONS)
        assert len(anns) == 7
        for ann in anns:
            ann_fields = decode_message(ann.value)  # type: ignore[arg-type]
            name_field_1 = get_field(ann_fields, 1)
            assert name_field_1 is None or name_field_1.wire_type != 2, (
                "field 1 of DebugAnnotation is `name_iid` (uint64); the annotation name must not be written there"
            )
            assert get_string(ann_fields, 10) is not None
            assert get_string(ann_fields, DebugAnnotationField.NAME) is not None

    def test_debug_annotations_on_pause(self) -> None:
        state = PerfettoTrackState()
        item = GCStatsInfo(
            gen=0,
            iid=0,
            ts_start=1_000,
            ts_stop=2_000,
            heap_size=1000,
            collections=5,
            collected=10,
            uncollectable=2,
            candidates=3,
            duration=0.001,
        )
        _, packets = _convert_item(100, item, state, sequence_id=1)
        # Disambiguate by name (and exclude the spec-15 "Processes" track
        # slice begin) to find the GC pause slice.
        lifetime_uuid = state.get_or_create_process_lifetime_track_uuid()
        begin_packet = next(
            p
            for p in packets
            if (
                lambda f: (
                    get_varint(f, TrackEventField.TYPE) == TYPE_SLICE_BEGIN
                    and get_varint(f, TrackEventField.TRACK_UUID) != lifetime_uuid
                    and get_string(f, TrackEventField.NAME) == "GC Pause (gen=0)"
                )
            )(decode_message(get_bytes(decode_message(p), TracePacketField.TRACK_EVENT) or b""))
        )
        first_packet_fields = decode_message(begin_packet)
        te_bytes = get_bytes(first_packet_fields, 11)
        assert te_bytes is not None
        te_fields = decode_message(te_bytes)
        anns = get_fields(te_fields, TrackEventField.DEBUG_ANNOTATIONS)
        assert len(anns) == 7
        ann_values = []
        for ann in anns:
            ann_fields = decode_message(ann.value)  # type: ignore[arg-type]
            name = get_string(ann_fields, DebugAnnotationField.NAME)
            val = get_varint(ann_fields, DebugAnnotationField.INT_VALUE)
            ann_values.append((name, val))
        assert ("generation", 0) in ann_values
        assert ("iid", 0) in ann_values
        assert ("collections", 5) in ann_values
        assert ("heap_size", 1000) in ann_values
        assert ("collected", 10) in ann_values
        assert ("uncollectable", 2) in ann_values
        assert ("candidates", 3) in ann_values

    def test_process_lifetime_track_emitted_once(self) -> None:
        """The ``Processes`` track descriptor is emitted at most
        once for a single pid, even across multiple convert passes."""
        state = PerfettoTrackState()
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
        desc1, _ = _convert_item(100, item, state, sequence_id=1)
        desc2, _ = _convert_item(
            100,
            GCStatsInfo(
                gen=1,
                iid=0,
                ts_start=3_000,
                ts_stop=4_000,
                heap_size=2000,
                collections=2,
                collected=20,
                uncollectable=0,
                candidates=10,
                duration=0.002,
            ),
            state,
            sequence_id=1,
        )
        lifetime_uuid = state.get_or_create_process_lifetime_track_uuid()
        # All "Processes" track descriptors in desc1 + desc2 must share
        # the same UUID and there must be exactly one.
        seen = 0
        for desc_bytes in (*desc1, *desc2):
            fields = decode_message(desc_bytes)
            td_bytes = get_bytes(fields, TracePacketField.TRACK_DESCRIPTOR)
            if td_bytes:
                td_fields = decode_message(td_bytes)
                if get_varint(td_fields, TrackDescriptorField.UUID) == lifetime_uuid:
                    assert get_string(td_fields, TrackDescriptorField.NAME) == _PROCESS_LIFETIME_TRACK_NAME
                    # The descriptor carries no parent_uuid (root), no
                    # process, no thread, no counter, no child_ordering,
                    # no sibling_order_rank, no description.
                    assert get_field(td_fields, TrackDescriptorField.PARENT_UUID) is None
                    assert get_field(td_fields, TrackDescriptorField.PROCESS) is None
                    assert get_field(td_fields, TrackDescriptorField.THREAD) is None
                    assert get_field(td_fields, TrackDescriptorField.COUNTER) is None
                    assert get_field(td_fields, TrackDescriptorField.CHILD_ORDERING) is None
                    assert get_field(td_fields, TrackDescriptorField.SIBLING_ORDER_RANK) is None
                    assert get_field(td_fields, TrackDescriptorField.DESCRIPTION) is None
                    seen += 1
        assert seen == 1, f"expected exactly one Processes track descriptor, got {seen}"

    def test_process_lifetime_slice_begin_at_first_event_ts(self) -> None:
        """The ``Process <pid>`` slice BEGIN is emitted at the ts of the
        first non-meta event for the pid, on the shared ``Processes``
        track, and carries a ``cmdline`` debug annotation joined with
        single spaces when ``state`` has a cmdline recorded for the pid."""
        state = PerfettoTrackState()
        state.set_cmdline(100, ["python3", "-m", "fake_target"])
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
        _, packets = _convert_item(100, item, state, sequence_id=1)
        lifetime_uuid = state.get_or_create_process_lifetime_track_uuid()
        begin_packets = [
            p
            for p in packets
            if (
                lambda f: (
                    get_varint(f, TrackEventField.TYPE) == TYPE_SLICE_BEGIN
                    and get_varint(f, TrackEventField.TRACK_UUID) == lifetime_uuid
                )
            )(decode_message(get_bytes(decode_message(p), TracePacketField.TRACK_EVENT) or b""))
        ]
        assert len(begin_packets) == 1, f"expected exactly one slice BEGIN on Processes track, got {len(begin_packets)}"
        packet_fields = decode_message(begin_packets[0])
        assert get_varint(packet_fields, TracePacketField.TIMESTAMP) == 1_000
        te_fields = decode_message(get_bytes(packet_fields, TracePacketField.TRACK_EVENT) or b"")
        assert get_string(te_fields, TrackEventField.NAME) == "Process 100"
        annotations = get_fields(te_fields, TrackEventField.DEBUG_ANNOTATIONS)
        assert len(annotations) == 1
        ann_fields = decode_message(annotations[0].value)  # type: ignore[arg-type]
        assert get_string(ann_fields, DebugAnnotationField.NAME) == "cmdline"
        assert get_string(ann_fields, DebugAnnotationField.STRING_VALUE) == "python3 -m fake_target"

    def test_process_lifetime_slice_begin_no_cmdline_omits_arg(self) -> None:
        """When ``state`` has no cmdline for the pid, the slice BEGIN on
        the ``Processes`` track carries no debug annotations."""
        state = PerfettoTrackState()
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
        _, packets = _convert_item(100, item, state, sequence_id=1)
        lifetime_uuid = state.get_or_create_process_lifetime_track_uuid()
        begin_packets = [
            p
            for p in packets
            if (
                lambda f: (
                    get_varint(f, TrackEventField.TYPE) == TYPE_SLICE_BEGIN
                    and get_varint(f, TrackEventField.TRACK_UUID) == lifetime_uuid
                )
            )(decode_message(get_bytes(decode_message(p), TracePacketField.TRACK_EVENT) or b""))
        ]
        assert len(begin_packets) == 1
        packet_fields = decode_message(begin_packets[0])
        te_fields = decode_message(get_bytes(packet_fields, TracePacketField.TRACK_EVENT) or b"")
        assert get_field(te_fields, TrackEventField.DEBUG_ANNOTATIONS) is None

    def test_process_lifetime_slice_end_at_last_event_ts(self) -> None:
        """The ``Process <pid>`` slice END is emitted at the ts of the
        last non-meta event for the pid, on the shared ``Processes``
        track."""
        state = PerfettoTrackState()
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
        _, packets = _convert_item(100, item, state, sequence_id=1)
        lifetime_uuid = state.get_or_create_process_lifetime_track_uuid()
        end_packets = [
            p
            for p in packets
            if (
                lambda f: (
                    get_varint(f, TrackEventField.TYPE) == TYPE_SLICE_END
                    and get_varint(f, TrackEventField.TRACK_UUID) == lifetime_uuid
                )
            )(decode_message(get_bytes(decode_message(p), TracePacketField.TRACK_EVENT) or b""))
        ]
        assert len(end_packets) == 1, f"expected exactly one slice END on Processes track, got {len(end_packets)}"
        packet_fields = decode_message(end_packets[0])
        # Last non-meta event ts in this fixture is 2_000 (ts_stop).
        assert get_varint(packet_fields, TracePacketField.TIMESTAMP) == 2_000
        te_fields = decode_message(get_bytes(packet_fields, TracePacketField.TRACK_EVENT) or b"")
        assert get_string(te_fields, TrackEventField.NAME) == "Process 100"

    def test_process_lifetime_two_pids_one_shared_track(self) -> None:
        """Two distinct pids share the same ``Processes`` track UUID and
        each get their own slice pair, ordered by end-ts at closeout.
        Each BEGIN carries a ``cmdline`` annotation reflecting that
        pid's recorded cmdline."""
        state = PerfettoTrackState()
        state.set_cmdline(100, ["python3", "-m", "early_target"])
        state.set_cmdline(200, ["python3", "-m", "late_target"])
        item_late_pid = GCStatsInfo(
            gen=0,
            iid=0,
            ts_start=1_000,
            ts_stop=5_000,
            heap_size=1000,
            collections=1,
            collected=10,
            uncollectable=0,
            candidates=5,
            duration=0.001,
        )
        item_early_pid = GCStatsInfo(
            gen=0,
            iid=0,
            ts_start=500,
            ts_stop=1_500,
            heap_size=1000,
            collections=1,
            collected=10,
            uncollectable=0,
            candidates=5,
            duration=0.001,
        )
        _, packets_late = _convert_item(200, item_late_pid, state, sequence_id=1)
        _, packets_early = _convert_item(100, item_early_pid, state, sequence_id=1)
        lifetime_uuid = state.get_or_create_process_lifetime_track_uuid()

        def _slice_pairs(packets: list[bytes]) -> list[tuple[int, int, str, str | None]]:
            """Return ``[(ts, type, name, cmdline_arg), ...]`` for slice
            events on the ``Processes`` track. ``cmdline_arg`` is the
            value of the ``cmdline`` debug annotation, or ``None`` if
            the BEGIN has no such annotation."""
            out: list[tuple[int, int, str, str | None]] = []
            for p in packets:
                pf = decode_message(p)
                te_bytes = get_bytes(pf, TracePacketField.TRACK_EVENT)
                if not te_bytes:
                    continue
                tef = decode_message(te_bytes)
                if get_varint(tef, TrackEventField.TRACK_UUID) != lifetime_uuid:
                    continue
                event_type = get_varint(tef, TrackEventField.TYPE) or 0
                if event_type not in (TYPE_SLICE_BEGIN, TYPE_SLICE_END):
                    continue
                cmdline_arg: str | None = None
                if event_type == TYPE_SLICE_BEGIN:
                    anns = get_fields(tef, TrackEventField.DEBUG_ANNOTATIONS)
                    if anns:
                        ann_fields = decode_message(anns[0].value)  # type: ignore[arg-type]
                        if get_string(ann_fields, DebugAnnotationField.NAME) == "cmdline":
                            cmdline_arg = get_string(ann_fields, DebugAnnotationField.STRING_VALUE)
                out.append(
                    (
                        get_varint(pf, TracePacketField.TIMESTAMP) or 0,
                        event_type,
                        get_string(tef, TrackEventField.NAME) or "",
                        cmdline_arg,
                    )
                )
            return out

        all_pairs = _slice_pairs(packets_late) + _slice_pairs(packets_early)
        # Closeout runs at the end of each convert call. So:
        # - packets_late (pid 200) contains BEGIN(pid 200) at ts=1000
        #   and END(pid 200) at ts=5000.
        # - packets_early (pid 100) contains BEGIN(pid 100) at ts=500
        #   and END(pid 100) at ts=1500.
        begins = [p for p in all_pairs if p[1] == TYPE_SLICE_BEGIN]
        ends = [p for p in all_pairs if p[1] == TYPE_SLICE_END]
        assert begins == [
            (1_000, TYPE_SLICE_BEGIN, "Process 200", "python3 -m late_target"),
            (500, TYPE_SLICE_BEGIN, "Process 100", "python3 -m early_target"),
        ]
        # END packets carry no annotations.
        assert ends == [
            (5_000, TYPE_SLICE_END, "Process 200", None),
            (1_500, TYPE_SLICE_END, "Process 100", None),
        ]

    def test_process_lifetime_idempotent_across_converts(self) -> None:
        """Two convert passes for the same pid emit only one slice BEGIN
        (the second pass updates the end-ts only). The single BEGIN
        carries the pid's recorded cmdline annotation."""
        state = PerfettoTrackState()
        state.set_cmdline(100, ["python3", "-m", "fake_target"])
        item1 = GCStatsInfo(
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
        item2 = GCStatsInfo(
            gen=1,
            iid=0,
            ts_start=3_000,
            ts_stop=4_000,
            heap_size=2000,
            collections=2,
            collected=20,
            uncollectable=0,
            candidates=10,
            duration=0.002,
        )
        _, packets1 = _convert_item(100, item1, state, sequence_id=1)
        _, packets2 = _convert_item(100, item2, state, sequence_id=1)
        lifetime_uuid = state.get_or_create_process_lifetime_track_uuid()

        def _count(packets: list[bytes], event_type: int) -> int:
            n = 0
            for p in packets:
                pf = decode_message(p)
                te_bytes = get_bytes(pf, TracePacketField.TRACK_EVENT)
                if not te_bytes:
                    continue
                tef = decode_message(te_bytes)
                if (
                    get_varint(tef, TrackEventField.TRACK_UUID) == lifetime_uuid
                    and get_varint(tef, TrackEventField.TYPE) == event_type
                ):
                    n += 1
            return n

        assert _count(packets1, TYPE_SLICE_BEGIN) == 1
        assert _count(packets1, TYPE_SLICE_END) == 1
        # Second pass: no new BEGIN, no new END (end-ts updates silently,
        # the closeout pass happens at the end of the second convert).
        assert _count(packets2, TYPE_SLICE_BEGIN) == 0
        assert _count(packets2, TYPE_SLICE_END) == 1
        # Last event ts after the second pass is 4_000.
        end_packet = next(
            p
            for p in packets2
            if (
                lambda f: (
                    get_varint(f, TrackEventField.TYPE) == TYPE_SLICE_END
                    and get_varint(f, TrackEventField.TRACK_UUID) == lifetime_uuid
                )
            )(decode_message(get_bytes(decode_message(p), TracePacketField.TRACK_EVENT) or b""))
        )
        assert get_varint(decode_message(end_packet), TracePacketField.TIMESTAMP) == 4_000
        # The single BEGIN (from packets1) carries the cmdline annotation.
        begin_packet = next(
            p
            for p in packets1
            if (
                lambda f: (
                    get_varint(f, TrackEventField.TYPE) == TYPE_SLICE_BEGIN
                    and get_varint(f, TrackEventField.TRACK_UUID) == lifetime_uuid
                )
            )(decode_message(get_bytes(decode_message(p), TracePacketField.TRACK_EVENT) or b""))
        )
        te_fields = decode_message(
            get_bytes(decode_message(begin_packet), TracePacketField.TRACK_EVENT) or b"",
        )
        annotations = get_fields(te_fields, TrackEventField.DEBUG_ANNOTATIONS)
        assert len(annotations) == 1
        ann_fields = decode_message(annotations[0].value)  # type: ignore[arg-type]
        assert get_string(ann_fields, DebugAnnotationField.NAME) == "cmdline"
        assert get_string(ann_fields, DebugAnnotationField.STRING_VALUE) == "python3 -m fake_target"


class TestConvertInstantToPerfettoPacket:
    def test_emits_process_descriptor(self) -> None:
        state = PerfettoTrackState()
        events = [
            process_meta(100, "Process 100"),
            instant_event(100, "start", ts_ns=5_000),
        ]
        descriptors, _ = convert_trace_events_to_perfetto(events, state, sequence_id=1)
        # 1 root descriptor + 1 process descriptor + 1 "Processes" track descriptor.
        assert len(descriptors) == 3
        assert state.has_pid(100)

    def test_emits_instant_event(self) -> None:
        state = PerfettoTrackState()
        events = [
            process_meta(100, "Process 100"),
            instant_event(100, "start GC monitor", ts_ns=5_000),
        ]
        _, packets = convert_trace_events_to_perfetto(events, state, sequence_id=1)
        # Three packets from the convert call: the synthetic "Start
        # Process" marker (process track), the "Process 100" slice begin
        # on the shared "Processes" track, and the user-provided instant
        # event (process track). The slice END is appended by
        # finalize_perfetto_packets (the encoder's close()).
        packets.extend(finalize_perfetto_packets(state, sequence_id=1))
        assert len(packets) == 4
        names = [
            get_string(
                decode_message(get_bytes(decode_message(p), TracePacketField.TRACK_EVENT) or b""),
                TrackEventField.NAME,
            )
            for p in packets
        ]
        assert names == [
            _START_PROCESS_MARKER_NAME,
            "Process 100",
            "start GC monitor",
            "Process 100",
        ]
        instant_packet = next(
            p
            for p in packets
            if get_string(
                decode_message(get_bytes(decode_message(p), TracePacketField.TRACK_EVENT) or b""),
                TrackEventField.NAME,
            )
            == "start GC monitor"
        )
        fields = decode_message(instant_packet)
        assert get_varint(fields, TracePacketField.TIMESTAMP) == 5_000
        te_bytes = get_bytes(fields, TracePacketField.TRACK_EVENT)
        assert te_bytes is not None
        te_fields = decode_message(te_bytes)
        assert get_varint(te_fields, TrackEventField.TYPE) == TYPE_INSTANT
        assert get_string(te_fields, TrackEventField.NAME) == "start GC monitor"

    def test_reuses_process_descriptor(self) -> None:
        state = PerfettoTrackState()
        desc1, packets1 = convert_trace_events_to_perfetto(
            [process_meta(100, "Process 100"), instant_event(100, "start", ts_ns=5_000)],
            state,
            sequence_id=1,
        )
        desc2, packets2 = convert_trace_events_to_perfetto(
            [process_meta(100, "Process 100"), instant_event(100, "stop", ts_ns=10_000)],
            state,
            sequence_id=1,
        )
        # First call: 3 descriptors (root + process + "Processes" track) + 3
        # packets from the convert (marker + Process 100 begin + instant).
        # The closeout END is appended by finalize_perfetto_packets.
        # Second call: 0 descriptors (all are idempotent) + 1 packet
        # (the new instant event; no slice begin since pid is already
        # opened). The closeout END is again emitted by finalize.
        assert len(desc1) == 3
        assert len(packets1) == 3
        packets1.extend(finalize_perfetto_packets(state, sequence_id=1))
        assert len(packets1) == 4
        assert len(desc2) == 0
        assert len(packets2) == 1

    def test_instant_after_gc_event_no_duplicate_descriptor(self) -> None:
        state = PerfettoTrackState()
        gc_item = GCStatsInfo(
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
        gc_desc, _ = _convert_item(100, gc_item, state, sequence_id=1)
        inst_desc, _ = convert_trace_events_to_perfetto(
            [process_meta(100, "Process 100"), instant_event(100, "stop", ts_ns=5_000)],
            state,
            sequence_id=1,
        )
        assert len(gc_desc) >= 2
        assert len(inst_desc) == 0

    def test_single_arg_counter_uses_metric_name_as_track_name(self) -> None:
        state = PerfettoTrackState()
        descriptors, packets = convert_trace_events_to_perfetto(
            [
                process_meta(100, "Process 100"),
                thread_meta(100, 0, "Thread 0"),
                counter_event(pid=100, tid=0, name="heap_size", ts_ns=1_000, args={"heap_size": 1234}),
            ],
            state,
            sequence_id=1,
        )
        track_names: list[str] = []
        for d in descriptors:
            fields = decode_message(d)
            td_bytes = get_bytes(fields, TracePacketField.TRACK_DESCRIPTOR)
            if not td_bytes:
                continue
            td_fields = decode_message(td_bytes)
            counter_bytes = get_bytes(td_fields, TrackDescriptorField.COUNTER)
            if counter_bytes is None:
                continue
            name = get_string(td_fields, TrackDescriptorField.NAME)
            if name is not None:
                track_names.append(name)
        assert "heap_size" in track_names
        assert "heap_size heap_size" not in track_names

    def test_shared_heap_size_track_reused_across_generations(self) -> None:
        state = PerfettoTrackState()
        item_g0 = GCStatsInfo(
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
        item_g1 = GCStatsInfo(
            gen=1,
            iid=0,
            ts_start=3_000,
            ts_stop=4_000,
            heap_size=2000,
            collections=1,
            collected=10,
            uncollectable=0,
            candidates=5,
            duration=0.001,
        )
        _convert_item(100, item_g0, state, sequence_id=1)
        uuid_after_g0 = state.get_or_create_counter_track_uuid(100, 0, "heap_size", "heap_size")
        _convert_item(100, item_g1, state, sequence_id=1)
        uuid_after_g1 = state.get_or_create_counter_track_uuid(100, 0, "heap_size", "heap_size")
        assert uuid_after_g0 == uuid_after_g1

    def test_no_closeout_emitted_during_convert(self) -> None:
        """``convert_trace_events_to_perfetto`` never emits a
        ``TYPE_SLICE_END`` on the ``Processes`` track; closeout is the
        caller's job (see ``finalize_perfetto_packets``)."""
        state = PerfettoTrackState()
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
        gc_events = convert_item_to_trace_format(100, item)
        meta = [
            process_meta(100, "Process 100"),
            thread_meta(100, item.iid, f"Thread {item.iid}"),
        ]
        _, packets = convert_trace_events_to_perfetto(
            meta + gc_events,
            state,
            sequence_id=1,
        )
        lifetime_uuid = state.get_or_create_process_lifetime_track_uuid()
        end_packets = [
            p
            for p in packets
            if (
                lambda f: (
                    get_varint(f, TrackEventField.TYPE) == TYPE_SLICE_END
                    and get_varint(f, TrackEventField.TRACK_UUID) == lifetime_uuid
                )
            )(decode_message(get_bytes(decode_message(p), TracePacketField.TRACK_EVENT) or b""))
        ]
        assert end_packets == [], (
            f"convert_trace_events_to_perfetto must not emit slice END "
            f"on the Processes track; got {len(end_packets)} ENDs"
        )
        # Calling finalize_perfetto_packets now produces exactly one END.
        closeout = finalize_perfetto_packets(state, sequence_id=1)
        end_packets = [
            p
            for p in closeout
            if (
                lambda f: (
                    get_varint(f, TrackEventField.TYPE) == TYPE_SLICE_END
                    and get_varint(f, TrackEventField.TRACK_UUID) == lifetime_uuid
                )
            )(decode_message(get_bytes(decode_message(p), TracePacketField.TRACK_EVENT) or b""))
        ]
        assert len(end_packets) == 1
        assert (
            get_varint(
                decode_message(end_packets[0]),
                TracePacketField.TIMESTAMP,
            )
            == 2_000
        )

    def test_closeout_emitted_only_at_finalize(self) -> None:
        """Across two ``convert_trace_events_to_perfetto`` calls for the
        same pid, the convert call never emits a slice END on the
        ``Processes`` track (the END is the caller's job, and
        ``finalize_perfetto_packets`` is called exactly once at the end
        of the trace). The single END's ts is the last non-counter
        non-meta event ts of the *second* convert call, not the first.
        """
        state = PerfettoTrackState()
        item1 = GCStatsInfo(
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
        item2 = GCStatsInfo(
            gen=1,
            iid=1,
            ts_start=3_000,
            ts_stop=4_000,
            heap_size=2000,
            collections=2,
            collected=20,
            uncollectable=0,
            candidates=10,
            duration=0.002,
        )
        events1 = [
            process_meta(100, "Process 100"),
            thread_meta(100, item1.iid, f"Thread {item1.iid}"),
            *convert_item_to_trace_format(100, item1),
        ]
        events2 = [
            process_meta(100, "Process 100"),
            thread_meta(100, item2.iid, f"Thread {item2.iid}"),
            *convert_item_to_trace_format(100, item2),
        ]
        _, packets1 = convert_trace_events_to_perfetto(
            events1,
            state,
            sequence_id=1,
        )
        _, packets2 = convert_trace_events_to_perfetto(
            events2,
            state,
            sequence_id=1,
        )
        # finalize is called exactly once at the end (mimicking
        # encoder.close()).
        closeout = finalize_perfetto_packets(state, sequence_id=1)
        all_packets = packets1 + packets2 + closeout
        lifetime_uuid = state.get_or_create_process_lifetime_track_uuid()

        def _count(packets: list[bytes], event_type: int) -> int:
            n = 0
            for p in packets:
                pf = decode_message(p)
                te_bytes = get_bytes(pf, TracePacketField.TRACK_EVENT)
                if not te_bytes:
                    continue
                tef = decode_message(te_bytes)
                if (
                    get_varint(tef, TrackEventField.TRACK_UUID) == lifetime_uuid
                    and get_varint(tef, TrackEventField.TYPE) == event_type
                ):
                    n += 1
            return n

        # First batch: BEGIN emitted (first non-meta event), no END.
        assert _count(packets1, TYPE_SLICE_BEGIN) == 1
        assert _count(packets1, TYPE_SLICE_END) == 0
        # Second batch: no new BEGIN (state has the "opened" flag), no
        # END (convert never emits ENDs).
        assert _count(packets2, TYPE_SLICE_BEGIN) == 0
        assert _count(packets2, TYPE_SLICE_END) == 0
        # The finalize pass: exactly one END.
        assert _count(closeout, TYPE_SLICE_END) == 1
        assert _count(closeout, TYPE_SLICE_BEGIN) == 0
        # Across the union, exactly one BEGIN and one END.
        assert _count(all_packets, TYPE_SLICE_BEGIN) == 1
        assert _count(all_packets, TYPE_SLICE_END) == 1
        # The single END's ts is the last non-counter non-meta event ts
        # of the *second* batch (4_000), not the first (2_000).
        end_packet = next(
            p
            for p in all_packets
            if (
                lambda f: (
                    get_varint(f, TrackEventField.TYPE) == TYPE_SLICE_END
                    and get_varint(f, TrackEventField.TRACK_UUID) == lifetime_uuid
                )
            )(decode_message(get_bytes(decode_message(p), TracePacketField.TRACK_EVENT) or b""))
        )
        assert get_varint(decode_message(end_packet), TracePacketField.TIMESTAMP) == 4_000
        # Calling finalize again is a no-op (state is drained).
        assert finalize_perfetto_packets(state, sequence_id=1) == []


def _track_descriptor_bytes(packet_bytes: bytes) -> bytes | None:
    """Extract the inner ``TrackDescriptor`` bytes from a ``TracePacket``.

    Returns ``None`` if the packet is not a track-descriptor packet.
    """
    fields = decode_message(packet_bytes)
    return get_bytes(fields, TracePacketField.TRACK_DESCRIPTOR)


def _process_descriptor_fields_for_pid(
    descriptors: list[bytes],
    pid: int,
) -> list:
    """Return the ``TrackDescriptor`` proto fields for the process
    descriptor of *pid* (i.e. a TrackDescriptor with a ``process``
    sub-message carrying the matching pid). Returns an empty list if
    no matching descriptor exists.
    """
    matched: list = []
    for d in descriptors:
        td_bytes = _track_descriptor_bytes(d)
        if td_bytes is None:
            continue
        td_fields = decode_message(td_bytes)
        proc_bytes = get_bytes(td_fields, TrackDescriptorField.PROCESS)
        if proc_bytes is None:
            continue
        proc_fields = decode_message(proc_bytes)
        if get_varint(proc_fields, ProcessDescriptorField.PID) == pid:
            matched.append(td_fields)
    return matched


def _root_descriptor_fields(descriptors: list[bytes]) -> list:
    """Return the ``TrackDescriptor`` proto fields for the root
    descriptor (the one with ``uuid = 0``)."""
    matched: list = []
    for d in descriptors:
        td_bytes = _track_descriptor_bytes(d)
        if td_bytes is None:
            continue
        td_fields = decode_message(td_bytes)
        if get_varint(td_fields, TrackDescriptorField.UUID) == 0:
            matched.append(td_fields)
    return matched


class TestProcessOrderingByFirstTs:
    """Wire-level tests for the root descriptor and per-process
    ``sibling_order_rank`` derived from the first event timestamp."""

    def test_root_descriptor_present_with_explicit_ordering(self) -> None:
        state = PerfettoTrackState()
        events = [
            process_meta(100, "Process 100"),
            instant_event(100, "start", ts_ns=5_000),
        ]
        descriptors, _ = convert_trace_events_to_perfetto(
            events,
            state,
            sequence_id=1,
        )
        roots = _root_descriptor_fields(descriptors)
        assert len(roots) == 1
        td = roots[0]
        assert get_varint(td, TrackDescriptorField.PROCESS_ORDERING) == 1
        assert get_varint(td, TrackDescriptorField.THREAD_ORDERING) == 1
        assert get_field(td, TrackDescriptorField.NAME) is None
        assert get_field(td, TrackDescriptorField.PROCESS) is None
        assert get_field(td, TrackDescriptorField.THREAD) is None
        assert get_field(td, TrackDescriptorField.COUNTER) is None
        assert get_field(td, TrackDescriptorField.PARENT_UUID) is None
        assert get_field(td, TrackDescriptorField.CHILD_ORDERING) is None

    def test_root_descriptor_emitted_exactly_once_across_calls(self) -> None:
        state = PerfettoTrackState()
        events1 = [
            process_meta(100, "Process 100"),
            instant_event(100, "first", ts_ns=1_000),
        ]
        events2 = [
            process_meta(200, "Process 200"),
            instant_event(200, "second", ts_ns=2_000),
        ]
        d1, _ = convert_trace_events_to_perfetto(events1, state, sequence_id=1)
        d2, _ = convert_trace_events_to_perfetto(events2, state, sequence_id=1)
        total_roots = len(_root_descriptor_fields(d1)) + len(_root_descriptor_fields(d2))
        assert total_roots == 1, f"expected one root descriptor total, got {total_roots}"

    def test_root_descriptor_not_emitted_for_empty_input(self) -> None:
        state = PerfettoTrackState()
        descriptors, packets = convert_trace_events_to_perfetto([], state, sequence_id=1)
        assert descriptors == []
        assert packets == []

    def test_process_descriptor_carries_sibling_order_rank_by_first_ts(self) -> None:
        """Pid with earlier first ts gets the smaller rank."""
        state = PerfettoTrackState()
        events = [
            process_meta(1, "Process 1"),
            instant_event(1, "ev1", ts_ns=2_000),
            process_meta(2, "Process 2"),
            instant_event(2, "ev2", ts_ns=1_000),
        ]
        descriptors, _ = convert_trace_events_to_perfetto(
            events,
            state,
            sequence_id=1,
        )
        ranks = {
            pid: get_varint(td, TrackDescriptorField.SIBLING_ORDER_RANK)
            for pid in (1, 2)
            for td in _process_descriptor_fields_for_pid(descriptors, pid)
        }
        assert ranks == {1: 1, 2: 0}, f"unexpected rank assignment: {ranks}"

    def test_sibling_order_rank_ties_broken_by_pid(self) -> None:
        """When two pids share the same first event ts, ranks follow
        ascending pid (deterministic)."""
        state = PerfettoTrackState()
        events = [
            process_meta(2, "Process 2"),
            instant_event(2, "ev", ts_ns=1_000),
            process_meta(1, "Process 1"),
            instant_event(1, "ev", ts_ns=1_000),
        ]
        descriptors, _ = convert_trace_events_to_perfetto(
            events,
            state,
            sequence_id=1,
        )
        ranks = {
            pid: get_varint(td, TrackDescriptorField.SIBLING_ORDER_RANK)
            for pid in (1, 2)
            for td in _process_descriptor_fields_for_pid(descriptors, pid)
        }
        assert ranks == {1: 0, 2: 1}, f"expected pid-ascending tiebreak; got {ranks}"

    def test_meta_only_pid_has_no_sibling_order_rank(self) -> None:
        """A pid with only ProcessMeta / ThreadMeta (no non-meta events)
        must not carry a ``sibling_order_rank`` on its descriptor."""
        state = PerfettoTrackState()
        events = [
            process_meta(100, "Process 100"),
            thread_meta(100, 0, "Thread 0"),
        ]
        descriptors, _ = convert_trace_events_to_perfetto(
            events,
            state,
            sequence_id=1,
        )
        tds = _process_descriptor_fields_for_pid(descriptors, 100)
        assert len(tds) == 1
        assert get_field(tds[0], TrackDescriptorField.SIBLING_ORDER_RANK) is None

    def test_meta_events_do_not_contribute_to_first_ts(self) -> None:
        """``ProcessMeta`` / ``ThreadMeta`` must not set the first
        event ts; the rank is driven solely by non-meta events."""
        state = PerfettoTrackState()
        events = [
            process_meta(100, "Process 100"),
            thread_meta(100, 0, "Thread 0"),
            process_meta(200, "Process 200"),
            thread_meta(200, 0, "Thread 0"),
            instant_event(100, "late", ts_ns=5_000),
            instant_event(200, "early", ts_ns=1_000),
        ]
        descriptors, _ = convert_trace_events_to_perfetto(
            events,
            state,
            sequence_id=1,
        )
        ranks = {
            pid: get_varint(td, TrackDescriptorField.SIBLING_ORDER_RANK)
            for pid in (100, 200)
            for td in _process_descriptor_fields_for_pid(descriptors, pid)
        }
        assert ranks == {100: 1, 200: 0}, f"unexpected rank assignment: {ranks}"

    def test_sibling_order_rank_uses_ts_start_for_gc_stats(self) -> None:
        """For ``TGCStatsInfo`` events, the first event ts is the
        ``ts_start`` (the earliest emitted event for that pause)."""
        state = PerfettoTrackState()
        item1 = GCStatsInfo(
            gen=0,
            iid=0,
            ts_start=3_000,
            ts_stop=4_000,
            heap_size=1000,
            collections=1,
            collected=10,
            uncollectable=0,
            candidates=5,
            duration=0.001,
        )
        events = [
            process_meta(1, "Process 1"),
            process_meta(2, "Process 2"),
            instant_event(2, "ev", ts_ns=2_000),
            *convert_item_to_trace_format(1, item1),
        ]
        descriptors, _ = convert_trace_events_to_perfetto(
            events,
            state,
            sequence_id=1,
        )
        ranks = {
            pid: get_varint(td, TrackDescriptorField.SIBLING_ORDER_RANK)
            for pid in (1, 2)
            for td in _process_descriptor_fields_for_pid(descriptors, pid)
        }
        assert ranks == {1: 1, 2: 0}, f"unexpected rank assignment: {ranks}"

    def test_sibling_order_rank_unchanged_when_input_pid_order_swapped(self) -> None:
        """Reordering the input pids (with the same first-ts values)
        must produce identical rank assignments."""

        def _make_events(ordered_pids: list[int]) -> list:
            ts_map = {1: 2_000, 2: 1_000}
            return [
                ev
                for pid in ordered_pids
                for ev in (
                    process_meta(pid, f"Process {pid}"),
                    instant_event(pid, "ev", ts_ns=ts_map[pid]),
                )
            ]

        s1 = PerfettoTrackState()
        d1, _ = convert_trace_events_to_perfetto(_make_events([1, 2]), s1, sequence_id=1)
        s2 = PerfettoTrackState()
        d2, _ = convert_trace_events_to_perfetto(_make_events([2, 1]), s2, sequence_id=1)
        ranks1 = {
            pid: get_varint(td, TrackDescriptorField.SIBLING_ORDER_RANK)
            for pid in (1, 2)
            for td in _process_descriptor_fields_for_pid(d1, pid)
        }
        ranks2 = {
            pid: get_varint(td, TrackDescriptorField.SIBLING_ORDER_RANK)
            for pid in (1, 2)
            for td in _process_descriptor_fields_for_pid(d2, pid)
        }
        assert ranks1 == ranks2 == {1: 1, 2: 0}

    def test_rank_persists_across_batches(self) -> None:
        """First-ts recorded in one batch must be remembered when
        computing ranks in a later batch (multi-flush invariant)."""
        s = PerfettoTrackState()
        d1, _ = convert_trace_events_to_perfetto(
            [process_meta(1, "p1"), instant_event(1, "a", ts_ns=1_000)],
            s,
            sequence_id=1,
        )
        d2, _ = convert_trace_events_to_perfetto(
            [process_meta(2, "p2"), instant_event(2, "b", ts_ns=5_000)],
            s,
            sequence_id=1,
        )
        # The pre-scan also re-records for batch 2, but the first-ts
        # for pid 1 from batch 1 is preserved (record_first_event_ts
        # only sets the first ts for a pid). Pid 1 should still get
        # rank 0 (ts=1_000) and pid 2 rank 1 (ts=5_000).
        ranks = {
            pid: get_varint(td, TrackDescriptorField.SIBLING_ORDER_RANK)
            for descriptors in (d1, d2)
            for pid in (1, 2)
            for td in _process_descriptor_fields_for_pid(descriptors, pid)
        }
        assert ranks == {1: 0, 2: 1}, f"unexpected rank assignment: {ranks}"

    def test_process_descriptor_writes_start_timestamp_ns(self) -> None:
        """Each process descriptor carries ``start_timestamp_ns``
        set to the first non-meta event ts for the pid (nanoseconds).
        The Perfetto UI uses this to align the process track with the
        process's actual start time.
        """
        state = PerfettoTrackState()
        events = [
            process_meta(100, "Process 100"),
            instant_event(100, "start", ts_ns=5_000),
            process_meta(200, "Process 200"),
            instant_event(200, "start", ts_ns=1_000),
        ]
        descriptors, _ = convert_trace_events_to_perfetto(
            events,
            state,
            sequence_id=1,
        )
        start_ts: dict[int, int | None] = {}
        for pid in (100, 200):
            tds = _process_descriptor_fields_for_pid(descriptors, pid)
            assert len(tds) == 1
            proc_bytes = get_bytes(tds[0], TrackDescriptorField.PROCESS)
            assert proc_bytes is not None
            proc_fields = decode_message(proc_bytes)
            start_ts[pid] = get_varint(
                proc_fields,
                ProcessDescriptorField.START_TIMESTAMP_NS,
            )
        assert start_ts == {100: 5_000, 200: 1_000}

    def test_meta_only_pid_has_no_start_timestamp_ns(self) -> None:
        """A pid with only ``ProcessMeta`` / ``ThreadMeta`` (no
        non-meta events) has no recorded first-ts, so
        ``start_timestamp_ns`` must be absent from the descriptor."""
        state = PerfettoTrackState()
        events = [
            process_meta(100, "Process 100"),
            thread_meta(100, 0, "Thread 0"),
        ]
        descriptors, _ = convert_trace_events_to_perfetto(
            events,
            state,
            sequence_id=1,
        )
        tds = _process_descriptor_fields_for_pid(descriptors, 100)
        assert len(tds) == 1
        proc_bytes = get_bytes(tds[0], TrackDescriptorField.PROCESS)
        assert proc_bytes is not None
        proc_fields = decode_message(proc_bytes)
        assert get_field(proc_fields, ProcessDescriptorField.START_TIMESTAMP_NS) is None

    def test_start_timestamp_ns_uses_ts_start_for_gc_stats(self) -> None:
        """For ``TGCStatsInfo`` events, the first-ts (and therefore
        ``start_timestamp_ns``) is the ``ts_start`` of the first GC
        pause, not the ``ts_stop`` or any sub-event ts."""
        from gcmon.data import GCStatsInfo

        state = PerfettoTrackState()
        item = GCStatsInfo(
            gen=0,
            iid=0,
            ts_start=3_000,
            ts_stop=4_000,
            heap_size=1000,
            collections=1,
            collected=10,
            uncollectable=0,
            candidates=5,
            duration=0.001,
        )
        events = [
            process_meta(1, "Process 1"),
            process_meta(2, "Process 2"),
            instant_event(2, "ev", ts_ns=2_000),
            *convert_item_to_trace_format(1, item),
        ]
        descriptors, _ = convert_trace_events_to_perfetto(
            events,
            state,
            sequence_id=1,
        )
        start_ts: dict[int, int | None] = {}
        for pid in (1, 2):
            tds = _process_descriptor_fields_for_pid(descriptors, pid)
            proc_bytes = get_bytes(tds[0], TrackDescriptorField.PROCESS)
            proc_fields = decode_message(proc_bytes)
            start_ts[pid] = get_varint(
                proc_fields,
                ProcessDescriptorField.START_TIMESTAMP_NS,
            )
        assert start_ts == {1: 3_000, 2: 2_000}

    def test_start_timestamp_ns_persists_across_batches(self) -> None:
        """First-ts recorded in one batch must be remembered when
        the process descriptor is emitted in a later batch."""
        s = PerfettoTrackState()
        d1, _ = convert_trace_events_to_perfetto(
            [process_meta(1, "p1"), instant_event(1, "a", ts_ns=1_000)],
            s,
            sequence_id=1,
        )
        d2, _ = convert_trace_events_to_perfetto(
            [process_meta(2, "p2"), instant_event(2, "b", ts_ns=5_000)],
            s,
            sequence_id=1,
        )
        # Pid 1 was seen in batch 1; pid 2 in batch 2.
        tds_1 = _process_descriptor_fields_for_pid(d1, 1)
        assert len(tds_1) == 1
        proc_bytes_1 = get_bytes(tds_1[0], TrackDescriptorField.PROCESS)
        proc_fields_1 = decode_message(proc_bytes_1)
        assert (
            get_varint(
                proc_fields_1,
                ProcessDescriptorField.START_TIMESTAMP_NS,
            )
            == 1_000
        )
        tds_2 = _process_descriptor_fields_for_pid(d2, 2)
        assert len(tds_2) == 1
        proc_bytes_2 = get_bytes(tds_2[0], TrackDescriptorField.PROCESS)
        proc_fields_2 = decode_message(proc_bytes_2)
        assert (
            get_varint(
                proc_fields_2,
                ProcessDescriptorField.START_TIMESTAMP_NS,
            )
            == 5_000
        )


def _counter_track_y_axis_share_key(
    descriptors: list[bytes],
    track_name: str,
) -> str | None:
    """Find the counter TrackDescriptor whose name equals *track_name*
    and return its ``y_axis_share_key`` (or ``None`` if the
    ``CounterDescriptor`` submessage is empty). Returns ``None`` if no
    such track descriptor exists at all.
    """
    for d in descriptors:
        td_bytes = _track_descriptor_bytes(d)
        if td_bytes is None:
            continue
        td_fields = decode_message(td_bytes)
        if get_string(td_fields, TrackDescriptorField.NAME) != track_name:
            continue
        counter_bytes = get_bytes(td_fields, TrackDescriptorField.COUNTER)
        if counter_bytes is None or counter_bytes == b"":
            return None
        counter_fields = decode_message(counter_bytes)
        return get_string(counter_fields, CounterDescriptorField.Y_AXIS_SHARE_KEY)
    return None


class TestCounterTrackYAxisShareKey:
    """End-to-end wire tests that drive ``convert_trace_events_to_perfetto``
    and inspect the resulting counter track descriptors for the
    ``y_axis_share_key`` value."""

    def test_grouped_counters_share_y_axis_by_metric(self) -> None:
        state = PerfettoTrackState()
        events = [
            process_meta(100, "Process 100"),
            thread_meta(100, 0, "Thread 0"),
            counter_event(100, 0, "G0", 1_000, {"collected": 100, "candidates": 50, "duration": 0.005}),
            counter_event(100, 0, "G1", 1_001, {"collected": 80, "candidates": 40, "duration": 0.004}),
            counter_event(100, 0, "G2", 1_002, {"collected": 60, "candidates": 30, "duration": 0.003}),
        ]
        descriptors, _ = convert_trace_events_to_perfetto(
            events,
            state,
            sequence_id=1,
        )
        for gen in ("G0", "G1", "G2"):
            for metric in ("collected", "candidates", "duration"):
                track_name = f"{gen} {metric}"
                assert _counter_track_y_axis_share_key(descriptors, track_name) == metric, (
                    f"{track_name} should share Y-axis under {metric!r}"
                )

    def test_heap_size_has_no_share_key(self) -> None:
        state = PerfettoTrackState()
        events = [
            process_meta(100, "Process 100"),
            thread_meta(100, 0, "Thread 0"),
            counter_event(100, 0, "heap_size", 1_000, {"heap_size": 4096}),
        ]
        descriptors, _ = convert_trace_events_to_perfetto(
            events,
            state,
            sequence_id=1,
        )
        assert _counter_track_y_axis_share_key(descriptors, "heap_size") is None

    def test_uncollectable_share_key_emitted_when_nonzero(self) -> None:
        state = PerfettoTrackState()
        events = [
            process_meta(100, "Process 100"),
            thread_meta(100, 0, "Thread 0"),
            counter_event(
                100,
                0,
                "G0",
                1_000,
                {"collected": 1, "uncollectable": 1, "candidates": 1, "duration": 1},
            ),
        ]
        descriptors, _ = convert_trace_events_to_perfetto(
            events,
            state,
            sequence_id=1,
        )
        assert _counter_track_y_axis_share_key(descriptors, "G0 uncollectable") == "uncollectable"

    def test_different_pids_have_independent_share_groups(self) -> None:
        """Two pids each emit a ``G0 collected`` counter. Both must
        carry ``y_axis_share_key = "collected"``; the parent-scoping
        is what the docs require for safe sharing, and is implicit in
        the existing per-``(pid, tid)`` ``GC Metrics`` group.

        Multiple metric args are used so the track name resolves to
        ``"G0 collected"`` (the encoder names a single-arg counter
        track by the metric itself, e.g. ``"collected"``).
        """
        state = PerfettoTrackState()
        events = [
            process_meta(100, "Process 100"),
            thread_meta(100, 0, "Thread 0"),
            counter_event(
                100,
                0,
                "G0",
                1_000,
                {"collected": 10, "candidates": 5},
            ),
            process_meta(200, "Process 200"),
            thread_meta(200, 0, "Thread 0"),
            counter_event(
                200,
                0,
                "G0",
                1_001,
                {"collected": 20, "candidates": 6},
            ),
        ]
        descriptors, _ = convert_trace_events_to_perfetto(
            events,
            state,
            sequence_id=1,
        )
        parent_uuids: set[int] = set()
        for d in descriptors:
            td_bytes = _track_descriptor_bytes(d)
            if td_bytes is None:
                continue
            td_fields = decode_message(td_bytes)
            if get_string(td_fields, TrackDescriptorField.NAME) != "G0 collected":
                continue
            parent = get_varint(td_fields, TrackDescriptorField.PARENT_UUID)
            assert parent is not None
            parent_uuids.add(parent)
            counter_bytes = get_bytes(td_fields, TrackDescriptorField.COUNTER)
            assert counter_bytes is not None and counter_bytes != b""
            counter_fields = decode_message(counter_bytes)
            assert get_string(counter_fields, CounterDescriptorField.Y_AXIS_SHARE_KEY) == "collected"
        assert len(parent_uuids) == 2, (
            f"expected G0 collected tracks under 2 distinct parent groups "
            f"(one per pid), got {len(parent_uuids)}: {parent_uuids}"
        )
