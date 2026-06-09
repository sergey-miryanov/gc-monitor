"""Tests for Chrome Trace Event format types and conversion utilities."""

from types import SimpleNamespace

from gcmon.exporters.chrome_trace_format import (
    CounterEvent,
    IncrementalEvent,
    InstantEvent,
    PauseEvent,
    ProcessMeta,
    ThreadMeta,
    convert_item_to_trace_format,
    convert_to_trace_format,
    counter_event,
    inc_event,
    instant_event,
    pause_event,
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
        assert event["name"] == "process_name"
        assert event["ph"] == "M"
        assert event["pid"] == 123
        assert event["args"] == {"name": "MyProcess"}

    def test_metadata_schema(self) -> None:
        event = process_meta(pid=999, name="test")
        assert set(event.keys()) == {"name", "ph", "pid", "args"}


class TestThreadMeta:
    def test_returns_thread_name_event(self) -> None:
        event = thread_meta(pid=123, tid=1, name="Thread 1")
        assert event["name"] == "thread_name"
        assert event["ph"] == "M"
        assert event["pid"] == 123
        assert event["tid"] == 1
        assert event["args"] == {"name": "Thread 1"}


class TestPauseEvent:
    def test_returns_complete_event(self) -> None:
        args = {
            "generation": 0,
            "iid": 1,
            "collections": 10,
            "heap_size": 1024,
            "collected": 50,
            "uncollectable": 1,
            "candidates": 20,
        }
        event = pause_event(
            pid=123, tid=1, name="GC Pause (gen=0)",
            cat="gc.pause(gen=0)", ts_us=1000, dur_us=500.0, args=args,
        )
        assert event == {
            "name": "GC Pause (gen=0)",
            "cat": "gc.pause(gen=0)",
            "ph": "X",
            "ts": 1000,
            "dur": 500.0,
            "pid": 123,
            "tid": 1,
            "args": args,
        }


class TestIncEvent:
    def test_returns_incremental_event(self) -> None:
        args = {"generation": 0, "iid": 1, "increment_size": 200, "alive_size": 100}
        event = inc_event(
            pid=123, tid=1, name="Mark Alive (gen=0)",
            cat="gc.mark.alive", ts_us=1000, dur_us=300.0, args=args,
        )
        assert event["ph"] == "X"
        assert event["args"]["increment_size"] == 200


class TestCounterEvent:
    def test_returns_counter_event(self) -> None:
        args = {
            "collected": 50, "uncollectable": 1,
            "candidates": 20, "heap_size": 1024,
        }
        event = counter_event(pid=123, tid=1, name="G0", ts_us=1000, args=args)
        assert event["ph"] == "C"
        assert event["name"] == "G0"
        assert event["ts"] == 1000
        assert event["args"]["heap_size"] == 1024


class TestInstantEvent:
    def test_returns_instant_event(self) -> None:
        event = instant_event(pid=123, name="start GC monitor", ts_us=5_000)
        assert event == {
            "name": "start GC monitor",
            "ph": "I",
            "s": "p",
            "pid": 123,
            "ts": 5_000,
        }


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
) -> SimpleNamespace:
    base = create_mock_stats_item(gen=gen, ts_start=ts_start, ts_stop=ts_stop)
    return SimpleNamespace(
        gen=base.gen, iid=base.iid,
        ts_start=base.ts_start, ts_stop=base.ts_stop,
        collections=base.collections, heap_size=base.heap_size,
        collected=base.collected, uncollectable=base.uncollectable,
        candidates=base.candidates, duration=base.duration,
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
        pauses = [e for e in events if e["ph"] == "X"]
        counters = [e for e in events if e["ph"] == "C"]
        assert len(pauses) == 1
        assert len(counters) == 1
        assert pauses[0]["name"] == "GC Pause (gen=0)"
        assert counters[0]["name"] == "G0"

    def test_converts_timestamps_to_microseconds(self) -> None:
        item = create_mock_stats_item(ts_start=1_500_000_000, ts_stop=1_505_000_000)
        events = convert_item_to_trace_format(pid=12345, item=item)
        pause = next(e for e in events if e["ph"] == "X")
        assert pause["ts"] == 1_500_000  # ns → us
        assert pause["dur"] == 5_000     # (1_505_000_000 - 1_500_000_000) / 1000

    def test_incremental_gen0_includes_mark_alive(self) -> None:
        item = _make_incremental_item(gen=0)
        events = convert_item_to_trace_format(pid=12345, item=item)
        names = {e["name"] for e in events if e["ph"] == "X"}
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
        pause = next(e for e in events if e["ph"] == "X" and "GC Pause" in e["name"])
        assert pause["args"]["increment_size"] == 1000

    def test_incremental_gen0_pause_data_no_alive_size(self) -> None:
        item = _make_incremental_item(gen=0)
        events = convert_item_to_trace_format(pid=12345, item=item)
        pause = next(e for e in events if e["ph"] == "X" and "GC Pause" in e["name"])
        assert "alive_size" not in pause["args"]

    def test_incremental_gen1_pause_data_has_both(self) -> None:
        item = _make_incremental_item(gen=1, increment_size=1000, alive_size=800)
        events = convert_item_to_trace_format(pid=12345, item=item)
        pause = next(e for e in events if e["ph"] == "X" and "GC Pause" in e["name"])
        assert pause["args"]["increment_size"] == 1000
        assert pause["args"]["alive_size"] == 800

    def test_incremental_gen2_skips_inc_data_in_pause(self) -> None:
        item = _make_incremental_item(gen=2, increment_size=1000, alive_size=800)
        events = convert_item_to_trace_format(pid=12345, item=item)
        pause = next(e for e in events if e["ph"] == "X" and "GC Pause" in e["name"])
        assert "increment_size" not in pause["args"]
        assert pause["args"]["alive_size"] == 800

    def test_counter_data_includes_inc_fields_for_gen0(self) -> None:
        item = _make_incremental_item(gen=0, increment_size=1000)
        events = convert_item_to_trace_format(pid=12345, item=item)
        counter = next(e for e in events if e["ph"] == "C")
        assert counter["args"]["increment_size"] == 1000
        assert "alive_size" not in counter["args"]

    def test_zero_duration_sub_steps_are_skipped(self) -> None:
        base = _make_incremental_item(gen=0)
        item = SimpleNamespace(
            gen=base.gen, iid=base.iid,
            ts_start=base.ts_start, ts_stop=base.ts_stop,
            collections=base.collections, heap_size=base.heap_size,
            collected=base.collected, uncollectable=base.uncollectable,
            candidates=base.candidates, duration=base.duration,
            increment_size=base.increment_size, alive_size=base.alive_size,
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
        names = {e["name"] for e in events if e["ph"] == "X"}
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
        pause = next(e for e in events if e["ph"] == "X")
        assert set(pause["args"].keys()) == {
            "generation", "iid", "collections", "heap_size",
            "collected", "uncollectable", "candidates",
        }

    def test_counter_data_has_all_required_fields(self) -> None:
        item = create_mock_stats_item()
        events = convert_item_to_trace_format(pid=12345, item=item)
        counter = next(e for e in events if e["ph"] == "C")
        assert set(counter["args"].keys()) == {
            "collected", "uncollectable", "candidates", "heap_size",
        }

    def test_pid_is_passed_through(self) -> None:
        item = create_mock_stats_item()
        events = convert_item_to_trace_format(pid=99999, item=item)
        for event in events:
            assert event["pid"] == 99999

    def test_tid_equals_item_iid(self) -> None:
        item = create_mock_stats_item(iid=42)
        events = convert_item_to_trace_format(pid=12345, item=item)
        for event in events:
            if event["ph"] != "M":
                assert event["tid"] == 42

    def test_incremental_gen0_pause_data_has_count_fields(self) -> None:
        item = _make_incremental_item(
            gen=0, finalized_garbage_count=42,
            deleted_garbage_count=13, clear_weakrefs_count=7,
        )
        events = convert_item_to_trace_format(pid=12345, item=item)
        pause = next(e for e in events if e["ph"] == "X" and "GC Pause" in e["name"])
        assert pause["args"]["finalized_garbage_count"] == 42
        assert pause["args"]["deleted_garbage_count"] == 13
        assert pause["args"]["clear_weakrefs_count"] == 7

    def test_counter_data_has_count_fields(self) -> None:
        item = _make_incremental_item(
            gen=0, finalized_garbage_count=42,
            deleted_garbage_count=13, clear_weakrefs_count=7,
        )
        events = convert_item_to_trace_format(pid=12345, item=item)
        counter = next(e for e in events if e["ph"] == "C")
        assert counter["args"]["finalized_garbage_count"] == 42
        assert counter["args"]["deleted_garbage_count"] == 13
        assert counter["args"]["clear_weakrefs_count"] == 7

    def test_regular_item_has_no_count_fields_in_pause(self) -> None:
        item = create_mock_stats_item()
        events = convert_item_to_trace_format(pid=12345, item=item)
        pause = next(e for e in events if e["ph"] == "X")
        assert "finalized_garbage_count" not in pause["args"]
        assert "deleted_garbage_count" not in pause["args"]
        assert "clear_weakrefs_count" not in pause["args"]

    def test_regular_item_has_no_count_fields_in_counter(self) -> None:
        item = create_mock_stats_item()
        events = convert_item_to_trace_format(pid=12345, item=item)
        counter = next(e for e in events if e["ph"] == "C")
        assert "finalized_garbage_count" not in counter["args"]
        assert "deleted_garbage_count" not in counter["args"]
        assert "clear_weakrefs_count" not in counter["args"]


class TestConvertToTraceFormat:
    def test_empty_items_returns_empty_events(self) -> None:
        events = convert_to_trace_format({})
        assert events == []

    def test_single_pid_adds_process_meta(self) -> None:
        item = create_mock_stats_item()
        events = convert_to_trace_format({12345: [item]})
        metas = [e for e in events if e["ph"] == "M"]
        assert len(metas) == 2  # process_name + thread_name
        assert metas[0]["name"] == "process_name"
        assert metas[0]["pid"] == 12345

    def test_multiple_pids(self) -> None:
        item1 = create_mock_stats_item(iid=0)
        item2 = create_mock_stats_item(iid=1)
        items = {1: [item1], 2: [item2]}
        events = convert_to_trace_format(items)
        pids = {e["pid"] for e in events}
        assert pids == {1, 2}
        process_metas = [e for e in events if e["name"] == "process_name"]
        assert len(process_metas) == 2

    def test_threads_are_unique_per_pid(self) -> None:
        item1 = create_mock_stats_item(iid=1)
        item2 = create_mock_stats_item(iid=1)  # same tid
        events = convert_to_trace_format({12345: [item1, item2]})
        thread_metas = [e for e in events if e["name"] == "thread_name"]
        assert len(thread_metas) == 1

    def test_all_returned_events_are_trace_events(self) -> None:
        item = create_mock_stats_item()
        events = convert_to_trace_format({12345: [item]})
        for event in events:
            assert isinstance(event, dict)
            assert "ph" in event


class TestConvertToTraceFormatWithInstant:
    def test_instant_msg_only(self) -> None:
        items = [create_instant_msg(name="start GC monitor", ts=1_500_000_000)]
        events = convert_to_trace_format({1: items})
        instants = [e for e in events if e["ph"] == "I"]
        assert len(instants) == 1
        assert instants[0]["name"] == "start GC monitor"
        assert instants[0]["pid"] == 1
        assert instants[0]["ts"] == 1_500_000  # ns -> us

    def test_mixed_gc_stats_and_instant_msg(self) -> None:
        item = create_mock_stats_item()
        instant = create_instant_msg(name="stop GC monitor", ts=2_000_000_000)
        items = {12345: [item, instant]}
        events = convert_to_trace_format(items)
        assert any(e["ph"] == "I" for e in events)
        assert any(e["ph"] == "X" for e in events)
        assert any(e["ph"] == "C" for e in events)

    def test_multiple_instant_messages(self) -> None:
        items = [
            create_instant_msg(name="start GC monitor", ts=1_000_000_000),
            create_instant_msg(name="stop GC monitor", ts=2_000_000_000),
        ]
        events = convert_to_trace_format({1: items})
        instants = [e for e in events if e["ph"] == "I"]
        assert len(instants) == 2
        assert instants[0]["name"] == "start GC monitor"
        assert instants[0]["ts"] == 1_000_000
        assert instants[1]["name"] == "stop GC monitor"
        assert instants[1]["ts"] == 2_000_000

