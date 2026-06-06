"""Tests for Perfetto protobuf message builders and conversion."""


from tests.proto_decoder import (
    decode_message,
    get_bytes,
    get_field,
    get_fields,
    get_string,
    get_varint,
)

from gc_monitor.data import GCStatsInfo, InstantMsg
from gc_monitor.exporters.perfetto_format import (
    TYPE_COUNTER,
    TYPE_INSTANT,
    TYPE_SLICE_BEGIN,
    TYPE_SLICE_END,
    PerfettoTrackState,
    build_trace,
    build_trace_packet,
    build_track_descriptor,
    build_track_event,
    convert_instant_to_perfetto_packet,
    convert_item_to_perfetto_packets,
)

_PROCESS_BASE = 1 << 60
_THREAD_BASE = 2 << 60
_COUNTER_BASE = 3 << 60


class TestPerfettoTrackState:
    def test_init_empty(self) -> None:
        state = PerfettoTrackState()
        assert not state.has_pid(123)
        assert not state.has_tid(123, 0)
        assert not state.has_counter_track(123, 0, 0, "collected")

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
        assert uuid == 12345 | _PROCESS_BASE

    def test_thread_track_uuid(self) -> None:
        state = PerfettoTrackState()
        uuid = state.get_thread_track_uuid(12345, 0)
        assert uuid == (12345 << 20) | 0 | _THREAD_BASE

    def test_thread_track_uuid_different_iid(self) -> None:
        state = PerfettoTrackState()
        uuid0 = state.get_thread_track_uuid(12345, 0)
        uuid1 = state.get_thread_track_uuid(12345, 1)
        assert uuid0 != uuid1

    def test_counter_track_uuid_sequential(self) -> None:
        state = PerfettoTrackState()
        uuid0 = state.get_or_create_counter_track_uuid(100, 0, 0, "collected")
        uuid1 = state.get_or_create_counter_track_uuid(100, 0, 0, "heap_size")
        assert uuid0 == _COUNTER_BASE
        assert uuid1 == _COUNTER_BASE + 1

    def test_counter_track_uuid_idempotent(self) -> None:
        state = PerfettoTrackState()
        uuid1 = state.get_or_create_counter_track_uuid(100, 0, 0, "collected")
        uuid2 = state.get_or_create_counter_track_uuid(100, 0, 0, "collected")
        assert uuid1 == uuid2

    def test_has_counter_track(self) -> None:
        state = PerfettoTrackState()
        assert not state.has_counter_track(100, 0, 0, "collected")
        state.get_or_create_counter_track_uuid(100, 0, 0, "collected")
        assert state.has_counter_track(100, 0, 0, "collected")
        assert not state.has_counter_track(100, 0, 1, "collected")


class TestBuildTrackDescriptor:
    def test_process_descriptor(self) -> None:
        data = build_track_descriptor(uuid=100, name="Process 100")
        fields = decode_message(data)
        assert get_varint(fields, 1) == 100
        assert get_string(fields, 2) == "Process 100"
        assert get_field(fields, 4) is None
        assert get_field(fields, 5) is None
        assert get_field(fields, 8) is None

    def test_thread_descriptor(self) -> None:
        data = build_track_descriptor(
            uuid=200, name="Thread 0", pid=100, tid=0, parent_uuid=100
        )
        fields = decode_message(data)
        assert get_varint(fields, 1) == 200
        assert get_string(fields, 2) == "Thread 0"
        assert get_varint(fields, 5) == 100
        thread_desc_bytes = get_bytes(fields, 4)
        assert thread_desc_bytes is not None
        thread_fields = decode_message(thread_desc_bytes)
        assert get_varint(thread_fields, 1) == 100
        assert get_varint(thread_fields, 2) == 0

    def test_counter_descriptor(self) -> None:
        data = build_track_descriptor(
            uuid=300, name="collected (gen=0)", parent_uuid=200, is_counter=True
        )
        fields = decode_message(data)
        assert get_varint(fields, 1) == 300
        assert get_string(fields, 2) == "collected (gen=0)"
        assert get_varint(fields, 5) == 200
        assert get_bytes(fields, 8) == b""


class TestBuildTracePacket:
    def test_empty_packet(self) -> None:
        data = build_trace_packet()
        assert data == b""

    def test_with_timestamp(self) -> None:
        data = build_trace_packet(timestamp=1_500_000_000)
        fields = decode_message(data)
        assert get_varint(fields, 8) == 1_500_000_000

    def test_with_track_event(self) -> None:
        event = b"\x08\x01"
        data = build_trace_packet(track_event=event)
        fields = decode_message(data)
        assert get_bytes(fields, 11) == event

    def test_with_track_descriptor(self) -> None:
        desc = b"\x0a\x05hello"
        data = build_trace_packet(track_descriptor=desc)
        fields = decode_message(data)
        assert get_bytes(fields, 60) == desc

    def test_with_all_fields(self) -> None:
        event = b"\x08\x01"
        desc = b"\x0a\x05hello"
        data = build_trace_packet(
            timestamp=1000, track_event=event, track_descriptor=desc
        )
        fields = decode_message(data)
        assert get_varint(fields, 8) == 1000
        assert get_bytes(fields, 11) == event
        assert get_bytes(fields, 60) == desc


