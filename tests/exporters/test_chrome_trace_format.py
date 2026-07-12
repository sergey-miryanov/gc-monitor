"""Tests for Chrome Trace Event format types and conversion utilities."""

import msgspec
from msgspec import structs

from gcmon.data import GCStatsInfo
from gcmon.exporters.chrome_trace_format import (
    convert_item_to_trace_format,
    convert_to_trace_format,
)
from gcmon.protocol import TGCStatsInfo, TInstantMsg
from gcmon.trace_event import (
    BeginEvent,
    CounterEvent,
    EndEvent,
    begin_event,
    counter_event,
    end_event,
    instant_event,
    process_meta,
    thread_meta,
)
from tests.data_helpers import create_instant_msg
from tests.helpers import create_mock_stats_item

# =============================================================================
# Factory function tests
# =============================================================================


class TestProcessMeta:
    def test_returns_process_name_event(self) -> None:
        event = process_meta(pid=123, name="MyProcess")
        assert event.name == "process_name"
        assert event.ph == "M"
        assert event.pid == 123
        assert event.args.name == "MyProcess"

    def test_metadata_schema(self) -> None:
        event = process_meta(pid=999, name="test")
        assert {f.name for f in structs.fields(event)} == {"name", "ph", "pid", "args"}


class TestThreadMeta:
    def test_returns_thread_name_event(self) -> None:
        event = thread_meta(pid=123, tid=1, name="Thread 1")
        assert event.name == "thread_name"
        assert event.ph == "M"
        assert event.pid == 123
        assert event.tid == 1
        assert event.args.name == "Thread 1"


class TestBeginEvent:
    def test_returns_begin_event(self) -> None:
        args = {
            "generation": 0,
            "iid": 1,
            "collections": 10,
            "heap_size": 1024,
            "collected": 50,
            "uncollectable": 1,
            "candidates": 20,
        }
        event = begin_event(
            pid=123,
            tid=1,
            name="GC Pause (gen=0)",
            cat="gc.pause(gen=0)",
            ts_ns=1_000_000,
            args=args,
        )
        assert event.name == "GC Pause (gen=0)"
        assert event.cat == "gc.pause(gen=0)"
        assert event.ph == "B"
        assert event.ts == 1_000_000
        assert event.pid == 123
        assert event.tid == 1
        assert event.args == args


class TestEndEvent:
    def test_returns_end_event(self) -> None:
        event = end_event(
            pid=123,
            tid=1,
            name="GC Pause (gen=0)",
            cat="gc.pause(gen=0)",
            ts_ns=2_000_000,
        )
        assert event.name == "GC Pause (gen=0)"
        assert event.cat == "gc.pause(gen=0)"
        assert event.ph == "E"
        assert event.ts == 2_000_000
        assert event.pid == 123
        assert event.tid == 1


class TestCounterEvent:
    def test_returns_counter_event(self) -> None:
        args: dict[str, int | float] = {
            "collected": 50,
            "uncollectable": 1,
            "candidates": 20,
            "heap_size": 1024,
        }
        event = counter_event(pid=123, tid=1, name="G0", ts_ns=1_000_000, args=args)
        assert event.ph == "C"
        assert event.name == "G0"
        assert event.ts == 1_000_000
        assert event.args["heap_size"] == 1024


class TestInstantEvent:
    def test_returns_instant_event(self) -> None:
        event = instant_event(pid=123, name="start GC monitor", ts_ns=5_000_000)
        assert event.name == "start GC monitor"
        assert event.ph == "I"
        assert event.s == "p"
        assert event.pid == 123
        assert event.ts == 5_000_000


# =============================================================================
# convert_item_to_trace_format tests
# =============================================================================


