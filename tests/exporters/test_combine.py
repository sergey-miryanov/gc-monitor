"""Tests for `gcmon combine`: what it reads, how it normalises, what it writes."""

import json
from pathlib import Path

import msgspec
import pytest

from gcmon.exporters.combine import _normalize_trace_timestamps, _parse_events, combine_files
from gcmon.exporters.jsonl_io import (
    convert_jsonl_to_trace_format,
    normalize_jsonl_timestamps,
    read_jsonl,
    write_jsonl,
)
from gcmon.model.data import LossMsg
from gcmon.model.trace_event import (
    EventArgs,
    TraceEvent,
    begin_event,
    counter_event,
    end_event,
    loss_tid,
    process_meta,
    thread_meta,
)
from tests.exporters.conftest import make_inc_jsonl_record
from tests.helpers import (
    create_jsonl_record,
    create_mock_loss_item,
    create_mock_stats_item,
)


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
        args: EventArgs = {
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
        args: EventArgs = {
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
        args: EventArgs = {
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
        args: EventArgs = {
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
        args: EventArgs = {
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
        args: EventArgs = {
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
        args: EventArgs = {
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
        record = make_inc_jsonl_record(pid=1, ts_start=1000, ts_stop=5000)
        f1.write_bytes(msgspec.json.encode(record) + b"\n")
        combine_files([f1], out, input_format="jsonl", output_format="jsonl")
        records = [json.loads(line) for line in out.read_text(encoding="utf-8").strip().split("\n") if line]
        assert records[0]["increment_size"] == 500
        assert records[0]["alive_size"] == 300


class TestJsonlLossRoundTrip:
    """`combine` reads and writes JSONL, so a loss span has to survive the
    round trip as well as a GC record does; otherwise a converted capture
    silently loses the spans the live run drew."""

    def _msg(self, **kw: int) -> LossMsg:
        kw.setdefault("iid", 1)
        kw.setdefault("ts_start", 5_000)
        kw.setdefault("ts_stop", 6_000)
        return create_mock_loss_item(**kw)

    def test_the_line_carries_every_field(self, tmp_path: Path) -> None:
        """Read off the wire rather than through `read_jsonl`, which would be
        equally happy with a field gcmon writes and reads under one wrong name.
        Every value is distinct, so a pair swapped in transit shows up."""
        path = tmp_path / "loss.jsonl"

        write_jsonl(
            path, {42: [self._msg(gen=1, observed_count=4, lost_from=413, lost_count=5, lost_pause_ns=8_100_000)]}
        )

        assert json.loads(path.read_text(encoding="utf-8")) == {
            "pid": 42,
            "tid": loss_tid(1),
            "iid": 1,
            "ts_start": 5_000,
            "ts_stop": 6_000,
            "gens": [
                {
                    "gen": 1,
                    "observed_count": 4,
                    "lost_from": 413,
                    "lost_count": 5,
                    "lost_pause_ns": 8_100_000,
                }
            ],
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

        assert [a["gen0"]["lost_collections"] for a in args] == ["413..431"]  # type: ignore[index]

    def test_written_on_the_loss_track(self, tmp_path: Path) -> None:
        path = tmp_path / "loss.jsonl"

        write_jsonl(path, {42: [self._msg(lost_count=76)]})

        assert json.loads(path.read_text(encoding="utf-8"))["tid"] == loss_tid(1)

    def test_normalize_shifts_a_loss_span(self) -> None:
        """It is neither a GC record nor an instant, so without a branch of its
        own it would keep raw timestamps while everything around it moved."""
        msg = self._msg(ts_start=7_000, ts_stop=8_000, lost_count=76)
        item = create_mock_stats_item(ts_start=5_000, ts_stop=6_000)

        normalize_jsonl_timestamps({1: [item, msg]})

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

        normalize_jsonl_timestamps({1: [msg, item]})

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