class TestBuildTrackEvent:
    def test_slice_begin(self) -> None:
        data = build_track_event(
            type=TYPE_SLICE_BEGIN, track_uuid=100, name="test"
        )
        fields = decode_message(data)
        assert get_varint(fields, 1) == TYPE_SLICE_BEGIN
        assert get_varint(fields, 2) == 100
        assert get_string(fields, 4) == "test"

    def test_slice_end(self) -> None:
        data = build_track_event(type=TYPE_SLICE_END, track_uuid=100)
        fields = decode_message(data)
        assert get_varint(fields, 1) == TYPE_SLICE_END
        assert get_varint(fields, 2) == 100
        assert get_field(fields, 4) is None

    def test_instant(self) -> None:
        data = build_track_event(
            type=TYPE_INSTANT, track_uuid=100, name="marker"
        )
        fields = decode_message(data)
        assert get_varint(fields, 1) == TYPE_INSTANT
        assert get_varint(fields, 2) == 100
        assert get_string(fields, 4) == "marker"

    def test_counter(self) -> None:
        data = build_track_event(
            type=TYPE_COUNTER, track_uuid=100, counter_value=42
        )
        fields = decode_message(data)
        assert get_varint(fields, 1) == TYPE_COUNTER
        assert get_varint(fields, 2) == 100
        assert get_varint(fields, 5) == 42

    def test_with_categories(self) -> None:
        data = build_track_event(
            type=TYPE_SLICE_BEGIN,
            track_uuid=100,
            name="test",
            categories=["cat1", "cat2"],
        )
        fields = decode_message(data)
        cats = get_fields(fields, 3)
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
        anns = get_fields(fields, 6)
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
        assert get_bytes(fields, 1) == packet

    def test_multiple_packets(self) -> None:
        p1 = b"\x40\x01"
        p2 = b"\x40\x02"
        data = build_trace([p1, p2])
        fields = decode_message(data)
        packets = get_fields(fields, 1)
        assert len(packets) == 2
        assert packets[0].value == p1
        assert packets[1].value == p2