def _make_incremental_item(
    gen: int = 0,
    ts_start: int = 1_500_000_000,
    ts_stop: int = 1_505_000_000,
    increment_size: int = 1000,
    alive_size: int = 800,
    sub_step_dur: int = 1_000_000,
    finalized_garbage_count: int = 42,
    deleted_garbage_count: int = 13,
    clear_weakrefs_count: int = 7,
) -> GCStatsInfo:
    return GCStatsInfo(
        gen=gen,
        iid=0,
        ts_start=ts_start,
        ts_stop=ts_stop,
        collections=50,
        collected=200,
        uncollectable=10,
        candidates=40,
        heap_size=52428800,
        duration=0.005,
        increment_size=increment_size,
        alive_size=alive_size,
        ts_mark_alive_start=ts_start,
        ts_mark_alive_stop=ts_start + sub_step_dur,
        ts_fill_increment_start=ts_start + sub_step_dur,
        ts_fill_increment_stop=ts_start + 2 * sub_step_dur,
        ts_deduce_unreachable_start=ts_start + 2 * sub_step_dur,
        ts_deduce_unreachable_stop=ts_start + 3 * sub_step_dur,
        ts_handle_weakref_callbacks_start=ts_start + 3 * sub_step_dur,
        ts_handle_weakref_callbacks_stop=ts_start + 4 * sub_step_dur,
        ts_finalize_garbage_stop=ts_start + 5 * sub_step_dur,
        finalized_garbage_count=finalized_garbage_count,
        ts_handle_resurrected_stop=ts_start + 6 * sub_step_dur,
        ts_clear_weakrefs_stop=ts_start + 7 * sub_step_dur,
        clear_weakrefs_count=clear_weakrefs_count,
        ts_delete_garbage_start=ts_start + 8 * sub_step_dur,
        ts_delete_garbage_stop=ts_start + 9 * sub_step_dur,
        deleted_garbage_count=deleted_garbage_count,
    )


