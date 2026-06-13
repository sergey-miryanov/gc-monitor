"""Tests for the format-neutral IR (intermediate representation) module."""

from gcmon.exporters.ir import (
    convert_instant_to_ir,
    convert_item_to_ir,
    convert_to_ir,
    ir_counter_event,
    ir_inc_event,
    ir_instant_event,
    ir_pause_event,
    ir_process_meta,
    ir_thread_meta,
)
from tests.data_helpers import create_instant_msg
from tests.helpers import create_mock_incremental_item, create_mock_stats_item


class TestIrBuilders:
    def test_ir_process_meta(self) -> None:
        e = ir_process_meta(pid=123, name="MyProcess")
        assert e == {"name": "process_name", "pid": 123, "args": {"name": "MyProcess"}}

    def test_ir_thread_meta(self) -> None:
        e = ir_thread_meta(pid=123, tid=1, name="Thread 1")
        assert e == {"name": "thread_name", "pid": 123, "tid": 1, "args": {"name": "Thread 1"}}

    def test_ir_pause_event_uses_nanoseconds(self) -> None:
        e = ir_pause_event(
            pid=1,
            tid=0,
            name="GC Pause (gen=0)",
            cat="gc.pause(gen=0)",
            ts_start_ns=1_500_000_000,
            dur_ns=5_000_000.0,
            args={
                "generation": 0,
                "iid": 0,
                "collections": 1,
                "heap_size": 1024,
                "collected": 50,
                "uncollectable": 0,
                "candidates": 20,
            },
        )
        assert e["ts_start_ns"] == 1_500_000_000
        assert e["dur_ns"] == 5_000_000.0
        assert "ph" not in e

    def test_ir_inc_event_uses_nanoseconds(self) -> None:
        e = ir_inc_event(
            pid=1,
            tid=0,
            name="Mark Alive (gen=0)",
            cat="gc.mark.alive",
            ts_start_ns=1_500_000_000,
            dur_ns=1_000_000.0,
            args={"generation": 0, "iid": 0},
        )
        assert e["ts_start_ns"] == 1_500_000_000
        assert e["dur_ns"] == 1_000_000.0

    def test_ir_instant_event_uses_nanoseconds(self) -> None:
        e = ir_instant_event(pid=1, name="start", ts_ns=1_500_000_000)
        assert e["ts_ns"] == 1_500_000_000

    def test_ir_counter_event_has_gen(self) -> None:
        e = ir_counter_event(
            pid=1,
            tid=0,
            gen=1,
            name="G1",
            ts_ns=1_500_000_000,
            args={"collected": 10, "uncollectable": 0, "candidates": 5, "heap_size": 1024},
        )
        assert e["gen"] == 1