class TestConvertItemToPerfettoPackets:
    def test_basic_item_emits_descriptors(self) -> None:
        state = PerfettoTrackState()
        item = GCStatsInfo(
            gen=0, iid=0, ts_start=1_000, ts_stop=2_000,
            heap_size=1000, collections=1, collected=10,
            uncollectable=0, candidates=5, duration=0.001,
        )
        descriptors, _ = convert_item_to_perfetto_packets(100, item, state)
        assert len(descriptors) >= 2
        assert state.has_pid(100)
        assert state.has_tid(100, 0)

    def test_basic_item_emits_pause_slice(self) -> None:
        state = PerfettoTrackState()
        item = GCStatsInfo(
            gen=0, iid=0, ts_start=1_000, ts_stop=2_000,
            heap_size=1000, collections=1, collected=10,
            uncollectable=0, candidates=5, duration=0.001,
        )
        _, packets = convert_item_to_perfetto_packets(100, item, state)
        assert len(packets) >= 2
        first_packet_fields = decode_message(packets[0])
        assert get_varint(first_packet_fields, 8) == 1_000
        track_event_bytes = get_bytes(first_packet_fields, 11)
        assert track_event_bytes is not None
        te_fields = decode_message(track_event_bytes)
        assert get_varint(te_fields, 1) == TYPE_SLICE_BEGIN
        assert get_string(te_fields, 4) == "GC Pause (gen=0)"

    def test_basic_item_emits_counter_events(self) -> None:
        state = PerfettoTrackState()
        item = GCStatsInfo(
            gen=0, iid=0, ts_start=1_000, ts_stop=2_000,
            heap_size=1000, collections=1, collected=10,
            uncollectable=2, candidates=5, duration=0.001,
        )
        _, packets = convert_item_to_perfetto_packets(100, item, state)
        counter_packets = []
        for p in packets:
            fields = decode_message(p)
            te_bytes = get_bytes(fields, 11)
            if te_bytes:
                te_fields = decode_message(te_bytes)
                if get_varint(te_fields, 1) == TYPE_COUNTER:
                    counter_packets.append((fields, te_fields))
        assert len(counter_packets) == 4
        values = [get_varint(te, 5) for _, te in counter_packets]
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
        desc1, _ = convert_item_to_perfetto_packets(100, item, state)
        desc2, _ = convert_item_to_perfetto_packets(100, item, state)
        assert len(desc1) > 0
        assert len(desc2) == 0

    def test_invalid_timestamps_skips_events(self) -> None:
        state = PerfettoTrackState()
        item = GCStatsInfo(
            gen=0, iid=0, ts_start=2_000, ts_stop=1_000,
            heap_size=1000, collections=1, collected=10,
            uncollectable=0, candidates=5, duration=0.001,
        )
        descriptors, packets = convert_item_to_perfetto_packets(100, item, state)
        assert len(packets) == 0
        assert len(descriptors) >= 2

    def test_equal_timestamps_skips_events(self) -> None:
        state = PerfettoTrackState()
        item = GCStatsInfo(
            gen=0, iid=0, ts_start=1_000, ts_stop=1_000,
            heap_size=1000, collections=1, collected=10,
            uncollectable=0, candidates=5, duration=0.0,
        )
        _, packets = convert_item_to_perfetto_packets(100, item, state)
        assert len(packets) == 0

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
        _, packets = convert_item_to_perfetto_packets(100, item, state)
        slice_begins = []
        for p in packets:
            fields = decode_message(p)
            te_bytes = get_bytes(fields, 11)
            if te_bytes:
                te_fields = decode_message(te_bytes)
                if get_varint(te_fields, 1) == TYPE_SLICE_BEGIN:
                    slice_begins.append(get_string(te_fields, 4))
        assert "GC Pause (gen=1)" in slice_begins
        assert "Mark Alive (gen=1)" in slice_begins
        assert "Fill increment (gen=1)" in slice_begins
        assert "Deduce Unreachable (gen=1)" in slice_begins
        assert "Handle Weakrefs Callbacks (gen=1)" in slice_begins
        assert "Finalize Garbage (gen=1)" in slice_begins
        assert "Handle Resurrected (gen=1)" in slice_begins
        assert "Clear Weakrefs (gen=1)" in slice_begins
        assert "Delete Garbage (gen=1)" in slice_begins

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
        _, packets = convert_item_to_perfetto_packets(100, item, state)
        slice_names = []
        for p in packets:
            fields = decode_message(p)
            te_bytes = get_bytes(fields, 11)
            if te_bytes:
                te_fields = decode_message(te_bytes)
                if get_varint(te_fields, 1) == TYPE_SLICE_BEGIN:
                    slice_names.append(get_string(te_fields, 4))
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
        desc0, _ = convert_item_to_perfetto_packets(100, item0, state)
        desc1, _ = convert_item_to_perfetto_packets(100, item1, state)
        assert len(desc0) >= 2
        assert len(desc1) >= 1
        assert state.has_tid(100, 0)
        assert state.has_tid(100, 1)

    def test_debug_annotations_on_pause(self) -> None:
        state = PerfettoTrackState()
        item = GCStatsInfo(
            gen=0, iid=0, ts_start=1_000, ts_stop=2_000,
            heap_size=1000, collections=5, collected=10,
            uncollectable=2, candidates=3, duration=0.001,
        )
        _, packets = convert_item_to_perfetto_packets(100, item, state)
        first_packet_fields = decode_message(packets[0])
        te_bytes = get_bytes(first_packet_fields, 11)
        assert te_bytes is not None
        te_fields = decode_message(te_bytes)
        anns = get_fields(te_fields, 6)
        assert len(anns) == 7
        ann_values = []
        for ann in anns:
            ann_fields = decode_message(ann.value)  # type: ignore[arg-type]
            name = get_string(ann_fields, 1)
            val = get_varint(ann_fields, 4)
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
        item = InstantMsg(type="i", name="start", ts=5_000)
        descriptors, _ = convert_instant_to_perfetto_packet(100, item, state)
        assert len(descriptors) == 1
        assert state.has_pid(100)

    def test_emits_instant_event(self) -> None:
        state = PerfettoTrackState()
        item = InstantMsg(type="i", name="start GC monitor", ts=5_000)
        _, packets = convert_instant_to_perfetto_packet(100, item, state)
        assert len(packets) == 1
        fields = decode_message(packets[0])
        assert get_varint(fields, 8) == 5_000
        te_bytes = get_bytes(fields, 11)
        assert te_bytes is not None
        te_fields = decode_message(te_bytes)
        assert get_varint(te_fields, 1) == TYPE_INSTANT
        assert get_string(te_fields, 4) == "start GC monitor"

    def test_reuses_process_descriptor(self) -> None:
        state = PerfettoTrackState()
        item1 = InstantMsg(type="i", name="start", ts=5_000)
        item2 = InstantMsg(type="i", name="stop", ts=10_000)
        desc1, _ = convert_instant_to_perfetto_packet(100, item1, state)
        desc2, _ = convert_instant_to_perfetto_packet(100, item2, state)
        assert len(desc1) == 1
        assert len(desc2) == 0

    def test_instant_after_gc_event_no_duplicate_descriptor(self) -> None:
        state = PerfettoTrackState()
        gc_item = GCStatsInfo(
            gen=0, iid=0, ts_start=1_000, ts_stop=2_000,
            heap_size=1000, collections=1, collected=10,
            uncollectable=0, candidates=5, duration=0.001,
        )
        instant_item = InstantMsg(type="i", name="stop", ts=5_000)
        gc_desc, _ = convert_item_to_perfetto_packets(100, gc_item, state)
        inst_desc, _ = convert_instant_to_perfetto_packet(100, instant_item, state)
        assert len(gc_desc) >= 2
        assert len(inst_desc) == 0
