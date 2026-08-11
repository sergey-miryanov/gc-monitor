"""Tests for Chrome Trace I/O and utility functions."""

import json
from pathlib import Path

import msgspec
import pytest

from gcmon.data import GCStatsInfo, LossMsg
from gcmon.exporters.chrome_trace_io import (
    _normalize_jsonl_timestamps,
    _normalize_trace_timestamps,
    _parse_events,
    combine_files,
    convert_jsonl_to_trace_format,
    json_to_item,
    read_jsonl,
    write_jsonl,
)
from gcmon.protocol import has_incremental
from gcmon.trace_event import (
    TraceEvent,
    begin_event,
    counter_event,
    end_event,
    loss_tid,
    process_meta,
    thread_meta,
)
from tests.data_helpers import create_instant_msg
from tests.helpers import JsonlRecord, create_jsonl_record, create_mock_stats_item


def _make_inc_item(
    gen: int = 0,
    ts_start: int = 1000,
    ts_stop: int = 2000,
    increment_size: int = 500,
    alive_size: int = 300,
) -> GCStatsInfo:
    return GCStatsInfo(
        gen=gen,
        iid=1,
        ts_start=ts_start,
        ts_stop=ts_stop,
        collections=1,
        heap_size=100,
        collected=10,
        uncollectable=0,
        candidates=5,
        duration=1.0,
        increment_size=increment_size,
        alive_size=alive_size,
        ts_mark_alive_start=ts_start,
        ts_mark_alive_stop=ts_start + 100,
        ts_fill_increment_start=ts_start + 100,
        ts_fill_increment_stop=ts_start + 200,
        ts_deduce_unreachable_start=ts_start + 200,
        ts_deduce_unreachable_stop=ts_start + 300,
        ts_handle_weakref_callbacks_start=ts_start + 300,
        ts_handle_weakref_callbacks_stop=ts_start + 400,
        ts_finalize_garbage_stop=ts_start + 500,
        finalized_garbage_count=42,
        ts_handle_resurrected_stop=ts_start + 600,
        ts_clear_weakrefs_stop=ts_start + 700,
        clear_weakrefs_count=7,
        ts_delete_garbage_start=ts_start + 800,
        ts_delete_garbage_stop=ts_start + 900,
        deleted_garbage_count=13,
    )


def _make_inc_jsonl_record(
    pid: int = 1,
    gen: int = 0,
    ts_start: int = 1000,
    ts_stop: int = 2000,
    increment_size: int = 500,
    alive_size: int = 300,
) -> dict[str, int | float]:
    record = create_jsonl_record(pid=pid, gen=gen, ts_start=ts_start, ts_stop=ts_stop)
    record.update(
        {
            "increment_size": increment_size,
            "alive_size": alive_size,
            "ts_mark_alive_start": ts_start,
            "ts_mark_alive_stop": ts_start + 100,
            "ts_fill_increment_start": ts_start + 100,
            "ts_fill_increment_stop": ts_start + 200,
            "ts_deduce_unreachable_start": ts_start + 200,
            "ts_deduce_unreachable_stop": ts_start + 300,
            "ts_handle_weakref_callbacks_start": ts_start + 300,
            "ts_handle_weakref_callbacks_stop": ts_start + 400,
            "ts_finalize_garbage_stop": ts_start + 500,
            "finalized_garbage_count": 42,
            "ts_handle_resurrected_stop": ts_start + 600,
            "ts_clear_weakrefs_stop": ts_start + 700,
            "clear_weakrefs_count": 7,
            "ts_delete_garbage_start": ts_start + 800,
            "ts_delete_garbage_stop": ts_start + 900,
            "deleted_garbage_count": 13,
        }
    )
    return record


class TestJsonToItem:
    def test_returns_pid_and_item(self) -> None:
        data = create_jsonl_record(pid=123, gen=0)
        pid, item = json_to_item(data)
        assert pid == 123
        assert hasattr(item, "gen")
        assert item.gen == 0

    def test_returns_incremental_item(self) -> None:
        data = _make_inc_jsonl_record(pid=456, gen=1, increment_size=500)
        pid, item = json_to_item(data)
        assert pid == 456
        assert has_incremental(item)
        assert item.increment_size == 500

    def test_pid_as_string(self) -> None:
        record = create_jsonl_record(pid=789)
        data: JsonlRecord = {**record, "pid": "789"}
        pid, _ = json_to_item(data)
        assert pid == 789


