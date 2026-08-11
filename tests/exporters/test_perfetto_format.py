"""Tests for converting trace events into Perfetto packets."""

from perfetto.protos.perfetto.trace.perfetto_trace_pb2 import (
    TracePacket,
    TrackDescriptor,
    TrackEvent,
)

from gcmon.data import GCStatsInfo, LossMsg
from gcmon.exporters.perfetto_format import convert_trace_events_to_perfetto
from gcmon.exporters.perfetto_process_lifetime import finalize_perfetto_packets
from gcmon.exporters.perfetto_proto import TrackEventType
from gcmon.exporters.perfetto_track_state import PerfettoTrackState
from gcmon.exporters.trace_converter import convert_loss_to_trace_format
from gcmon.trace_event import TraceEvent, counter_event, instant_event, process_meta, thread_meta
from tests.exporters.perfetto_helpers import (
    convert_item,
    lifetime_slices,
    parse_track_descriptor,
)

# Name of the synthetic marker emitted on the process track so the
# cmdline description is always visible in the Perfetto UI. Must match
# ``_START_PROCESS_INSTANT_NAME`` in ``gcmon.exporters.perfetto_format``.
_START_PROCESS_MARKER_NAME: str = "Start Process"


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
        desc1, _ = convert_item(100, item, state, sequence_id=1)

        found_cmdline = False
        found_description = False
        for desc_bytes in desc1:
            packet = TracePacket()
            packet.ParseFromString(desc_bytes)
            if packet.HasField("track_descriptor"):
                td = packet.track_descriptor
                if td.description == "python script.py":
                    found_description = True
                if td.HasField("process") and len(td.process.cmdline) > 0:
                    assert len(td.process.cmdline) == 2
                    assert td.process.cmdline[0] == "python"
                    assert td.process.cmdline[1] == "script.py"
                    found_cmdline = True
        assert found_cmdline
        assert found_description, "description should be set when cmdline is present"

        desc2, _ = convert_item(
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
            packet = TracePacket()
            packet.ParseFromString(desc_bytes)
            if packet.HasField("track_descriptor"):
                assert not packet.track_descriptor.HasField("process")

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
        descriptors, _ = convert_item(100, item, state, sequence_id=1)
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
        descriptors, _ = convert_item(100, item, state, sequence_id=1)
        proc_uuid = state.get_process_track_uuid(100)
        thread_uuid = state.get_thread_track_uuid(100, 0)
        thread_found = False
        for desc_bytes in descriptors:
            packet = TracePacket()
            packet.ParseFromString(desc_bytes)
            if packet.HasField("track_descriptor"):
                td = packet.track_descriptor
                if td.uuid == thread_uuid:
                    assert td.parent_uuid == proc_uuid
                    assert td.sibling_order_rank == 0
                    assert not td.HasField("child_ordering")
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
        descriptors, _ = convert_item(100, item, state, sequence_id=1)
        proc_uuid = state.get_process_track_uuid(100)
        group_uuid = state.get_or_create_counter_group_track_uuid(100, 0)
        assert group_uuid != proc_uuid
        group_seen = False
        per_metric_parent: dict[str, int] = {}
        for desc_bytes in descriptors:
            packet = TracePacket()
            packet.ParseFromString(desc_bytes)
            if packet.HasField("track_descriptor"):
                td = packet.track_descriptor
                uuid = td.uuid
                if td.HasField("counter"):
                    per_metric_parent[td.name] = td.parent_uuid
                elif uuid == group_uuid:
                    group_seen = True
                    assert td.parent_uuid == proc_uuid
                    assert td.child_ordering == 3
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
        _, packets = convert_item(100, item, state, sequence_id=1)
        # Three packets are emitted before the GC pause slice: the
        # synthetic "Start Process" marker on the process track, then
        # the "Process 100" slice begin on the shared "Processes" track,
        # then the GC pause slice begin on the thread track. Find the
        # GC pause slice by name to disambiguate.
        assert len(packets) >= 3
        lifetime_uuid = state.get_or_create_process_lifetime_track_uuid()

        def _packet_name(p: bytes) -> str | None:
            packet = TracePacket()
            packet.ParseFromString(p)
            if not packet.HasField("track_event"):
                return None
            name = packet.track_event.name
            return name or None

        begin_packet = None
        for p in packets:
            packet = TracePacket()
            packet.ParseFromString(p)
            if (
                packet.track_event.type == TrackEvent.Type.TYPE_SLICE_BEGIN
                and packet.track_event.track_uuid != lifetime_uuid
                and packet.track_event.name == "GC Pause(0)"
            ):
                begin_packet = p
                break
        assert begin_packet is not None
        first_packet = TracePacket()
        first_packet.ParseFromString(begin_packet)
        assert first_packet.timestamp == 1_000
        assert first_packet.HasField("track_event")
        assert first_packet.track_event.type == TrackEvent.Type.TYPE_SLICE_BEGIN
        assert first_packet.track_event.name == "GC Pause(0)"

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
        _, packets = convert_item(100, item, state, sequence_id=1)
        counter_packets: list[tuple[TracePacket, TrackEvent]] = []
        for p in packets:
            packet = TracePacket()
            packet.ParseFromString(p)
            if packet.HasField("track_event") and packet.track_event.type == TrackEvent.Type.TYPE_COUNTER:
                counter_packets.append((packet, packet.track_event))
        assert len(counter_packets) == 5
        values = [track_event.counter_value for _, track_event in counter_packets]
        assert 10 in values
        assert 2 in values
        assert 5 in values
        assert 1000 in values
        # The `duration` value is encoded as a double (DOUBLE_COUNTER_VALUE,
        # field 44), not as a varint counter_value. Verify it is present.
        double_values = [track_event.double_counter_value for _, track_event in counter_packets]
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
        desc1, _ = convert_item(100, item, state, sequence_id=1)
        desc2, _ = convert_item(100, item, state, sequence_id=1)
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
        descriptors, packets = convert_item(100, item, state, sequence_id=1)
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
        descriptors, packets = convert_item(100, item, state, sequence_id=1)
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
        _, packets = convert_item(100, item, state, sequence_id=1)
        slice_begins: list[str | None] = []
        for p in packets:
            packet = TracePacket()
            packet.ParseFromString(p)
            if packet.HasField("track_event") and packet.track_event.type == TrackEvent.Type.TYPE_SLICE_BEGIN:
                slice_begins.append(packet.track_event.name or None)
        assert "GC Pause(1)" in slice_begins
        assert "Mark Alive(1)" in slice_begins
        assert "Fill increment(1)" in slice_begins
        assert "Deduce Unreachable(1)" in slice_begins
        assert "Handle Weakrefs Callbacks(1)" in slice_begins
        assert "Finalize Garbage(1)" in slice_begins
        assert "Handle Resurrected(1)" in slice_begins
        assert "Clear Weakrefs(1)" in slice_begins
        assert "Delete Garbage(1)" in slice_begins

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
        _, packets = convert_item(100, item, state, sequence_id=1)
        counter_uuids: set[int] = set()
        for p in packets:
            packet = TracePacket()
            packet.ParseFromString(p)
            if not packet.HasField("track_event"):
                continue
            if packet.track_event.type != TrackEvent.Type.TYPE_COUNTER:
                continue
            counter_uuids.add(packet.track_event.track_uuid)
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
        _, packets = convert_item(100, item, state, sequence_id=1)
        counter_uuids: set[int] = set()
        for p in packets:
            packet = TracePacket()
            packet.ParseFromString(p)
            if not packet.HasField("track_event"):
                continue
            if packet.track_event.type != TrackEvent.Type.TYPE_COUNTER:
                continue
            counter_uuids.add(packet.track_event.track_uuid)
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
        descriptors_packets, packets = convert_item(100, item, state, sequence_id=1)
        # Find the per-gen `G0 duration` counter track UUID. The duration is
        # now split by generation (one `G{gen} duration` track per (pid, iid))
        # so a shared `duration` track is no longer emitted.
        duration_track_uuid: int | None = None
        for p in packets:
            packet = TracePacket()
            packet.ParseFromString(p)
            if not packet.HasField("track_event"):
                continue
            track_event = packet.track_event
            if track_event.type == TrackEvent.Type.TYPE_COUNTER and track_event.double_counter_value == 0.42:
                duration_track_uuid = track_event.track_uuid
                break
        assert duration_track_uuid is not None

        # Find the matching TrackDescriptor and assert rank=4 (per-gen rank
        # for `duration` in the new layout) plus parent resolves to a track
        # named "GC Metrics".
        descriptors: dict[int, tuple[int, int, str]] = {}
        for p in descriptors_packets:
            packet = TracePacket()
            packet.ParseFromString(p)
            if not packet.HasField("track_descriptor"):
                continue
            td = packet.track_descriptor
            descriptors[td.uuid] = (
                td.parent_uuid,
                td.sibling_order_rank,
                td.name,
            )
        assert duration_track_uuid in descriptors
        parent, rank, _ = descriptors[duration_track_uuid]
        assert rank == 5
        assert parent != 0
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
            packet = TracePacket()
            packet.ParseFromString(p)
            if not packet.HasField("track_event"):
                continue
            track_event = packet.track_event
            if track_event.type != TrackEvent.Type.TYPE_SLICE_BEGIN:
                continue
            if track_event.name != slice_name:
                continue
            out: list[tuple[str | None, int | None]] = []
            for ann in track_event.debug_annotations:
                out.append(
                    (
                        ann.name or None,
                        ann.int_value if ann.HasField("int_value") else None,
                    )
                )
            return out
        raise AssertionError(f"slice {slice_name!r} not found in packets")

    def test_finalize_garbage_substep_has_count_annotation(self) -> None:
        state = PerfettoTrackState()
        _, packets = convert_item(100, self._make_full_incremental_item(), state, sequence_id=1)
        anns = self._annotations_for_slice(packets, "Finalize Garbage(1)")
        assert ("finalized_garbage_count", 42) in anns
        assert all(name not in ("deleted_garbage_count", "clear_weakrefs_count") for name, _ in anns)

    def test_clear_weakrefs_substep_has_count_annotation(self) -> None:
        state = PerfettoTrackState()
        _, packets = convert_item(100, self._make_full_incremental_item(), state, sequence_id=1)
        anns = self._annotations_for_slice(packets, "Clear Weakrefs(1)")
        assert ("clear_weakrefs_count", 7) in anns
        assert all(name not in ("finalized_garbage_count", "deleted_garbage_count") for name, _ in anns)

    def test_delete_garbage_substep_has_count_annotation(self) -> None:
        state = PerfettoTrackState()
        _, packets = convert_item(100, self._make_full_incremental_item(), state, sequence_id=1)
        anns = self._annotations_for_slice(packets, "Delete Garbage(1)")
        assert ("deleted_garbage_count", 13) in anns
        assert all(name not in ("finalized_garbage_count", "clear_weakrefs_count") for name, _ in anns)

    def test_deduce_unreachable_substep_has_candidates_annotation(self) -> None:
        state = PerfettoTrackState()
        item = self._make_full_incremental_item()
        _, packets = convert_item(100, item, state, sequence_id=1)
        anns = self._annotations_for_slice(packets, "Deduce Unreachable(1)")
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
        _, packets = convert_item(100, item, state, sequence_id=1)
        slice_names: list[str | None] = []
        for p in packets:
            packet = TracePacket()
            packet.ParseFromString(p)
            if packet.HasField("track_event") and packet.track_event.type == TrackEvent.Type.TYPE_SLICE_BEGIN:
                slice_names.append(packet.track_event.name or None)
        assert "Mark Alive(1)" not in slice_names
        assert "Fill increment(1)" in slice_names

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
        desc0, _ = convert_item(100, item0, state, sequence_id=1)
        desc1, _ = convert_item(100, item1, state, sequence_id=1)
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
        _, packets = convert_item(100, item, state, sequence_id=1)
        # Three packets precede the GC pause slice begin: the synthetic
        # "Start Process" marker, the "Process 100" slice begin on the
        # shared "Processes" track, and any other warm-up events.
        # Identify the GC pause slice by its name.
        lifetime_uuid = state.get_or_create_process_lifetime_track_uuid()
        begin_packet = None
        for p in packets:
            packet = TracePacket()
            packet.ParseFromString(p)
            if (
                packet.track_event.type == TrackEvent.Type.TYPE_SLICE_BEGIN
                and packet.track_event.track_uuid != lifetime_uuid
                and packet.track_event.name == "GC Pause(0)"
            ):
                begin_packet = packet
                break
        first_packet = begin_packet
        assert first_packet is not None
        anns = first_packet.track_event.debug_annotations
        assert len(anns) == 7
        for ann in anns:
            assert not ann.HasField("name_iid"), (
                "field 1 of DebugAnnotation is `name_iid` (uint64); the annotation name must not be written there"
            )
            assert ann.HasField("name")

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
        _, packets = convert_item(100, item, state, sequence_id=1)
        # Disambiguate by name (and exclude the spec-15 "Processes" track
        # slice begin) to find the GC pause slice.
        lifetime_uuid = state.get_or_create_process_lifetime_track_uuid()
        begin_packet = None
        for p in packets:
            packet = TracePacket()
            packet.ParseFromString(p)
            if (
                packet.track_event.type == TrackEvent.Type.TYPE_SLICE_BEGIN
                and packet.track_event.track_uuid != lifetime_uuid
                and packet.track_event.name == "GC Pause(0)"
            ):
                begin_packet = p
                break
        assert begin_packet is not None
        first_packet = TracePacket()
        first_packet.ParseFromString(begin_packet)
        anns = first_packet.track_event.debug_annotations
        assert len(anns) == 7
        ann_values: list[tuple[str | None, int | None]] = []
        for ann in anns:
            name = ann.name or None
            val = ann.int_value if ann.HasField("int_value") else None
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
        events: list[TraceEvent] = [
            process_meta(100, "Process 100"),
            instant_event(100, "start", ts_ns=5_000),
        ]
        descriptors, _ = convert_trace_events_to_perfetto(events, state, sequence_id=1)
        # 1 root descriptor + 1 process descriptor. The "Processes" track
        # descriptor is emitted at closeout, not here.
        assert len(descriptors) == 2
        assert state.has_pid(100)

    def test_emits_instant_event(self) -> None:
        state = PerfettoTrackState()
        events: list[TraceEvent] = [
            process_meta(100, "Process 100"),
            instant_event(100, "start GC monitor", ts_ns=5_000),
        ]
        _, packets = convert_trace_events_to_perfetto(events, state, sequence_id=1)
        # Two packets from the convert call: the synthetic "Start
        # Process" marker (process track) and the user-provided instant
        # event (process track). This pid's whole observed span is a
        # single ts, so its "Processes" slice is zero-length -- and it is
        # still drawn, so finalize adds the track descriptor plus a
        # BEGIN/END pair, both at ts 5_000.
        packets.extend(finalize_perfetto_packets(state, sequence_id=1))
        assert len(packets) == 5
        names: list[str | None] = []
        for p in packets:
            packet = TracePacket()
            packet.ParseFromString(p)
            if packet.HasField("track_event"):
                names.append(packet.track_event.name or None)
        assert names == [
            _START_PROCESS_MARKER_NAME,
            "start GC monitor",
            "Process 100",
            "Process 100",
        ]
        instant_packet = None
        for p in packets:
            packet = TracePacket()
            packet.ParseFromString(p)
            if packet.track_event.name == "start GC monitor":
                instant_packet = packet
                break
        assert instant_packet is not None
        assert instant_packet.timestamp == 5_000
        assert instant_packet.track_event.type == TrackEvent.Type.TYPE_INSTANT
        assert instant_packet.track_event.name == "start GC monitor"

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
        # First call: 2 descriptors (root + process) + 2 packets from the
        # convert (marker + instant). Second call: 0 descriptors (all are
        # idempotent) + 1 packet (the new instant event). The whole
        # "Processes" pair -- descriptor, BEGIN, END -- comes from the
        # single finalize, spanning both calls' timestamps.
        assert len(desc1) == 2
        assert len(packets1) == 2
        assert len(desc2) == 0
        assert len(packets2) == 1
        closeout = finalize_perfetto_packets(state, sequence_id=1)
        assert len(closeout) == 3
        lifetime_uuid = state.get_or_create_process_lifetime_track_uuid()
        assert lifetime_slices(closeout, lifetime_uuid) == [
            (
                5_000,
                TrackEventType.SLICE_BEGIN,
                "Process 100",
                {"real_start_ts": 5_000, "real_end_ts": 10_000},
            ),
            (10_000, TrackEventType.SLICE_END, "Process 100", {}),
        ]

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
        gc_desc, _ = convert_item(100, gc_item, state, sequence_id=1)
        inst_desc, _ = convert_trace_events_to_perfetto(
            [process_meta(100, "Process 100"), instant_event(100, "stop", ts_ns=5_000)],
            state,
            sequence_id=1,
        )
        assert len(gc_desc) >= 2
        assert len(inst_desc) == 0

    def test_single_arg_counter_uses_metric_name_as_track_name(self) -> None:
        state = PerfettoTrackState()
        descriptors, _ = convert_trace_events_to_perfetto(
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
            packet = TracePacket()
            packet.ParseFromString(d)
            if packet.HasField("track_descriptor"):
                td = packet.track_descriptor
                if td.HasField("counter") and td.name:
                    track_names.append(td.name)
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
        convert_item(100, item_g0, state, sequence_id=1)
        uuid_after_g0 = state.get_or_create_counter_track_uuid(100, 0, "heap_size", "heap_size")
        convert_item(100, item_g1, state, sequence_id=1)
        uuid_after_g1 = state.get_or_create_counter_track_uuid(100, 0, "heap_size", "heap_size")
        assert uuid_after_g0 == uuid_after_g1


class TestLossTrackDescriptor:
    """No meta event describes the loss track.

    Every other track gets its descriptor from a ``ProcessMeta`` or
    ``ThreadMeta``, and the exporter emits neither for a negative tid — the
    same guard that keeps the track from being drawn as a thread. So the
    descriptor has to come off the slices themselves, or they land on a uuid
    nothing ever named.
    """

    def _convert(self, msgs: list[LossMsg], state: PerfettoTrackState, pid: int = 100) -> list[bytes]:
        events: list[TraceEvent] = [process_meta(pid, f"Process {pid}")]
        for msg in msgs:
            events.extend(convert_loss_to_trace_format(pid, msg))
        descriptors, _ = convert_trace_events_to_perfetto(events, state, 1)
        return descriptors

    def _msg(self, iid: int = 0) -> LossMsg:
        return LossMsg(iid=iid, gen=0, ts_start=1_000, ts_stop=2_000, lost_count=1, lost_pause_ns=200)

    def _loss_descriptors(self, descriptors: list[bytes]) -> list[TrackDescriptor]:
        parsed = [parse_track_descriptor(d) for d in descriptors]
        return [td for td in parsed if td is not None and td.name.startswith("GC Loss")]

    def test_the_track_is_described(self) -> None:
        state = PerfettoTrackState()

        found = self._loss_descriptors(self._convert([self._msg(iid=0)], state))

        assert [td.name for td in found] == ["GC Loss 0"]

    def test_it_is_named_for_its_interpreter(self) -> None:
        state = PerfettoTrackState()

        found = self._loss_descriptors(self._convert([self._msg(iid=0), self._msg(iid=1)], state))

        assert [td.name for td in found] == ["GC Loss 0", "GC Loss 1"]

    def test_it_hangs_off_the_process_track(self) -> None:
        state = PerfettoTrackState()

        found = self._loss_descriptors(self._convert([self._msg()], state))

        assert found[0].parent_uuid == state.get_process_track_uuid(100)

    def test_it_is_a_plain_custom_track(self) -> None:
        """A ``thread`` sub-message would describe an OS thread that does not
        exist, and Perfetto ignores ordering hints on OS-scoped tracks."""
        state = PerfettoTrackState()

        found = self._loss_descriptors(self._convert([self._msg()], state))

        assert not found[0].HasField("thread")
        assert not found[0].HasField("process")
        assert found[0].sibling_order_rank == 1

    def test_it_is_described_once(self) -> None:
        state = PerfettoTrackState()

        self._convert([self._msg()], state)
        again = self._loss_descriptors(self._convert([self._msg()], state))

        assert again == []

    def test_a_gc_slice_does_not_trigger_it(self) -> None:
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

        descriptors, _ = convert_item(100, item, state)

        assert self._loss_descriptors(descriptors) == []


class TestTheMissingCollectionsAnnotation:
    """``missing_collections`` is the one arg that is not a number.

    Every other annotation the exporter writes is an integer, and a range
    written into an integer field arrives as a number that is not one of the
    counters it names. So this checks the wire, not the dict: the annotation
    has to come back off a parsed packet as ``string_value``.
    """

    def _annotations(self, msg: LossMsg) -> dict[str, str | int]:
        events: list[TraceEvent] = [process_meta(100, "Process 100"), *convert_loss_to_trace_format(100, msg)]
        _, packets = convert_trace_events_to_perfetto(events, PerfettoTrackState(), 1)

        for raw in packets:
            packet = TracePacket()
            packet.ParseFromString(raw)
            if not packet.HasField("track_event"):
                continue
            track_event = packet.track_event
            if track_event.name != "GC Loss(1)":
                continue
            return {
                ann.name: ann.string_value if ann.HasField("string_value") else ann.int_value
                for ann in track_event.debug_annotations
            }
        raise AssertionError("no GC Loss(1) slice in the packets")

    def _msg(self, lost_from: int, lost_count: int) -> LossMsg:
        return LossMsg(
            iid=0,
            gen=1,
            ts_start=1_000,
            ts_stop=2_000,
            lost_count=lost_count,
            lost_pause_ns=200,
            lost_from=lost_from,
        )

    def test_a_range_reaches_the_wire_as_a_string(self) -> None:
        assert self._annotations(self._msg(lost_from=413, lost_count=19))["missing_collections"] == "413..431"

    def test_one_collection_reaches_it_as_its_own_number(self) -> None:
        assert self._annotations(self._msg(lost_from=11, lost_count=1))["missing_collections"] == "11"

    def test_the_counts_beside_it_stay_integers(self) -> None:
        annotations = self._annotations(self._msg(lost_from=11, lost_count=1))

        assert annotations["missing_count"] == 1
        assert annotations["missing_pause_total_ns"] == 200
