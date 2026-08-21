"""The `gcmon combine` command: many captures in, one trace out."""

from pathlib import Path

import msgspec

from ..model.protocol import TItem
from ..model.trace_event import (
    BeginEvent,
    CounterEvent,
    EndEvent,
    InstantEvent,
    ProcessMeta,
    ThreadMeta,
    TraceEvent,
)
from .encoder import JsonEventEncoder, ProtobufEventEncoder
from .jsonl_io import normalize_jsonl_timestamps, read_jsonl, write_jsonl
from .trace_converter import convert_to_trace_format

__all__ = [
    "combine_files",
]


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
            normalize_jsonl_timestamps(all_items)

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