class TestReadJsonl:
    def test_reads_single_record(self, tmp_path: Path) -> None:
        path = tmp_path / "test.jsonl"
        record = create_jsonl_record()
        path.write_bytes(msgspec.json.encode(record) + b"\n")

        result = read_jsonl(path)
        assert 123 in result
        assert len(result[123]) == 1
        assert hasattr(result[123][0], "gen")
        assert result[123][0].gen == 0

    def test_reads_multiple_pids(self, tmp_path: Path) -> None:
        path = tmp_path / "test.jsonl"
        lines = [
            msgspec.json.encode(create_jsonl_record(pid=1)),
            msgspec.json.encode(create_jsonl_record(pid=2)),
        ]
        path.write_bytes(b"\n".join(lines) + b"\n")

        result = read_jsonl(path)
        assert set(result.keys()) == {1, 2}

    def test_ignores_empty_lines(self, tmp_path: Path) -> None:
        path = tmp_path / "test.jsonl"
        record = create_jsonl_record()
        path.write_bytes(msgspec.json.encode(record) + b"\n\n\n")
        result = read_jsonl(path)
        assert len(result[123]) == 1

    def test_returns_empty_dict_for_empty_file(self, tmp_path: Path) -> None:
        path = tmp_path / "empty.jsonl"
        path.write_text("", encoding="utf-8")
        result = read_jsonl(path)
        assert result == {}

    def test_reads_incremental_record(self, tmp_path: Path) -> None:
        path = tmp_path / "inc.jsonl"
        record = _make_inc_jsonl_record(pid=1)
        path.write_bytes(msgspec.json.encode(record) + b"\n")
        result = read_jsonl(path)
        assert has_incremental(result[1][0])

    def test_raises_on_malformed_json(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.jsonl"
        path.write_text("not valid json\n", encoding="utf-8")
        with pytest.raises(msgspec.DecodeError):
            read_jsonl(path)

    def test_raises_on_non_dict_json(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.jsonl"
        path.write_text("[1, 2, 3]\n", encoding="utf-8")
        with pytest.raises(TypeError):
            read_jsonl(path)


class TestWriteJsonl:
    def test_writes_one_line_per_event(self, tmp_path: Path) -> None:
        path = tmp_path / "out.jsonl"
        item = create_mock_stats_item()
        write_jsonl(path, {12345: [item]})

        lines = path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["pid"] == 12345

    def test_writes_multiple_pids(self, tmp_path: Path) -> None:
        path = tmp_path / "out.jsonl"
        item1 = create_mock_stats_item(gen=0)
        item2 = create_mock_stats_item(gen=1)
        write_jsonl(path, {1: [item1], 2: [item2]})

        lines = path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 2
        pids = {json.loads(line)["pid"] for line in lines}
        assert pids == {1, 2}

    def test_writes_incremental_fields(self, tmp_path: Path) -> None:
        path = tmp_path / "out.jsonl"
        item = _make_inc_item(increment_size=500, alive_size=300)
        write_jsonl(path, {1: [item]})
        lines = path.read_text(encoding="utf-8").strip().split("\n")
        record = json.loads(lines[0])
        assert record["pid"] == 1
        assert record["increment_size"] == 500
        assert record["alive_size"] == 300

    def test_empty_items_produces_empty_file(self, tmp_path: Path) -> None:
        path = tmp_path / "empty.jsonl"
        write_jsonl(path, {})
        content = path.read_text(encoding="utf-8")
        assert content == ""

    def test_multiple_events_per_pid(self, tmp_path: Path) -> None:
        path = tmp_path / "out.jsonl"
        item1 = create_mock_stats_item(gen=0)
        item2 = create_mock_stats_item(gen=1)
        write_jsonl(path, {1: [item1, item2]})
        lines = path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 2
        assert all(json.loads(line)["pid"] == 1 for line in lines)

    def test_writes_instant_msg(self, tmp_path: Path) -> None:
        path = tmp_path / "out.jsonl"
        item = create_instant_msg(name="event", ts=1_000)
        write_jsonl(path, {1: [item]})
        lines = path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["type"] == "i"
        assert "tid" not in record


class TestParseEvents:
    def test_parses_complete_events(self) -> None:
        events = [
            process_meta(pid=1, name="test"),
            thread_meta(pid=1, tid=1, name="t1"),
        ]
        content = msgspec.json.encode(events)
        result = _parse_events(content)
        assert len(result) == 2
        assert result[0].ph == "M"
        assert result[0].name == "process_name"

    def test_parses_counter_event(self) -> None:
        event = counter_event(
            pid=1,
            tid=1,
            name="G0",
            ts_ns=1_000_000,
            args={"collected": 10, "uncollectable": 1, "candidates": 5, "heap_size": 1000},
        )
        result = _parse_events(msgspec.json.encode([event]))
        assert result[0].ph == "C"

    def test_parses_begin_and_end_events(self) -> None:
        args = {
            "generation": 0,
            "iid": 1,
            "collections": 1,
            "heap_size": 100,
            "collected": 10,
            "uncollectable": 0,
            "candidates": 5,
        }
        events = [
            begin_event(pid=1, tid=1, name="GC Pause", cat="gc", ts_ns=1_000_000, args=args),
            end_event(pid=1, tid=1, name="GC Pause", cat="gc", ts_ns=1_500_000),
        ]
        result = _parse_events(msgspec.json.encode(events))
        assert result[0].ph == "B"
        assert result[0].args["generation"] == 0
        assert result[1].ph == "E"

    def test_raises_on_invalid_json(self) -> None:
        with pytest.raises(ValueError):
            _parse_events("not json")

    def test_raises_on_non_array(self) -> None:
        with pytest.raises(ValueError, match="Expected JSON array"):
            _parse_events('{"ph": "M"}')

    def test_parses_begin_event_with_args(self) -> None:
        event = begin_event(
            pid=1,
            tid=1,
            name="Mark Alive",
            cat="gc",
            ts_ns=1_000_000,
            args={"generation": 0, "iid": 1, "increment_size": 500, "alive_size": 300},
        )
        result = _parse_events(msgspec.json.encode([event]))
        assert result[0].ph == "B"
        assert result[0].args["increment_size"] == 500

    def test_parses_bytes_input(self) -> None:
        event = process_meta(pid=1, name="test")
        result = _parse_events(msgspec.json.encode([event]))
        assert result[0].name == "process_name"

    def test_skips_non_dict_items(self) -> None:
        event = process_meta(pid=1, name="test")
        raw = msgspec.json.encode([event, "not a dict", 42])
        result = _parse_events(raw)
        assert len(result) == 1

    def test_skips_unknown_ph(self) -> None:
        raw = json.dumps(
            [
                {"ph": "R", "name": "resource", "ts": 100, "pid": 1, "tid": 1, "args": {}},
            ]
        )
        result = _parse_events(raw)
        assert len(result) == 0

    def test_skips_unknown_meta_name(self) -> None:
        raw = json.dumps(
            [
                {"name": "unknown_meta", "ph": "M", "pid": 1, "args": {"name": "x"}},
            ]
        )
        result = _parse_events(raw)
        assert len(result) == 0

    def test_parses_instant_event(self) -> None:
        raw = json.dumps(
            [
                {"ph": "I", "name": "marker", "ts": 5000, "pid": 1, "s": "p"},
            ]
        )
        result = _parse_events(raw)
        assert len(result) == 1
        assert result[0].ph == "I"
        assert result[0].name == "marker"
        assert result[0].ts == 5000


class TestNormalizeTraceTimestamps:
    def test_normalizes_to_zero(self) -> None:
        args = {
            "generation": 0,
            "iid": 1,
            "collections": 1,
            "heap_size": 100,
            "collected": 10,
            "uncollectable": 0,
            "candidates": 5,
        }
        e1 = begin_event(pid=1, tid=1, name="e1", cat="c", ts_ns=5_000_000, args=args)
        e2 = counter_event(
            pid=1,
            tid=1,
            name="c1",
            ts_ns=3_000_000,
            args={"collected": 10, "uncollectable": 0, "candidates": 5, "heap_size": 100},
        )
        e3 = process_meta(pid=1, name="p")
        events: list[TraceEvent] = [e1, e2, e3]
        _normalize_trace_timestamps(events)
        assert e1.ts == 2_000_000  # 5_000_000 - 3_000_000
        assert e2.ts == 0  # 3_000_000 - 3_000_000
        assert e3.name == "process_name"  # metadata preserved

    def test_no_timestamp_events_is_noop(self) -> None:
        events: list[TraceEvent] = [process_meta(pid=1, name="p")]
        _normalize_trace_timestamps(events)
        assert len(events) == 1
        assert events[0].name == "process_name"

    def test_single_event_ts_becomes_zero(self) -> None:
        args = {
            "generation": 0,
            "iid": 1,
            "collections": 1,
            "heap_size": 100,
            "collected": 10,
            "uncollectable": 0,
            "candidates": 5,
        }
        e1 = begin_event(pid=1, tid=1, name="e1", cat="c", ts_ns=1_000_000, args=args)
        events: list[TraceEvent] = [e1]
        _normalize_trace_timestamps(events)
        assert e1.ts == 0

    def test_empty_events_is_noop(self) -> None:
        events: list[TraceEvent] = []
        _normalize_trace_timestamps(events)
        assert events == []

    def test_per_pid_normalization(self) -> None:
        args = {
            "generation": 0,
            "iid": 1,
            "collections": 1,
            "heap_size": 100,
            "collected": 10,
            "uncollectable": 0,
            "candidates": 5,
        }
        e1 = begin_event(pid=1, tid=1, name="e1", cat="c", ts_ns=10_000_000, args=args)
        e2 = begin_event(pid=1, tid=1, name="e2", cat="c", ts_ns=12_000_000, args=args)
        e3 = begin_event(pid=2, tid=1, name="e3", cat="c", ts_ns=5_000_000, args=args)
        e4 = begin_event(pid=2, tid=1, name="e4", cat="c", ts_ns=7_000_000, args=args)
        events: list[TraceEvent] = [e1, e2, e3, e4]
        _normalize_trace_timestamps(events)
        assert e1.ts == 0  # pid=1: 10_000_000 - 10_000_000
        assert e2.ts == 2_000_000  # pid=1: 12_000_000 - 10_000_000
        assert e3.ts == 0  # pid=2: 5_000_000 - 5_000_000
        assert e4.ts == 2_000_000  # pid=2: 7_000_000 - 5_000_000

    def test_negative_timestamps(self) -> None:
        args = {
            "generation": 0,
            "iid": 1,
            "collections": 1,
            "heap_size": 100,
            "collected": 10,
            "uncollectable": 0,
            "candidates": 5,
        }
        e1 = begin_event(pid=1, tid=1, name="e1", cat="c", ts_ns=-100, args=args)
        e2 = begin_event(pid=1, tid=1, name="e2", cat="c", ts_ns=-500, args=args)
        events: list[TraceEvent] = [e1, e2]
        _normalize_trace_timestamps(events)
        assert e1.ts == 400  # -100 - (-500)
        assert e2.ts == 0


class TestNormalizeJsonlTimestamps:
    def test_normalizes_all_timestamps(self) -> None:
        item = _make_inc_item(ts_start=5000, ts_stop=6000)
        items = {1: [item]}
        _normalize_jsonl_timestamps(items)
        assert item.ts_start == 0
        assert item.ts_stop == 1000
        assert item.ts_mark_alive_start == 0
        assert item.ts_mark_alive_stop == 100

    def test_no_items_is_noop(self) -> None:
        _normalize_jsonl_timestamps({})

    def test_non_incremental_skips_sub_steps(self) -> None:
        item = create_mock_stats_item(ts_start=5000, ts_stop=6000)
        items = {1: [item]}
        _normalize_jsonl_timestamps(items)
        assert item.ts_start == 0
        assert item.ts_stop == 1000

    def test_mixed_types(self) -> None:
        non_inc = create_mock_stats_item(ts_start=5000, ts_stop=6000)
        inc = _make_inc_item(ts_start=7000, ts_stop=8000)
        items = {1: [non_inc, inc]}
        _normalize_jsonl_timestamps(items)
        assert non_inc.ts_start == 0
        assert inc.ts_start == 2000
        assert inc.ts_mark_alive_start == 2000
        assert inc.ts_mark_alive_stop == 2100

    def test_instant_only_normalize(self) -> None:
        item = create_instant_msg(name="event", ts=5_000)
        items = {1: [item]}
        _normalize_jsonl_timestamps(items)
        assert item.ts == 0

    def test_multiple_pids(self) -> None:
        item1 = _make_inc_item(ts_start=10000, ts_stop=11000)
        item2 = _make_inc_item(ts_start=5000, ts_stop=6000)
        items = {1: [item1], 2: [item2]}
        _normalize_jsonl_timestamps(items)
        assert item1.ts_start == 0  # per-PID min
        assert item2.ts_start == 0  # per-PID min


class TestConvertJsonlToTraceFormat:
    def test_converts_jsonl_to_trace_events(self, tmp_path: Path) -> None:
        path = tmp_path / "test.jsonl"
        record = create_jsonl_record()
        path.write_bytes(msgspec.json.encode(record) + b"\n")

        events = convert_jsonl_to_trace_format(path)
        assert len(events) > 0
        assert any(e.ph == "M" for e in events)  # metadata
        assert any(e.ph == "B" for e in events)  # pause begin
        assert any(e.ph == "C" for e in events)  # counter

    def test_empty_file_returns_empty_list(self, tmp_path: Path) -> None:
        path = tmp_path / "empty.jsonl"
        path.write_text("", encoding="utf-8")
        events = convert_jsonl_to_trace_format(path)
        assert events == []

    def test_incremental_record_creates_sub_events(self, tmp_path: Path) -> None:
        path = tmp_path / "inc.jsonl"
        record = _make_inc_jsonl_record(pid=1, ts_start=1000, ts_stop=5000)
        path.write_bytes(msgspec.json.encode(record) + b"\n")
        events = convert_jsonl_to_trace_format(path)
        pause_events = [e for e in events if e.ph == "B"]
        assert any("Mark Alive" in e.name for e in pause_events)
        assert any("Fill increment" in e.name for e in pause_events)
        assert any("Deduce Unreachable" in e.name for e in pause_events)
        assert any("Handle Weakrefs" in e.name for e in pause_events)
        assert any("Finalize Garbage" in e.name for e in pause_events)
        assert any("Handle Resurrected" in e.name for e in pause_events)
        assert any("Clear Weakrefs" in e.name for e in pause_events)
        assert any("Delete Garbage" in e.name for e in pause_events)

    def test_multiple_pids_generates_metadata(self, tmp_path: Path) -> None:
        path = tmp_path / "multi.jsonl"
        lines = [
            msgspec.json.encode(create_jsonl_record(pid=1)),
            msgspec.json.encode(create_jsonl_record(pid=2)),
        ]
        path.write_bytes(b"\n".join(lines) + b"\n")
        events = convert_jsonl_to_trace_format(path)
        process_metas = [e for e in events if e.name == "process_name"]
        assert len(process_metas) == 2
        assert {e.pid for e in process_metas} == {1, 2}


class TestCombineFiles:
    def test_chrome_to_chrome(self, tmp_path: Path) -> None:
        f1 = tmp_path / "a.json"
        f2 = tmp_path / "b.json"
        out = tmp_path / "out.json"
        e1 = process_meta(pid=1, name="p1")
        e2 = process_meta(pid=2, name="p2")
        f1.write_bytes(msgspec.json.encode([e1]))
        f2.write_bytes(msgspec.json.encode([e2]))

        combine_files([f1, f2], out, input_format="chrome", output_format="chrome")
        data = json.loads(out.read_text(encoding="utf-8"))
        assert len(data) == 2

    def test_jsonl_to_chrome(self, tmp_path: Path) -> None:
        f1 = tmp_path / "a.jsonl"
        out = tmp_path / "out.json"
        f1.write_bytes(
            msgspec.json.encode(create_jsonl_record()) + b"\n",
        )
        combine_files([f1], out, input_format="jsonl", output_format="chrome")
        data = json.loads(out.read_text(encoding="utf-8"))
        assert any(e["ph"] == "B" for e in data)

    def test_jsonl_to_jsonl(self, tmp_path: Path) -> None:
        f1 = tmp_path / "a.jsonl"
        out = tmp_path / "out.jsonl"
        r = create_jsonl_record(pid=1)
        f1.write_bytes(msgspec.json.encode(r) + b"\n")
        combine_files([f1], out, input_format="jsonl", output_format="jsonl")
        lines = out.read_text(encoding="utf-8").strip().split("\n")
        assert json.loads(lines[0])["pid"] == 1

    def test_chrome_to_jsonl_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="not supported"):
            combine_files([], tmp_path / "out.jsonl", input_format="chrome", output_format="jsonl")

    def test_normalize_chrome(self, tmp_path: Path) -> None:
        f1 = tmp_path / "a.json"
        out = tmp_path / "out.json"
        args = {
            "generation": 0,
            "iid": 1,
            "collections": 1,
            "heap_size": 100,
            "collected": 10,
            "uncollectable": 0,
            "candidates": 5,
        }
        e1 = begin_event(pid=1, tid=1, name="e1", cat="c", ts_ns=5_000_000, args=args)
        e2 = begin_event(pid=1, tid=1, name="e2", cat="c", ts_ns=3_000_000, args=args)
        f1.write_bytes(msgspec.json.encode([e1, e2]))
        combine_files([f1], out, normalize=True, input_format="chrome", output_format="chrome")
        data = json.loads(out.read_text(encoding="utf-8"))
        assert data[0]["ts"] == 2000
        assert data[1]["ts"] == 0

    def test_normalize_chrome_multiple_pids(self, tmp_path: Path) -> None:
        f1 = tmp_path / "a.json"
        out = tmp_path / "out.json"
        args = {
            "generation": 0,
            "iid": 1,
            "collections": 1,
            "heap_size": 100,
            "collected": 10,
            "uncollectable": 0,
            "candidates": 5,
        }
        e1 = begin_event(pid=1, tid=1, name="e1", cat="c", ts_ns=10_000_000, args=args)
        e2 = begin_event(pid=2, tid=1, name="e2", cat="c", ts_ns=5_000_000, args=args)
        f1.write_bytes(msgspec.json.encode([e1, e2]))
        combine_files([f1], out, normalize=True, input_format="chrome", output_format="chrome")
        data = json.loads(out.read_text(encoding="utf-8"))
        assert data[0]["ts"] == 0  # pid=1: 10000 - 10000
        assert data[1]["ts"] == 0  # pid=2: 5000 - 5000

    def test_jsonl_to_chrome_normalize(self, tmp_path: Path) -> None:
        f1 = tmp_path / "a.jsonl"
        out = tmp_path / "out.json"
        r1 = create_jsonl_record(ts_start=10_000_000, ts_stop=11_000_000)
        r2 = create_jsonl_record(ts_start=5_000_000, ts_stop=6_000_000)
        lines = [
            msgspec.json.encode(r1),
            msgspec.json.encode(r2),
        ]
        f1.write_bytes(b"\n".join(lines) + b"\n")
        combine_files([f1], out, normalize=True, input_format="jsonl", output_format="chrome")
        data = json.loads(out.read_text(encoding="utf-8"))
        pause_events = [e for e in data if e["ph"] == "B"]
        assert pause_events[0]["ts"] > 0
        assert pause_events[1]["ts"] == 0

    def test_jsonl_to_jsonl_normalize(self, tmp_path: Path) -> None:
        f1 = tmp_path / "a.jsonl"
        out = tmp_path / "out.jsonl"
        r1 = create_jsonl_record(pid=1, ts_start=10_000_000, ts_stop=11_000_000)
        r2 = create_jsonl_record(pid=1, ts_start=5_000_000, ts_stop=6_000_000)
        lines = [
            msgspec.json.encode(r1),
            msgspec.json.encode(r2),
        ]
        f1.write_bytes(b"\n".join(lines) + b"\n")
        combine_files([f1], out, normalize=True, input_format="jsonl", output_format="jsonl")
        records = [json.loads(line) for line in out.read_text(encoding="utf-8").strip().split("\n") if line]
        assert records[0]["ts_start"] == 5_000_000
        assert records[1]["ts_start"] == 0

    def test_jsonl_to_jsonl_merges_same_pid(self, tmp_path: Path) -> None:
        f1 = tmp_path / "a.jsonl"
        f2 = tmp_path / "b.jsonl"
        out = tmp_path / "out.jsonl"
        for f, pid in [(f1, 1), (f2, 2)]:
            f.write_bytes(
                msgspec.json.encode(create_jsonl_record(pid=pid)) + b"\n",
            )
        combine_files([f1, f2], out, input_format="jsonl", output_format="jsonl")
        records = [json.loads(line) for line in out.read_text(encoding="utf-8").strip().split("\n") if line]
        assert len(records) == 2
        assert {r["pid"] for r in records} == {1, 2}

    def test_jsonl_to_jsonl_append_same_pid(self, tmp_path: Path) -> None:
        f1 = tmp_path / "a.jsonl"
        f2 = tmp_path / "b.jsonl"
        out = tmp_path / "out.jsonl"
        r1 = create_jsonl_record(pid=1, ts_start=1000, ts_stop=2000)
        r2 = create_jsonl_record(pid=1, ts_start=3000, ts_stop=4000)
        for f, lines in [(f1, [r1]), (f2, [r2])]:
            f.write_bytes(
                b"\n".join(msgspec.json.encode(r) for r in lines) + b"\n",
            )
        combine_files([f1, f2], out, input_format="jsonl", output_format="jsonl")
        records = [json.loads(line) for line in out.read_text(encoding="utf-8").strip().split("\n") if line]
        assert len(records) == 2
        assert records[0]["ts_start"] == 1000
        assert records[1]["ts_start"] == 3000

    def test_jsonl_to_chrome_multiple_files(self, tmp_path: Path) -> None:
        f1 = tmp_path / "a.jsonl"
        f2 = tmp_path / "b.jsonl"
        out = tmp_path / "out.json"
        for f, pid in [(f1, 1), (f2, 2)]:
            f.write_bytes(
                msgspec.json.encode(create_jsonl_record(pid=pid)) + b"\n",
            )
        combine_files([f1, f2], out, input_format="jsonl", output_format="chrome")
        data = json.loads(out.read_text(encoding="utf-8"))
        process_metas = [e for e in data if e["name"] == "process_name"]
        assert len(process_metas) == 2

    def test_jsonl_to_jsonl_with_incremental(self, tmp_path: Path) -> None:
        f1 = tmp_path / "a.jsonl"
        out = tmp_path / "out.jsonl"
        record = _make_inc_jsonl_record(pid=1, ts_start=1000, ts_stop=5000)
        f1.write_bytes(msgspec.json.encode(record) + b"\n")
        combine_files([f1], out, input_format="jsonl", output_format="jsonl")
        records = [json.loads(line) for line in out.read_text(encoding="utf-8").strip().split("\n") if line]
        assert records[0]["increment_size"] == 500
        assert records[0]["alive_size"] == 300


class TestJsonlLossRoundTrip:
    """`combine` reads and writes JSONL, so a loss span has to survive the
    round trip as well as a GC record does — otherwise a converted capture
    silently loses the spans the live run drew."""

    def _msg(self, **kw: int) -> LossMsg:
        return LossMsg(
            iid=kw.pop("iid", 1),
            gen=kw.pop("gen", 0),
            ts_start=kw.pop("ts_start", 5_000),
            ts_stop=kw.pop("ts_stop", 6_000),
            **kw,
        )

    def test_the_line_carries_every_field(self, tmp_path: Path) -> None:
        """Read off the wire rather than through `read_jsonl`, which would be
        equally happy with a field gcmon writes and reads under one wrong name.
        Every value is distinct, so a pair swapped in transit shows up."""
        path = tmp_path / "loss.jsonl"

        write_jsonl(path, {42: [self._msg(gen=1, lost_from=413, lost_count=5, lost_pause_ns=8_100_000)]})

        assert json.loads(path.read_text(encoding="utf-8")) == {
            "pid": 42,
            "tid": loss_tid(1),
            "iid": 1,
            "gen": 1,
            "ts_start": 5_000,
            "ts_stop": 6_000,
            "lost_from": 413,
            "lost_count": 5,
            "lost_pause_ns": 8_100_000,
        }

    def test_write_then_read(self, tmp_path: Path) -> None:
        path = tmp_path / "loss.jsonl"
        msg = self._msg(gen=1, lost_from=413, lost_count=5, lost_pause_ns=8_100_000)

        write_jsonl(path, {42: [msg]})

        assert read_jsonl(path) == {42: [msg]}

    def test_the_range_survives_into_the_drawing(self, tmp_path: Path) -> None:
        """`lost_from` earns its place on the wire by naming collections in the
        trace. A record that came back with it defaulted still draws a span, so
        the args are where the loss of the field would be visible."""
        path = tmp_path / "loss.jsonl"
        write_jsonl(path, {42: [self._msg(lost_from=413, lost_count=19)]})

        args = [e.args for e in convert_jsonl_to_trace_format(path) if e.ph == "B"]

        assert [a["missing_collections"] for a in args] == ["413..431"]

    def test_written_on_the_loss_track(self, tmp_path: Path) -> None:
        path = tmp_path / "loss.jsonl"

        write_jsonl(path, {42: [self._msg(lost_count=76)]})

        assert json.loads(path.read_text(encoding="utf-8"))["tid"] == loss_tid(1)

    def test_normalize_shifts_a_loss_span(self) -> None:
        """It is neither a GC record nor an instant, so without a branch of its
        own it would keep raw timestamps while everything around it moved."""
        msg = self._msg(ts_start=7_000, ts_stop=8_000, lost_count=76)
        item = create_mock_stats_item(ts_start=5_000, ts_stop=6_000)

        _normalize_jsonl_timestamps({1: [item, msg]})

        assert (msg.ts_start, msg.ts_stop) == (2_000, 3_000)
        assert item.ts_start == 0

    def test_a_loss_span_can_set_the_origin(self) -> None:
        """A window opens at the record before the gap and closes at the one
        after it, so a capture whose first poll already lost records starts on
        a loss span. If the origin were taken from GC records alone every
        timestamp in the combined trace would be off by the difference, and
        the span itself would go negative."""
        msg = self._msg(ts_start=3_000, ts_stop=5_000, lost_count=76)
        item = create_mock_stats_item(ts_start=5_000, ts_stop=6_000)

        _normalize_jsonl_timestamps({1: [msg, item]})

        assert (msg.ts_start, msg.ts_stop) == (0, 2_000)
        assert (item.ts_start, item.ts_stop) == (2_000, 3_000)

    def test_combine_normalizes_from_a_loss_span(self, tmp_path: Path) -> None:
        """The same claim down the path an operator takes, and read back off
        the trace rather than off the structs `combine` mutated in place."""
        source = tmp_path / "in.jsonl"
        out = tmp_path / "out.json"
        msg = self._msg(ts_start=3_000_000, ts_stop=5_000_000, lost_count=76)
        write_jsonl(source, {42: [msg, create_mock_stats_item(iid=1, ts_start=5_000_000, ts_stop=6_000_000)]})

        combine_files([source], out, input_format="jsonl", output_format="chrome", normalize=True)

        events = json.loads(out.read_text(encoding="utf-8"))
        assert [(e["name"], e["ph"], e["ts"]) for e in events if e["name"].startswith("GC ")] == [
            ("GC Loss(0)", "B", 0),
            ("GC Loss(0)", "E", 2_000),
            ("GC Pause(0)", "B", 2_000),
            ("GC Pause(0)", "E", 3_000),
        ]

    def test_combine_carries_loss_into_a_chrome_trace(self, tmp_path: Path) -> None:
        source = tmp_path / "in.jsonl"
        out = tmp_path / "out.json"
        write_jsonl(source, {42: [create_mock_stats_item(iid=1), self._msg(lost_count=76)]})

        combine_files([source], out, input_format="jsonl", output_format="chrome")

        names = {e["name"] for e in json.loads(out.read_text(encoding="utf-8"))}
        assert "GC Loss(0)" in names

    def test_combine_jsonl_to_jsonl_keeps_the_span(self, tmp_path: Path) -> None:
        source = tmp_path / "in.jsonl"
        out = tmp_path / "out.jsonl"
        msg = self._msg(lost_count=76)
        write_jsonl(source, {42: [msg]})

        combine_files([source], out, input_format="jsonl", output_format="jsonl")

        assert read_jsonl(out) == {42: [msg]}


class TestAnOldFormatLossRecord:
    """A capture from before the record went one-per-generation.

    That gcmon flattened three generations into `lost_gen_0`..`lost_gen_2` and
    wrote no `lost_count`, which is the field `from_mapping` now discriminates
    on. Such a line therefore falls through to the GC-record branch, and the
    danger is that it lands there quietly: an interval gcmon was blind in would
    be read back as a collection it observed, and counted into `--stats` and
    drawn on an interpreter's own row as a `GC Pause`.

    It cannot. A GC record is built around counters a loss record never had —
    `gen`, `collections`, `heap_size` — so the conversion fails on the first of
    them. Pinned here because "it happens to be missing a required field" is a
    property of the two shapes, not a decision anything states, and a later
    default on `GCStatsInfo.gen` would turn the failure into a silent misread.
    """

    def _line(self, **kw: int) -> dict[str, int]:
        return {
            "pid": 42,
            "tid": loss_tid(1),
            "iid": 1,
            "ts_start": 5_000,
            "ts_stop": 6_000,
            "lost_gen_0": kw.pop("lost_gen_0", 76),
            "lost_gen_1": 5,
            "lost_gen_2": 0,
            "lost_pause_gen_0": 8_100_000,
            "lost_pause_gen_1": 0,
            "lost_pause_gen_2": 0,
            **kw,
        }

    def _write(self, tmp_path: Path) -> Path:
        path = tmp_path / "old.jsonl"
        path.write_bytes(msgspec.json.encode(self._line()) + b"\n")
        return path

    def test_it_is_not_read_as_a_collection(self) -> None:
        with pytest.raises(msgspec.ValidationError) as excinfo:
            json_to_item(self._line())

        assert "gen" in str(excinfo.value)

    def test_reading_the_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(msgspec.ValidationError):
            read_jsonl(self._write(tmp_path))

    def test_combine_refuses_the_capture(self, tmp_path: Path) -> None:
        """The path an operator actually takes. Nothing here catches the error,
        so `combine` stops rather than writing a trace with a phantom pause on
        it."""
        with pytest.raises(msgspec.ValidationError):
            combine_files(
                [self._write(tmp_path)],
                tmp_path / "out.json",
                input_format="jsonl",
                output_format="chrome",
            )

    def test_a_capture_from_before_lost_from_still_combines(self, tmp_path: Path) -> None:
        """The break is at `lost_count`, not at the whole record. A capture
        written once the record was already per-generation but before
        `lost_from` existed carries the discriminator, so it reads back with the
        field defaulted to the zero no `collections` counter takes."""
        source = tmp_path / "no_lost_from.jsonl"
        line = {"pid": 42, "tid": loss_tid(1), "iid": 1, "gen": 1, "ts_start": 5_000, "ts_stop": 6_000}
        source.write_bytes(msgspec.json.encode({**line, "lost_count": 5, "lost_pause_ns": 8_100_000}) + b"\n")
        out = tmp_path / "out.json"

        combine_files([source], out, input_format="jsonl", output_format="chrome")

        args = [e["args"] for e in json.loads(out.read_text(encoding="utf-8")) if e["ph"] == "B"]
        assert [(a["missing_count"], a["missing_collections"]) for a in args] == [(5, "0..4")]
