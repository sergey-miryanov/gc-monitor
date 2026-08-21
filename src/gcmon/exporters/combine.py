"""The `gcmon combine` command: many captures in, one trace out."""

from pathlib import Path

from ..model.protocol import TItem
from ..model.trace_event import (
    BeginEvent,
    CounterEvent,
    EndEvent,
    InstantEvent,
    TraceEvent,
)
from .encoder import ProtobufEventEncoder
from .jsonl_io import normalize_jsonl_timestamps, read_jsonl, write_jsonl
from .trace_converter import convert_to_trace_format

__all__ = [
    "combine_files",
]


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
    output_format: str = "perfetto",
) -> None:
    """Merge JSONL captures into one JSONL capture or one Perfetto trace.

    The two paths normalize over different scopes; ADR-0021 records why.
    """
    if output_format == "jsonl":
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

    if output_format != "perfetto":
        raise ValueError(f"Unsupported output format: {output_format}")

    trace_events: list[TraceEvent] = []

    for input_path in input_paths:
        file_events = convert_to_trace_format(read_jsonl(input_path))

        if normalize:
            _normalize_trace_timestamps(file_events)

        trace_events.extend(file_events)

    perfetto_encoder = ProtobufEventEncoder()
    perfetto_encoder.open(output_path)
    try:
        perfetto_encoder.write_events(trace_events)
    finally:
        perfetto_encoder.close()
