"""Statistics output formatting for GC monitoring."""

from enum import Enum
from typing import Any

from .data import dur_to_ms
from .stats import METRICS, Stats, StreamingStats

_SEP_GROUP: Any = object()
_SEP_PHASE: Any = object()


class TableFormat(Enum):
    PLAIN = "plain"
    MARKDOWN = "markdown"


def _print_table(rows: list[list[str] | Any], table_format: TableFormat = TableFormat.PLAIN) -> None:
    if not rows:
        return

    headers = ["PID", "Metric", "Count", "Sum", "Avg", "P50", "P90", "P95", "P99"]
    data = [r for r in rows if r is not _SEP_GROUP and r is not _SEP_PHASE]
    w_type = max(len(h) for h in [headers[0]] + [r[0] for r in data])
    w_metric = max(len(h) for h in [headers[1]] + [r[1] for r in data])
    w_count = max(len(h) for h in [headers[2]] + [r[2] for r in data])
    w_sum = max(len(h) for h in [headers[3]] + [r[3] for r in data])
    w_pct = max(len(v) for h in headers[4:] for v in [h] + [r[i] for r in data for i in range(4, 9)])

    sep_full = (
        "|" + "|".join("-" * w for w in [w_type + 2, w_metric + 2, w_count + 2, w_sum + 2] + [w_pct + 2] * 5) + "|"
    )
    sep_phase = (
        "|"
        + " " * (w_type + 2)
        + "|"
        + "|".join("-" * w for w in [w_metric + 2, w_count + 2, w_sum + 2] + [w_pct + 2] * 5)
        + "|"
    )
    sep_blank = (
        "|" + "|".join(" " * w for w in [w_type + 2, w_metric + 2, w_count + 2, w_sum + 2] + [w_pct + 2] * 5) + "|"
    )
    use_markdown = table_format == TableFormat.MARKDOWN
    print("")
    print(
        f"| {headers[0]:<{w_type}} | {headers[1]:<{w_metric}} | {headers[2]:>{w_count}} | {headers[3]:>{w_sum}} "
        f"| {headers[4]:>{w_pct}} | {headers[5]:>{w_pct}} | {headers[6]:>{w_pct}} "
        f"| {headers[7]:>{w_pct}} | {headers[8]:>{w_pct}} |"
    )
    print(sep_full)
    for r in rows:
        if r is _SEP_GROUP:
            print(sep_blank if use_markdown else sep_full)
            continue
        if r is _SEP_PHASE:
            print(sep_blank if use_markdown else sep_phase)
            continue
        print(
            f"| {r[0]:<{w_type}} | {r[1]:<{w_metric}} | {r[2]:>{w_count}} | {r[3]:>{w_sum}} "
            f"| {r[4]:>{w_pct}} | {r[5]:>{w_pct}} | {r[6]:>{w_pct}} "
            f"| {r[7]:>{w_pct}} | {r[8]:>{w_pct}} |"
        )


def _format_stats(s: Stats) -> list[str]:
    """Format a Stats of nanosecond durations as table cells in milliseconds."""
    return [
        str(s.count()),
        f"{dur_to_ms(s.sum()):.3f}",
        f"{dur_to_ms(s.average()):.3f}",
        f"{dur_to_ms(s.percentile(50)):.3f}",
        f"{dur_to_ms(s.percentile(90)):.3f}",
        f"{dur_to_ms(s.percentile(95)):.3f}",
        f"{dur_to_ms(s.percentile(99)):.3f}",
    ]


def _build_rows(gen_stats: dict[int, Stats], label: str) -> list[list[str]]:
    rows = []
    for gen in sorted(gen_stats.keys()):
        s = gen_stats[gen]
        if s.count() == 0:
            continue
        rows.append([f"{label}({gen})", *_format_stats(s)])
    return rows


def print_stats(stats: StreamingStats, table_format: TableFormat = TableFormat.PLAIN) -> None:
    all_rows: list[list[str] | Any] = []

    first = True
    has_rows = False
    for metric_key, metric in METRICS.items():
        rows = _build_rows(stats.metrics[metric_key], metric.name)
        if rows:
            if has_rows:
                all_rows.append(_SEP_PHASE)
            for row in rows:
                all_rows.append(["Total" if first else "", *row])
                first = False
            has_rows = True

    for pid in sorted(stats.pids()):
        all_rows.append(_SEP_GROUP)
        pid_data = stats.get_pid_stats(pid)
        if pid_data is None:
            continue

        first = True
        has_rows = False
        for metric_key, metric in METRICS.items():
            rows = _build_rows(pid_data.get(metric_key, {}), metric.name)
            if rows:
                if has_rows:
                    all_rows.append(_SEP_PHASE)
                for row in rows:
                    all_rows.append([str(pid) if first else "", *row])
                    first = False
                has_rows = True

    rt = stats.read_time
    if rt.count() > 0:
        all_rows.append(_SEP_GROUP)
        all_rows.append(["", "Read Time", *_format_stats(rt)])

    if not all_rows:
        print("No GC statistics collected.")
        return

    _print_table(all_rows, table_format=table_format)
