"""File I/O and utility functions for Chrome Trace Event format."""

from collections.abc import Mapping, Sequence
from pathlib import Path

import msgspec

from ..model.data import from_mapping
from ..model.protocol import (
    JsonlRecord,
    TItem,
    TMapping,
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
    is_loss,
    to_mapping,
)
from ..model.trace_event import (
    BeginEvent,
    CounterEvent,
    EndEvent,
    InstantEvent,
    ProcessMeta,
    ThreadMeta,
    TraceEvent,
    loss_tid,
)
from .chrome_trace_format import convert_to_trace_format
from .encoder import JsonEventEncoder, ProtobufEventEncoder

__all__ = [
    "combine_files",
    "convert_jsonl_to_trace_format",
    "read_jsonl",
]


def json_to_item(data: TMapping) -> tuple[int, TItem]:
    pid = data["pid"]
    # A pid gcmon wrote decodes as an int, and every line of a capture carries
    # one. Anything else goes through a lax convert, so a pid written as a
    # string still reads.
    if not isinstance(pid, int):
        pid = msgspec.convert(pid, int, strict=False)
    return pid, from_mapping(data)


def read_jsonl(filename: Path) -> dict[int, list[TItem]]:
    items: dict[int, list[TItem]] = {}
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


def write_jsonl(filename: Path, items: Mapping[int, Sequence[TItem]]) -> None:
    """Write GC stats items to a JSONL file."""
    with open(filename, "wb") as f:
        for pid, pid_items in items.items():
            for item in pid_items:
                rec: JsonlRecord = {"pid": pid}
                if is_loss(item):
                    rec["tid"] = loss_tid(item.iid)
                elif is_gc_stats(item):
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
        elif ph == "B":
            result.append(msgspec.convert(obj, BeginEvent))
        elif ph == "E":
            result.append(msgspec.convert(obj, EndEvent))
        elif ph == "I":
            result.append(msgspec.convert(obj, InstantEvent))
    return result


def _normalize_trace_timestamps(events: list[TraceEvent]) -> None:
    by_pid: dict[int, list[BeginEvent | EndEvent | CounterEvent | InstantEvent]] = {}
    for event in events:
        if event.ph in ("B", "E", "C", "I"):
            by_pid.setdefault(event.pid, []).append(event)

    for timed in by_pid.values():
        min_ts = min(e.ts for e in timed)
        for e in timed:
            e.ts = e.ts - min_ts


def _normalize_jsonl_timestamps(items: Mapping[int, Sequence[TItem]]) -> None:
    for pid_items in items.values():
        timestamps: list[int] = []
        for item in pid_items:
            if is_instant(item):
                timestamps.append(item.ts)
            elif is_loss(item) or is_gc_stats(item):
                timestamps.append(item.ts_start)
        if not timestamps:
            continue

        min_ts = min(timestamps)

        for item in pid_items:
            if is_instant(item):
                item.ts -= min_ts
            elif is_loss(item):
                item.ts_start -= min_ts
                item.ts_stop -= min_ts
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


def combine_files(
    input_paths: list[Path],
    output_path: Path,
    normalize: bool = False,
    input_format: str = "chrome",
    output_format: str = "chrome",
) -> None:
    if input_format == "chrome" and output_format == "jsonl":
        raise ValueError(
            "Input format 'chrome' with output format 'jsonl' is not supported. "
            "Use --output-format 'chrome' or 'perfetto' instead."
        )

    if input_format == "jsonl" and output_format == "jsonl":
        all_items: dict[int, list[TItem]] = {}

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
        return

    trace_events: list[TraceEvent] = []

    for input_path in input_paths:
        if input_format == "chrome":
            with open(input_path, encoding="utf-8") as f:
                content = f.read()
            file_events = _parse_events(content)
        else:
            items = read_jsonl(input_path)
            file_events = convert_to_trace_format(items)

        if normalize:
            _normalize_trace_timestamps(file_events)

        trace_events.extend(file_events)

    if output_format == "chrome":
        chrome_encoder = JsonEventEncoder()
        chrome_encoder.open(output_path)
        try:
            chrome_encoder.write_events(trace_events)
        finally:
            chrome_encoder.close()
    elif output_format == "perfetto":
        perfetto_encoder = ProtobufEventEncoder()
        perfetto_encoder.open(output_path)
        try:
            perfetto_encoder.write_events(trace_events)
        finally:
            perfetto_encoder.close()
    else:
        raise ValueError(f"Unsupported output format: {output_format}")
