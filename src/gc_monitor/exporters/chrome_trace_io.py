"""File I/O and utility functions for Chrome Trace Event format."""

from collections.abc import Mapping
from pathlib import Path

import msgspec

from ..data import from_mapping
from ..protocol import (
    TGCStatsInfo,
    TInstantMsg,
    has_clear_weakrefs,
    has_deduce_unreachable,
    has_delete_garbage,
    has_finalize_garbage,
    has_handle_resurrected,
    has_handle_weakrefs,
    has_incremental,
    has_mark_alive,
    is_gc_stats,
    is_instant,
    to_mapping,
)
from .chrome_trace_format import (
    CounterEvent,
    IncrementalEvent,
    InstantEvent,
    PauseEvent,
    ProcessMeta,
    ThreadMeta,
    TraceEvent,
    convert_to_trace_format,
)

__all__ = [
    "combine_files",
    "convert_jsonl_to_trace_format",
    "read_jsonl",
    "write_trace_events",
]


def json_to_item(data: Mapping[str, str | int | float]) -> tuple[int, TGCStatsInfo | TInstantMsg]:
    pid = int(data["pid"])
    item = from_mapping(data)
    return pid, item


def read_jsonl(filename: Path) -> dict[int, list[TGCStatsInfo | TInstantMsg]]:
    items: dict[int, list[TGCStatsInfo | TInstantMsg]] = {}
    with open(filename, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                pid, item = json_to_item(msgspec.json.decode(line))
                if pid not in items:
                    items[pid] = [item]
                else:
                    items[pid].append(item)

    return items


def convert_jsonl_to_trace_format(path: Path) -> list[TraceEvent]:
    items = read_jsonl(path)
    return convert_to_trace_format(items)


def write_trace_events(filename: Path, events: list[TraceEvent]) -> None:
    """Write TraceEvents to a file."""
    with open(filename, "wb") as f:
        linesep = b"\n"
        f.write(b"[")
        for event in events:
            f.write(linesep)
            f.write(msgspec.json.encode(event))
            linesep = b",\n"
        f.write(b"]\n")
        f.flush()


def write_jsonl(filename: Path, items: dict[int, list[TGCStatsInfo | TInstantMsg]]) -> None:
    """Write GC stats items to a JSONL file."""
    with open(filename, "wb") as f:
        for pid, pid_items in items.items():
            for item in pid_items:
                rec: dict[str, str | int | float] = {"pid": pid}
                if is_gc_stats(item):
                    rec["tid"] = item.iid

                rec.update(to_mapping(item))
                f.write(msgspec.json.encode(rec))
                f.write(b"\n")
            f.flush()


def _parse_events(content: str | bytes) -> list[TraceEvent]:
    try:
        raw: object = msgspec.json.decode(content)
    except msgspec.DecodeError as e:
        raise ValueError(str(e)) from e
    if not isinstance(raw, list):
        raise ValueError(f"Expected JSON array, got {type(raw)}")

    result: list[TraceEvent] = []
    for obj in raw:
        if not isinstance(obj, dict):
            continue
        ph = obj.get("ph")
        if ph == "M":
            name = obj.get("name")
            if name == "process_name":
                result.append(msgspec.convert(obj, ProcessMeta))
            elif name == "thread_name":
                result.append(msgspec.convert(obj, ThreadMeta))
        elif ph == "C":
            result.append(msgspec.convert(obj, CounterEvent))
        elif ph == "X":
            args = obj.get("args")
            if isinstance(args, dict):
                if "collected" in args:
                    result.append(msgspec.convert(obj, PauseEvent))
                else:
                    result.append(msgspec.convert(obj, IncrementalEvent))
            else:
                raise ValueError(f"Expected args should be a dict, not: {type(args)}")
        elif ph == "I":
            result.append(msgspec.convert(obj, InstantEvent))
    return result


def _normalize_trace_timestamps(events: list[TraceEvent]) -> None:
    by_pid: dict[int, list[PauseEvent | IncrementalEvent | CounterEvent | InstantEvent]] = {}
    for event in events:
        if event["ph"] in ("X", "C", "I"):
            by_pid.setdefault(event["pid"], []).append(event)  # pyrefly: ignore[bad-argument-type]

    for timed in by_pid.values():
        min_ts = min(e["ts"] for e in timed)
        for e in timed:
            e["ts"] = e["ts"] - min_ts


def _normalize_jsonl_timestamps(items: dict[int, list[TGCStatsInfo | TInstantMsg]]) -> None:
    for pid_items in items.values():
        timestamps: list[int] = []
        for item in pid_items:
            if is_instant(item):
                timestamps.append(item.ts)
            elif is_gc_stats(item):
                timestamps.append(item.ts_start)
        if not timestamps:
            continue

        min_ts = min(timestamps)

        for item in pid_items:
            if is_instant(item):
                item.ts -= min_ts
            elif is_gc_stats(item):
                item.ts_start -= min_ts
                item.ts_stop -= min_ts
                if has_mark_alive(item):
                    item.ts_mark_alive_start -= min_ts
                    item.ts_mark_alive_stop -= min_ts
                if has_incremental(item):
                    item.ts_fill_increment_start -= min_ts
                    item.ts_fill_increment_stop -= min_ts
                if has_deduce_unreachable(item):
                    item.ts_deduce_unreachable_start -= min_ts
                    item.ts_deduce_unreachable_stop -= min_ts
                if has_handle_weakrefs(item):
                    item.ts_handle_weakref_callbacks_start -= min_ts
                    item.ts_handle_weakref_callbacks_stop -= min_ts
                if has_finalize_garbage(item):
                    item.ts_finalize_garbage_stop -= min_ts
                if has_handle_resurrected(item):
                    item.ts_handle_resurrected_stop -= min_ts
                if has_clear_weakrefs(item):
                    item.ts_clear_weakrefs_stop -= min_ts
                if has_delete_garbage(item):
                    item.ts_delete_garbage_start -= min_ts
                    item.ts_delete_garbage_stop -= min_ts


def combine_files(input_paths: list[Path], output_path: Path, normalize: bool = False,
                  input_format: str = "chrome", output_format: str = "chrome") -> None:
    if input_format == "chrome" and output_format == "jsonl":
        raise ValueError(
            "Input format 'chrome' with output format 'jsonl' is not supported. "
            "Use --output-format 'chrome' instead."
        )

    if input_format == "chrome":
        chrome_events: list[TraceEvent] = []

        for input_path in input_paths:
            with open(input_path, encoding="utf-8") as f:
                content = f.read()
            events = _parse_events(content)

            if normalize:
                _normalize_trace_timestamps(events)

            chrome_events.extend(events)

        write_trace_events(output_path, chrome_events)

    elif input_format == "jsonl" and output_format == "chrome":
        trace_events: list[TraceEvent] = []

        for input_path in input_paths:
            items = read_jsonl(input_path)
            events = convert_to_trace_format(items)
            trace_events.extend(events)

        if normalize:
            _normalize_trace_timestamps(trace_events)

        write_trace_events(output_path, trace_events)

    elif input_format == "jsonl" and output_format == "jsonl":
        all_items: dict[int, list[TGCStatsInfo | TInstantMsg]] = {}

        for input_path in input_paths:
            items = read_jsonl(input_path)
            for pid, pid_items in items.items():
                if pid not in all_items:
                    all_items[pid] = pid_items
                else:
                    all_items[pid].extend(pid_items)

        if normalize:
            _normalize_jsonl_timestamps(all_items)

        write_jsonl(output_path, all_items)