class TestConvertItemToTraceFormat:
    def test_regular_item_returns_pause_and_counter(self) -> None:
        item = create_mock_stats_item(gen=0)
        events = convert_item_to_trace_format(pid=12345, item=item)
        begins = [e for e in events if e.ph == "B"]
        counters = [e for e in events if e.ph == "C"]
        assert len(begins) == 1
        assert len(counters) == 2
        assert begins[0].name == "GC Pause (gen=0)"
        assert {c.name for c in counters} == {"G0", "heap_size"}

    def test_preserves_timestamps_in_nanoseconds(self) -> None:
        item = create_mock_stats_item(ts_start=1_500_000_000, ts_stop=1_505_000_000)
        events = convert_item_to_trace_format(pid=12345, item=item)
        begin = next(e for e in events if e.ph == "B")
        end = next(e for e in events if e.ph == "E")
        assert begin.ts == 1_500_000_000  # ns preserved (no us conversion)
        assert end.ts == 1_505_000_000  # ns preserved (no us conversion)

    def test_incremental_gen0_includes_mark_alive(self) -> None:
        item = _make_incremental_item(gen=0)
        events = convert_item_to_trace_format(pid=12345, item=item)
        names = {e.name for e in events if e.ph == "B"}
        assert "GC Pause (gen=0)" in names
        assert "Mark Alive (gen=0)" in names
        assert "Fill increment (gen=0)" in names
        assert "Deduce Unreachable (gen=0)" in names
        assert "Handle Weakrefs Callbacks (gen=0)" in names
        assert "Finalize Garbage (gen=0)" in names
        assert "Handle Resurrected (gen=0)" in names
        assert "Clear Weakrefs (gen=0)" in names
        assert "Delete Garbage (gen=0)" in names

    def test_incremental_gen0_pause_data_has_increment_size(self) -> None:
        item = _make_incremental_item(gen=0, increment_size=1000)
        events = convert_item_to_trace_format(pid=12345, item=item)
        pause = next(e for e in events if e.ph == "B" and "GC Pause" in e.name)
        assert pause.args["increment_size"] == 1000

    def test_deduce_unreachable_slice_args_has_candidates(self) -> None:
        item = _make_incremental_item(gen=0)
        events = convert_item_to_trace_format(pid=12345, item=item)
        deduce = next(e for e in events if e.ph == "B" and e.name == "Deduce Unreachable (gen=0)")
        assert deduce.args["candidates"] == item.candidates
        assert deduce.args["generation"] == 0

    def test_incremental_gen0_pause_data_no_alive_size(self) -> None:
        item = _make_incremental_item(gen=0)
        events = convert_item_to_trace_format(pid=12345, item=item)
        pause = next(e for e in events if e.ph == "B" and "GC Pause" in e.name)
        assert "alive_size" not in pause.args

    def test_incremental_gen1_pause_data_has_both(self) -> None:
        item = _make_incremental_item(gen=1, increment_size=1000, alive_size=800)
        events = convert_item_to_trace_format(pid=12345, item=item)
        pause = next(e for e in events if e.ph == "B" and "GC Pause" in e.name)
        assert pause.args["increment_size"] == 1000
        assert pause.args["alive_size"] == 800

    def test_incremental_gen2_skips_inc_data_in_pause(self) -> None:
        item = _make_incremental_item(gen=2, increment_size=1000, alive_size=800)
        events = convert_item_to_trace_format(pid=12345, item=item)
        pause = next(e for e in events if e.ph == "B" and "GC Pause" in e.name)
        assert "increment_size" not in pause.args
        assert pause.args["alive_size"] == 800

    def test_counter_data_excludes_increment_size(self) -> None:
        item = _make_incremental_item(gen=0, increment_size=1000)
        events = convert_item_to_trace_format(pid=12345, item=item)
        # The per-gen `G{gen}` counter no longer carries `increment_size`;
        # it is exposed on the GC Pause slice's args instead. The
        # consolidated `heap_size` event is also a `C` event — pick the
        # per-gen one by its name prefix.
        counter = next(e for e in events if e.ph == "C" and e.name.startswith("G"))
        assert "increment_size" not in counter.args
        assert "collected" in counter.args
        pause = next(e for e in events if e.ph == "B" and "GC Pause" in e.name)
        assert pause.args["increment_size"] == 1000

    def test_zero_duration_sub_steps_are_skipped(self) -> None:
        base = _make_incremental_item(gen=0)
        item = GCStatsInfo(
            gen=base.gen,
            iid=base.iid,
            ts_start=base.ts_start,
            ts_stop=base.ts_stop,
            collections=base.collections,
            heap_size=base.heap_size,
            collected=base.collected,
            uncollectable=base.uncollectable,
            candidates=base.candidates,
            duration=base.duration,
            increment_size=base.increment_size,
            alive_size=base.alive_size,
            ts_mark_alive_start=1_500_000_000,
            ts_mark_alive_stop=1_500_000_000,
            ts_fill_increment_start=1_500_000_000,
            ts_fill_increment_stop=1_501_000_000,
            ts_deduce_unreachable_start=1_501_000_000,
            ts_deduce_unreachable_stop=1_501_000_000,
            ts_handle_weakref_callbacks_start=1_501_000_000,
            ts_handle_weakref_callbacks_stop=1_501_000_000,
            ts_finalize_garbage_stop=1_501_000_000,
            finalized_garbage_count=42,
            ts_handle_resurrected_stop=1_501_000_000,
            ts_clear_weakrefs_stop=1_501_000_000,
            clear_weakrefs_count=7,
            ts_delete_garbage_start=1_502_000_000,
            ts_delete_garbage_stop=1_503_000_000,
            deleted_garbage_count=13,
        )
        events = convert_item_to_trace_format(pid=12345, item=item)
        names = {e.name for e in events if e.ph == "B"}
        assert "Mark Alive (gen=0)" not in names
        assert "Fill increment (gen=0)" in names
        assert "Deduce Unreachable (gen=0)" not in names
        assert "Handle Weakrefs Callbacks (gen=0)" not in names
        assert "Finalize Garbage (gen=0)" not in names
        assert "Handle Resurrected (gen=0)" not in names
        assert "Clear Weakrefs (gen=0)" not in names
        assert "Delete Garbage (gen=0)" in names

    def test_pause_data_has_all_required_fields(self) -> None:
        item = create_mock_stats_item()
        events = convert_item_to_trace_format(pid=12345, item=item)
        pause = next(e for e in events if e.ph == "B")
        assert set(pause.args.keys()) == {
            "generation",
            "iid",
            "collections",
            "heap_size",
            "collected",
            "uncollectable",
            "candidates",
        }

    def test_counter_data_has_all_required_fields(self) -> None:
        item = create_mock_stats_item()
        events = convert_item_to_trace_format(pid=12345, item=item)
        counter = next(e for e in events if e.ph == "C" and e.name.startswith("G"))
        assert set(counter.args.keys()) == {
            "collected",
            "uncollectable",
            "candidates",
            "duration",
        }

    def test_counter_data_omits_uncollectable_when_zero(self) -> None:
        item = create_mock_stats_item(uncollectable=0)
        events = convert_item_to_trace_format(pid=12345, item=item)
        counter = next(e for e in events if e.ph == "C" and e.name.startswith("G"))
        assert "uncollectable" not in counter.args
        assert "collected" in counter.args
        assert "candidates" in counter.args
        assert "duration" in counter.args

    def test_duration_counter_event_emitted(self) -> None:
        item = create_mock_stats_item(duration=0.123)
        events = convert_item_to_trace_format(pid=12345, item=item)
        counter = next(e for e in events if e.ph == "C" and e.name == "G0")
        assert counter.args["duration"] == 0.123

    def test_duration_counter_split_by_generation(self) -> None:
        events_g0 = convert_item_to_trace_format(
            pid=12345,
            item=create_mock_stats_item(gen=0, iid=7, duration=0.01),
        )
        events_g1 = convert_item_to_trace_format(
            pid=12345,
            item=create_mock_stats_item(gen=1, iid=7, duration=0.02),
        )
        events_g2 = convert_item_to_trace_format(
            pid=12345,
            item=create_mock_stats_item(gen=2, iid=7, duration=0.03),
        )
        # Each generation produces a per-gen counter event ("G{gen}") with
        # `duration` as one of its args. The three generations' duration
        # values are NOT collapsed onto a single shared track; they live on
        # three separate per-gen tracks.
        for gen, events, expected in (
            (0, events_g0, 0.01),
            (1, events_g1, 0.02),
            (2, events_g2, 0.03),
        ):
            counter = next(e for e in events if e.ph == "C" and e.name == f"G{gen}")
            assert counter.args["duration"] == expected

    def test_heap_size_counter_event_is_shared_across_generations(self) -> None:
        events_g0 = convert_item_to_trace_format(
            pid=12345,
            item=create_mock_stats_item(gen=0, iid=7, heap_size=1000),
        )
        events_g1 = convert_item_to_trace_format(
            pid=12345,
            item=create_mock_stats_item(gen=1, iid=7, heap_size=2000),
        )
        events_g2 = convert_item_to_trace_format(
            pid=12345,
            item=create_mock_stats_item(gen=2, iid=7, heap_size=3000),
        )
        for events in (events_g0, events_g1, events_g2):
            per_gen = [e for e in events if e.ph == "C" and e.name.startswith("G")]
            heap = [e for e in events if e.ph == "C" and e.name == "heap_size"]
            assert len(per_gen) == 1
            assert "heap_size" not in per_gen[0].args
            assert len(heap) == 1
            assert set(heap[0].args.keys()) == {"heap_size"}
        heap0 = next(e for e in events_g0 if e.ph == "C" and e.name == "heap_size")
        assert isinstance(heap0, (BeginEvent, EndEvent, CounterEvent))
        assert heap0.args["heap_size"] == 1000
        heap1 = next(e for e in events_g1 if e.ph == "C" and e.name == "heap_size")
        assert isinstance(heap1, (BeginEvent, EndEvent, CounterEvent))
        assert heap1.args["heap_size"] == 2000
        heap2 = next(e for e in events_g2 if e.ph == "C" and e.name == "heap_size")
        assert isinstance(heap2, (BeginEvent, EndEvent, CounterEvent))
        assert heap2.args["heap_size"] == 3000

    def test_pid_is_passed_through(self) -> None:
        item = create_mock_stats_item()
        events = convert_item_to_trace_format(pid=99999, item=item)
        for event in events:
            assert event.pid == 99999

    def test_tid_equals_item_iid(self) -> None:
        item = create_mock_stats_item(iid=42)
        events = convert_item_to_trace_format(pid=12345, item=item)
        for event in events:
            if event.ph not in ("M",):
                assert isinstance(event, (BeginEvent, EndEvent, CounterEvent))
                assert event.tid == 42

    def test_incremental_gen0_pause_data_has_count_fields(self) -> None:
        item = _make_incremental_item(
            gen=0,
            finalized_garbage_count=42,
            deleted_garbage_count=13,
            clear_weakrefs_count=7,
        )
        events = convert_item_to_trace_format(pid=12345, item=item)
        pause = next(e for e in events if e.ph == "B" and "GC Pause" in e.name)
        assert pause.args["finalized_garbage_count"] == 42
        assert pause.args["deleted_garbage_count"] == 13
        assert pause.args["clear_weakrefs_count"] == 7

    def test_incremental_counter_excludes_size_and_count_fields(self) -> None:
        item = _make_incremental_item(
            gen=0,
            finalized_garbage_count=42,
            deleted_garbage_count=13,
            clear_weakrefs_count=7,
        )
        events = convert_item_to_trace_format(pid=12345, item=item)
        counter = next(e for e in events if e.ph == "C")
        assert "alive_size" not in counter.args
        assert "finalized_garbage_count" not in counter.args
        assert "deleted_garbage_count" not in counter.args
        assert "clear_weakrefs_count" not in counter.args

    def test_regular_item_has_no_count_fields_in_pause(self) -> None:
        item = create_mock_stats_item()
        events = convert_item_to_trace_format(pid=12345, item=item)
        pause = next(e for e in events if e.ph == "B")
        assert "finalized_garbage_count" not in pause.args
        assert "deleted_garbage_count" not in pause.args
        assert "clear_weakrefs_count" not in pause.args

    def test_regular_item_has_no_count_fields_in_counter(self) -> None:
        item = create_mock_stats_item()
        events = convert_item_to_trace_format(pid=12345, item=item)
        counter = next(e for e in events if e.ph == "C")
        assert "finalized_garbage_count" not in counter.args
        assert "deleted_garbage_count" not in counter.args
        assert "clear_weakrefs_count" not in counter.args

    def test_finalize_garbage_substep_has_count(self) -> None:
        item = _make_incremental_item(
            gen=0,
            finalized_garbage_count=42,
            deleted_garbage_count=13,
            clear_weakrefs_count=7,
        )
        events = convert_item_to_trace_format(pid=12345, item=item)
        begin = next(e for e in events if e.ph == "B" and e.name == "Finalize Garbage (gen=0)")
        assert begin.args["finalized_garbage_count"] == 42
        assert "deleted_garbage_count" not in begin.args
        assert "clear_weakrefs_count" not in begin.args

    def test_clear_weakrefs_substep_has_count(self) -> None:
        item = _make_incremental_item(
            gen=0,
            finalized_garbage_count=42,
            deleted_garbage_count=13,
            clear_weakrefs_count=7,
        )
        events = convert_item_to_trace_format(pid=12345, item=item)
        begin = next(e for e in events if e.ph == "B" and e.name == "Clear Weakrefs (gen=0)")
        assert begin.args["clear_weakrefs_count"] == 7
        assert "finalized_garbage_count" not in begin.args
        assert "deleted_garbage_count" not in begin.args

    def test_delete_garbage_substep_has_count(self) -> None:
        item = _make_incremental_item(
            gen=0,
            finalized_garbage_count=42,
            deleted_garbage_count=13,
            clear_weakrefs_count=7,
        )
        events = convert_item_to_trace_format(pid=12345, item=item)
        begin = next(e for e in events if e.ph == "B" and e.name == "Delete Garbage (gen=0)")
        assert begin.args["deleted_garbage_count"] == 13
        assert "finalized_garbage_count" not in begin.args
        assert "clear_weakrefs_count" not in begin.args