class TestConvertItemToIr:
    def test_basic_item_emits_pause_counter(self) -> None:
        item = create_mock_stats_item(gen=0)
        events = convert_item_to_ir(pid=12345, item=item)
        pauses = [e for e in events if "ts_start_ns" in e and "collected" in e["args"]]
        counters = [e for e in events if "ts_ns" in e and "tid" in e]
        assert len(pauses) == 1
        assert pauses[0]["name"] == "GC Pause (gen=0)"
        assert len(counters) == 1
        assert counters[0]["name"] == "G0"
        assert counters[0]["gen"] == 0

    def test_no_process_or_thread_meta_emitted(self) -> None:
        item = create_mock_stats_item()
        events = convert_item_to_ir(pid=12345, item=item)
        metas = [e for e in events if e["name"] in ("process_name", "thread_name")]
        assert metas == []

    def test_pause_args_have_base_fields_only_for_basic_item(self) -> None:
        item = create_mock_stats_item()
        events = convert_item_to_ir(pid=1, item=item)
        pause = next(e for e in events if "ts_start_ns" in e and "collected" in e["args"])
        assert set(pause["args"].keys()) == {
            "generation",
            "iid",
            "collections",
            "heap_size",
            "collected",
            "uncollectable",
            "candidates",
        }

    def test_pause_args_include_optional_fields(self) -> None:
        item = create_mock_incremental_item(gen=1)
        events = convert_item_to_ir(pid=1, item=item)
        pause = next(e for e in events if "ts_start_ns" in e and "collected" in e["args"])
        args = pause["args"]
        assert args["generation"] == 1
        assert args["increment_size"] == 1000
        assert args["alive_size"] == 800
        assert args["finalized_garbage_count"] == 42
        assert args["deleted_garbage_count"] == 13
        assert args["clear_weakrefs_count"] == 7

    def test_gen0_pause_omits_alive_size(self) -> None:
        item = create_mock_incremental_item(gen=0)
        events = convert_item_to_ir(pid=1, item=item)
        pause = next(e for e in events if "ts_start_ns" in e and "collected" in e["args"])
        assert "alive_size" not in pause["args"]
        assert "increment_size" in pause["args"]

    def test_gen2_pause_omits_increment_size(self) -> None:
        item = create_mock_incremental_item(gen=2)
        events = convert_item_to_ir(pid=1, item=item)
        pause = next(e for e in events if "ts_start_ns" in e and "collected" in e["args"])
        assert "alive_size" in pause["args"]
        assert "increment_size" not in pause["args"]

    def test_incremental_item_emits_all_subphases(self) -> None:
        item = create_mock_incremental_item(gen=0)
        events = convert_item_to_ir(pid=1, item=item)
        names = {e["name"] for e in events if "ts_start_ns" in e}
        assert "GC Pause (gen=0)" in names
        assert "Mark Alive (gen=0)" in names
        assert "Fill increment (gen=0)" in names
        assert "Deduce Unreachable (gen=0)" in names
        assert "Handle Weakrefs Callbacks (gen=0)" in names
        assert "Finalize Garbage (gen=0)" in names
        assert "Handle Resurrected (gen=0)" in names
        assert "Clear Weakrefs (gen=0)" in names
        assert "Delete Garbage (gen=0)" in names

    def test_subphase_carries_full_optional_set(self) -> None:
        item = create_mock_incremental_item(gen=1)
        events = convert_item_to_ir(pid=1, item=item)
        sub = next(e for e in events if e["name"] == "Mark Alive (gen=1)")
        assert sub["args"]["increment_size"] == 1000
        assert sub["args"]["alive_size"] == 800
        assert sub["args"]["finalized_garbage_count"] == 42
        assert sub["args"]["deleted_garbage_count"] == 13
        assert sub["args"]["clear_weakrefs_count"] == 7

    def test_zero_duration_subphase_is_skipped(self) -> None:
        item = create_mock_incremental_item(
            gen=0,
            ts_mark_alive_start=1_500_000_000,
            ts_mark_alive_stop=1_500_000_000,
        )
        events = convert_item_to_ir(pid=1, item=item)
        names = {e["name"] for e in events if "ts_start_ns" in e}
        assert "Mark Alive (gen=0)" not in names

    def test_dur_ns_uses_ts_start_to_ts_stop(self) -> None:
        item = create_mock_stats_item(ts_start=1_000, ts_stop=2_500)
        events = convert_item_to_ir(pid=1, item=item)
        pause = next(e for e in events if "ts_start_ns" in e and "collected" in e["args"])
        assert pause["ts_start_ns"] == 1_000
        assert pause["dur_ns"] == 1_500.0

    def test_tid_equals_iid(self) -> None:
        item = create_mock_stats_item(iid=42)
        events = convert_item_to_ir(pid=1, item=item)
        for event in events:
            if "tid" in event:
                assert event["tid"] == 42


class TestConvertInstantToIr:
    def test_returns_single_instant_event(self) -> None:
        item = create_instant_msg(name="start GC monitor", ts=1_500_000_000)
        events = convert_instant_to_ir(pid=1, item=item)
        assert len(events) == 1
        assert events[0]["name"] == "start GC monitor"
        assert events[0]["pid"] == 1
        assert events[0]["ts_ns"] == 1_500_000_000


class TestConvertToIr:
    def test_empty_items_returns_empty(self) -> None:
        assert convert_to_ir({}) == []

    def test_emits_process_and_thread_meta(self) -> None:
        item = create_mock_stats_item()
        events = convert_to_ir({12345: [item]})
        metas = [e for e in events if e["name"] in ("process_name", "thread_name")]
        assert len(metas) == 2
        assert any(e["name"] == "process_name" for e in metas)
        assert any(e["name"] == "thread_name" for e in metas)

    def test_threads_are_unique_per_pid(self) -> None:
        item1 = create_mock_stats_item(iid=1)
        item2 = create_mock_stats_item(iid=1)
        events = convert_to_ir({12345: [item1, item2]})
        thread_metas = [e for e in events if e["name"] == "thread_name"]
        assert len(thread_metas) == 1

    def test_instant_msg_emits_instant_event(self) -> None:
        items = [create_instant_msg(name="start", ts=1_000_000)]
        events = convert_to_ir({1: items})
        instants = [
            e for e in events if "ts_ns" in e and e["name"] not in ("process_name", "thread_name") and "tid" not in e
        ]
        assert len(instants) == 1
        assert instants[0]["name"] == "start"
        assert instants[0]["ts_ns"] == 1_000_000

    def test_mixed_items_emit_all_kinds(self) -> None:
        item = create_mock_stats_item()
        instant = create_instant_msg(name="stop", ts=2_000_000)
        events = convert_to_ir({1: [item, instant]})
        names = {e.get("name") for e in events}
        assert "process_name" in names
        assert "thread_name" in names
        assert "GC Pause (gen=0)" in names
        assert "G0" in names
        assert "stop" in names
