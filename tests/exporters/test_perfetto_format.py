"""Tests for Perfetto protobuf message builders and conversion."""


from gcmon.data import GCStatsInfo
from gcmon.exporters.perfetto_format import (
    TYPE_COUNTER,
    TYPE_INSTANT,
    TYPE_SLICE_BEGIN,
    TYPE_SLICE_END,
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
)
from gcmon.exporters.trace_converter import convert_item_to_trace_format
from gcmon.trace_event import counter_event, instant_event, process_meta, thread_meta
from tests.proto_decoder import (
    decode_message,
    get_bytes,
    get_field,
    get_fields,
    get_string,
    get_varint,
)


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
    return convert_trace_events_to_perfetto(meta + gc_events, state, sequence_id)


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
            uuid=200, name="Thread 0", pid=100, tid=0, parent_uuid=100, sibling_order_rank=0,
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
        data = build_track_descriptor(
            uuid=300, name="G0 collected", parent_uuid=200, is_counter=True
        )
        fields = decode_message(data)
        assert get_varint(fields, TrackDescriptorField.UUID) == 300
        assert get_string(fields, TrackDescriptorField.NAME) == "G0 collected"
        assert get_varint(fields, TrackDescriptorField.PARENT_UUID) == 200
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
        data = build_track_event(
            type=TYPE_SLICE_BEGIN, track_uuid=100, name="test"
        )
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
        data = build_track_event(
            type=TYPE_INSTANT, track_uuid=100, name="marker"
        )
        fields = decode_message(data)
        assert get_varint(fields, TrackEventField.TYPE) == TYPE_INSTANT
        assert get_varint(fields, TrackEventField.TRACK_UUID) == 100
        assert get_string(fields, TrackEventField.NAME) == "marker"

    def test_counter(self) -> None:
        data = build_track_event(
            type=TYPE_COUNTER, track_uuid=100, counter_value=42
        )
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
            gen=0, iid=0, ts_start=1_000, ts_stop=2_000,
            heap_size=1000, collections=1, collected=10,
            uncollectable=0, candidates=5, duration=0.001,
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

        desc2, _ = _convert_item(100, GCStatsInfo(
            gen=1, iid=0, ts_start=3_000, ts_stop=4_000,
            heap_size=2000, collections=2, collected=20,
            uncollectable=0, candidates=10, duration=0.002,
        ), state, sequence_id=1)

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
            gen=0, iid=0, ts_start=1_000, ts_stop=2_000,
            heap_size=1000, collections=1, collected=10,
            uncollectable=0, candidates=5, duration=0.001,
        )
        descriptors, _ = _convert_item(100, item, state, sequence_id=1)
        assert len(descriptors) >= 2
        assert state.has_pid(100)
        assert state.has_tid(100, 0)

    def test_thread_track_has_sibling_order_rank_zero(self) -> None:
        state = PerfettoTrackState()
        item = GCStatsInfo(
            gen=0, iid=0, ts_start=1_000, ts_stop=2_000,
            heap_size=1000, collections=1, collected=10,
            uncollectable=0, candidates=5, duration=0.001,
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
            gen=0, iid=0, ts_start=1_000, ts_stop=2_000,
            heap_size=1000, collections=1, collected=10,
            uncollectable=0, candidates=5, duration=0.001,
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
            gen=0, iid=0, ts_start=1_000, ts_stop=2_000,
            heap_size=1000, collections=1, collected=10,
            uncollectable=0, candidates=5, duration=0.001,
        )
        _, packets = _convert_item(100, item, state, sequence_id=1)
        assert len(packets) >= 2
        first_packet_fields = decode_message(packets[0])
        assert get_varint(first_packet_fields, TracePacketField.TIMESTAMP) == 1_000
        track_event_bytes = get_bytes(first_packet_fields, TracePacketField.TRACK_EVENT)
        assert track_event_bytes is not None
        te_fields = decode_message(track_event_bytes)
        assert get_varint(te_fields, TrackEventField.TYPE) == TYPE_SLICE_BEGIN
        assert get_string(te_fields, TrackEventField.NAME) == "GC Pause (gen=0)"

    def test_basic_item_emits_counter_events(self) -> None:
        state = PerfettoTrackState()
        item = GCStatsInfo(
            gen=0, iid=0, ts_start=1_000, ts_stop=2_000,
            heap_size=1000, collections=1, collected=10,
            uncollectable=2, candidates=5, duration=0.001,
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
        assert len(counter_packets) == 4
        values = [get_varint(te, TrackEventField.COUNTER_VALUE) for _, te in counter_packets]
        assert 10 in values
        assert 2 in values
        assert 5 in values
        assert 1000 in values

    def test_counter_descriptor_emitted_once(self) -> None:
        state = PerfettoTrackState()
        item = GCStatsInfo(
            gen=0, iid=0, ts_start=1_000, ts_stop=2_000,
            heap_size=1000, collections=1, collected=10,
            uncollectable=0, candidates=5, duration=0.001,
        )
        desc1, _ = _convert_item(100, item, state, sequence_id=1)
        desc2, _ = _convert_item(100, item, state, sequence_id=1)
        assert len(desc1) > 0
        assert len(desc2) == 0

    def test_invalid_timestamps_produces_events(self) -> None:
        state = PerfettoTrackState()
        item = GCStatsInfo(
            gen=0, iid=0, ts_start=2_000, ts_stop=1_000,
            heap_size=1000, collections=1, collected=10,
            uncollectable=0, candidates=5, duration=0.001,
        )
        descriptors, packets = _convert_item(100, item, state, sequence_id=1)
        assert len(descriptors) >= 2
        assert len(packets) >= 2

    def test_equal_timestamps_produces_events(self) -> None:
        state = PerfettoTrackState()
        item = GCStatsInfo(
            gen=0, iid=0, ts_start=1_000, ts_stop=1_000,
            heap_size=1000, collections=1, collected=10,
            uncollectable=0, candidates=5, duration=0.0,
        )
        descriptors, packets = _convert_item(100, item, state, sequence_id=1)
        assert len(descriptors) >= 2
        assert len(packets) >= 2

    def test_incremental_item_emits_subphases(self) -> None:
        state = PerfettoTrackState()
        item = GCStatsInfo(
            gen=1, iid=0, ts_start=3_000, ts_stop=4_000,
            heap_size=2048, collections=10, collected=100,
            uncollectable=1, candidates=20, duration=0.01,
            increment_size=500, alive_size=300,
            ts_mark_alive_start=3_000, ts_mark_alive_stop=3_100,
            ts_fill_increment_start=3_100, ts_fill_increment_stop=3_200,
            ts_deduce_unreachable_start=3_200, ts_deduce_unreachable_stop=3_300,
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

    def _make_full_incremental_item(self) -> GCStatsInfo:
        return GCStatsInfo(
            gen=1, iid=0, ts_start=3_000, ts_stop=4_000,
            heap_size=2048, collections=10, collected=100,
            uncollectable=1, candidates=20, duration=0.01,
            increment_size=500, alive_size=300,
            ts_mark_alive_start=3_000, ts_mark_alive_stop=3_100,
            ts_fill_increment_start=3_100, ts_fill_increment_stop=3_200,
            ts_deduce_unreachable_start=3_200, ts_deduce_unreachable_stop=3_300,
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
        self, packets: list[bytes], slice_name: str,
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
                out.append((
                    get_string(ann_fields, DebugAnnotationField.NAME),
                    get_varint(ann_fields, DebugAnnotationField.INT_VALUE),
                ))
            return out
        raise AssertionError(f"slice {slice_name!r} not found in packets")

    def test_finalize_garbage_substep_has_count_annotation(self) -> None:
        state = PerfettoTrackState()
        _, packets = _convert_item(100, self._make_full_incremental_item(), state, sequence_id=1)
        anns = self._annotations_for_slice(packets, "Finalize Garbage (gen=1)")
        assert ("finalized_garbage_count", 42) in anns
        assert all(name not in ("deleted_garbage_count", "clear_weakrefs_count")
                   for name, _ in anns)

    def test_clear_weakrefs_substep_has_count_annotation(self) -> None:
        state = PerfettoTrackState()
        _, packets = _convert_item(100, self._make_full_incremental_item(), state, sequence_id=1)
        anns = self._annotations_for_slice(packets, "Clear Weakrefs (gen=1)")
        assert ("clear_weakrefs_count", 7) in anns
        assert all(name not in ("finalized_garbage_count", "deleted_garbage_count")
                   for name, _ in anns)

    def test_delete_garbage_substep_has_count_annotation(self) -> None:
        state = PerfettoTrackState()
        _, packets = _convert_item(100, self._make_full_incremental_item(), state, sequence_id=1)
        anns = self._annotations_for_slice(packets, "Delete Garbage (gen=1)")
        assert ("deleted_garbage_count", 13) in anns
        assert all(name not in ("finalized_garbage_count", "clear_weakrefs_count")
                   for name, _ in anns)

    def test_zero_duration_subphase_skipped(self) -> None:
        state = PerfettoTrackState()
        item = GCStatsInfo(
            gen=1, iid=0, ts_start=3_000, ts_stop=4_000,
            heap_size=2048, collections=10, collected=100,
            uncollectable=1, candidates=20, duration=0.01,
            increment_size=500, alive_size=300,
            ts_mark_alive_start=3_000, ts_mark_alive_stop=3_000,
            ts_fill_increment_start=3_100, ts_fill_increment_stop=3_200,
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
            gen=0, iid=0, ts_start=1_000, ts_stop=2_000,
            heap_size=1000, collections=1, collected=10,
            uncollectable=0, candidates=5, duration=0.001,
        )
        item1 = GCStatsInfo(
            gen=0, iid=1, ts_start=1_000, ts_stop=2_000,
            heap_size=1000, collections=1, collected=10,
            uncollectable=0, candidates=5, duration=0.001,
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
            gen=0, iid=0, ts_start=1_000, ts_stop=2_000,
            heap_size=1000, collections=5, collected=10,
            uncollectable=2, candidates=3, duration=0.001,
        )
        _, packets = _convert_item(100, item, state, sequence_id=1)
        first_packet_fields = decode_message(packets[0])
        te_bytes = get_bytes(first_packet_fields, TracePacketField.TRACK_EVENT)
        assert te_bytes is not None
        te_fields = decode_message(te_bytes)
        anns = get_fields(te_fields, TrackEventField.DEBUG_ANNOTATIONS)
        assert len(anns) == 7
        for ann in anns:
            ann_fields = decode_message(ann.value)  # type: ignore[arg-type]
            name_field_1 = get_field(ann_fields, 1)
            assert name_field_1 is None or name_field_1.wire_type != 2, (
                "field 1 of DebugAnnotation is `name_iid` (uint64); "
                "the annotation name must not be written there"
            )
            assert get_string(ann_fields, 10) is not None
            assert get_string(ann_fields, DebugAnnotationField.NAME) is not None

    def test_debug_annotations_on_pause(self) -> None:
        state = PerfettoTrackState()
        item = GCStatsInfo(
            gen=0, iid=0, ts_start=1_000, ts_stop=2_000,
            heap_size=1000, collections=5, collected=10,
            uncollectable=2, candidates=3, duration=0.001,
        )
        _, packets = _convert_item(100, item, state, sequence_id=1)
        first_packet_fields = decode_message(packets[0])
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


class TestConvertInstantToPerfettoPacket:
    def test_emits_process_descriptor(self) -> None:
        state = PerfettoTrackState()
        events = [
            process_meta(100, "Process 100"),
            instant_event(100, "start", ts_ns=5_000),
        ]
        descriptors, _ = convert_trace_events_to_perfetto(events, state, sequence_id=1)
        assert len(descriptors) == 1
        assert state.has_pid(100)

    def test_emits_instant_event(self) -> None:
        state = PerfettoTrackState()
        events = [
            process_meta(100, "Process 100"),
            instant_event(100, "start GC monitor", ts_ns=5_000),
        ]
        _, packets = convert_trace_events_to_perfetto(events, state, sequence_id=1)
        assert len(packets) == 1
        fields = decode_message(packets[0])
        assert get_varint(fields, TracePacketField.TIMESTAMP) == 5_000
        te_bytes = get_bytes(fields, TracePacketField.TRACK_EVENT)
        assert te_bytes is not None
        te_fields = decode_message(te_bytes)
        assert get_varint(te_fields, TrackEventField.TYPE) == TYPE_INSTANT
        assert get_string(te_fields, TrackEventField.NAME) == "start GC monitor"

    def test_reuses_process_descriptor(self) -> None:
        state = PerfettoTrackState()
        desc1, _ = convert_trace_events_to_perfetto(
            [process_meta(100, "Process 100"), instant_event(100, "start", ts_ns=5_000)],
            state, sequence_id=1,
        )
        desc2, _ = convert_trace_events_to_perfetto(
            [process_meta(100, "Process 100"), instant_event(100, "stop", ts_ns=10_000)],
            state, sequence_id=1,
        )
        assert len(desc1) == 1
        assert len(desc2) == 0

    def test_instant_after_gc_event_no_duplicate_descriptor(self) -> None:
        state = PerfettoTrackState()
        gc_item = GCStatsInfo(
            gen=0, iid=0, ts_start=1_000, ts_stop=2_000,
            heap_size=1000, collections=1, collected=10,
            uncollectable=0, candidates=5, duration=0.001,
        )
        gc_desc, _ = _convert_item(100, gc_item, state, sequence_id=1)
        inst_desc, _ = convert_trace_events_to_perfetto(
            [process_meta(100, "Process 100"), instant_event(100, "stop", ts_ns=5_000)],
            state, sequence_id=1,
        )
        assert len(gc_desc) >= 2
        assert len(inst_desc) == 0

    def test_single_arg_counter_uses_metric_name_as_track_name(self) -> None:
        state = PerfettoTrackState()
        descriptors, packets = convert_trace_events_to_perfetto(
            [
                process_meta(100, "Process 100"),
                thread_meta(100, 0, "Thread 0"),
                counter_event(pid=100, tid=0, name="heap_size", ts_ns=1_000,
                              args={"heap_size": 1234}),
            ],
            state, sequence_id=1,
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
            gen=0, iid=0, ts_start=1_000, ts_stop=2_000,
            heap_size=1000, collections=1, collected=10,
            uncollectable=0, candidates=5, duration=0.001,
        )
        item_g1 = GCStatsInfo(
            gen=1, iid=0, ts_start=3_000, ts_stop=4_000,
            heap_size=2000, collections=1, collected=10,
            uncollectable=0, candidates=5, duration=0.001,
        )
        _convert_item(100, item_g0, state, sequence_id=1)
        uuid_after_g0 = state.get_or_create_counter_track_uuid(100, 0, "heap_size", "heap_size")
        _convert_item(100, item_g1, state, sequence_id=1)
        uuid_after_g1 = state.get_or_create_counter_track_uuid(100, 0, "heap_size", "heap_size")
        assert uuid_after_g0 == uuid_after_g1