class TestConvertToTraceFormat:
    def test_empty_items_returns_empty_events(self) -> None:
        events = convert_to_trace_format({})
        assert events == []

    def test_single_pid_adds_process_meta(self) -> None:
        item = create_mock_stats_item()
        events = convert_to_trace_format({12345: [item]})
        metas = [e for e in events if e.ph == "M"]
        assert len(metas) == 2  # process_name + thread_name
        assert metas[0].name == "process_name"
        assert metas[0].pid == 12345

    def test_multiple_pids(self) -> None:
        item1 = create_mock_stats_item(iid=0)
        item2 = create_mock_stats_item(iid=1)
        items: dict[int, list[TGCStatsInfo | TInstantMsg]] = {1: [item1], 2: [item2]}
        events = convert_to_trace_format(items)
        pids = {e.pid for e in events}
        assert pids == {1, 2}
        process_metas = [e for e in events if e.name == "process_name"]
        assert len(process_metas) == 2

    def test_threads_are_unique_per_pid(self) -> None:
        item1 = create_mock_stats_item(iid=1)
        item2 = create_mock_stats_item(iid=1)  # same tid
        events = convert_to_trace_format({12345: [item1, item2]})
        thread_metas = [e for e in events if e.name == "thread_name"]
        assert len(thread_metas) == 1

    def test_all_returned_events_are_trace_events(self) -> None:
        item = create_mock_stats_item()
        events = convert_to_trace_format({12345: [item]})
        for event in events:
            assert isinstance(event, msgspec.Struct)
            assert hasattr(event, "ph")


class TestConvertToTraceFormatWithInstant:
    def test_instant_msg_only(self) -> None:
        items: list[TGCStatsInfo | TInstantMsg] = [create_instant_msg(name="start GC monitor", ts=1_500_000_000)]
        events = convert_to_trace_format({1: items})
        instants = [e for e in events if e.ph == "I"]
        assert len(instants) == 1
        assert instants[0].name == "start GC monitor"
        assert instants[0].pid == 1
        assert instants[0].ts == 1_500_000_000  # ns preserved (no us conversion)

    def test_mixed_gc_stats_and_instant_msg(self) -> None:
        item = create_mock_stats_item()
        instant = create_instant_msg(name="stop GC monitor", ts=2_000_000_000)
        items: dict[int, list[TGCStatsInfo | TInstantMsg]] = {12345: [item, instant]}
        events = convert_to_trace_format(items)
        assert any(e.ph == "I" for e in events)
        assert any(e.ph == "B" for e in events)
        assert any(e.ph == "C" for e in events)

    def test_multiple_instant_messages(self) -> None:
        items: list[TGCStatsInfo | TInstantMsg] = [
            create_instant_msg(name="start GC monitor", ts=1_000_000_000),
            create_instant_msg(name="stop GC monitor", ts=2_000_000_000),
        ]
        events = convert_to_trace_format({1: items})
        instants = [e for e in events if e.ph == "I"]
        assert len(instants) == 2
        assert instants[0].name == "start GC monitor"
        assert instants[0].ts == 1_000_000_000
        assert instants[1].name == "stop GC monitor"
        assert instants[1].ts == 2_000_000_000
