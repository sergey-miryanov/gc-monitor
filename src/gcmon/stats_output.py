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

    headers = ["PID", "Metric", "Count", "Sum", "Avg", "P50", "P90", "P95", "P99", "Cov", "F"]
    data = [r for r in rows if r is not _SEP_GROUP and r is not _SEP_PHASE]
    w_type = max(len(h) for h in [headers[0]] + [r[0] for r in data])
    w_metric = max(len(h) for h in [headers[1]] + [r[1] for r in data])
    w_count = max(len(h) for h in [headers[2]] + [r[2] for r in data])
    w_sum = max(len(h) for h in [headers[3]] + [r[3] for r in data])
    w_pct = max(len(v) for h in headers[4:9] for v in [h] + [r[i] for r in data for i in range(4, 9)])
    w_cov = max(len(v) for h in headers[9:] for v in [h] + [r[i] for r in data for i in range(9, 11)])

    widths = [w_type + 2, w_metric + 2, w_count + 2, w_sum + 2, *[w_pct + 2] * 5, *[w_cov + 2] * 2]
    sep_full = "|" + "|".join("-" * w for w in widths) + "|"
    sep_phase = "|" + " " * widths[0] + "|" + "|".join("-" * w for w in widths[1:]) + "|"
    sep_blank = "|" + "|".join(" " * w for w in widths) + "|"
    use_markdown = table_format == TableFormat.MARKDOWN
    print("")
    print(
        f"| {headers[0]:<{w_type}} | {headers[1]:<{w_metric}} | {headers[2]:>{w_count}} | {headers[3]:>{w_sum}} "
        f"| {headers[4]:>{w_pct}} | {headers[5]:>{w_pct}} | {headers[6]:>{w_pct}} "
        f"| {headers[7]:>{w_pct}} | {headers[8]:>{w_pct}} "
        f"| {headers[9]:>{w_cov}} | {headers[10]:>{w_cov}} |"
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
            f"| {r[7]:>{w_pct}} | {r[8]:>{w_pct}} "
            f"| {r[9]:>{w_cov}} | {r[10]:>{w_cov}} |"
        )


def _dual(sampled: str, whole: str | None, marker: str = "") -> str:
    """``sampled/whole``, or one value when the two agree.

    Nothing was lost is worth saying once, not twice in every cell.
    """
    if whole is None or whole == sampled:
        return sampled
    return f"{sampled}/{marker}{whole}"


def _format_stats(s: Stats, count: str | None = None, total: str | None = None) -> list[str]:
    """Format a Stats of nanosecond durations as table cells in milliseconds.

    *count* and *total* replace the first two cells when given, already
    carrying their exact or estimated companion. The rest stay sampled:
    percentiles cannot be corrected, and an average of a sampled set is a
    sampled average.
    """
    return [
        count if count is not None else str(s.count()),
        total if total is not None else f"{dur_to_ms(s.sum()):.3f}",
        f"{dur_to_ms(s.average()):.3f}",
        f"{dur_to_ms(s.percentile(50)):.3f}",
        f"{dur_to_ms(s.percentile(90)):.3f}",
        f"{dur_to_ms(s.percentile(95)):.3f}",
        f"{dur_to_ms(s.percentile(99)):.3f}",
    ]


def _build_rows(
    gen_stats: dict[int, Stats],
    label: str,
    stats: StreamingStats,
    pid: int | None,
    exact: bool,
) -> list[list[str]]:
    """One row per generation that recorded anything.

    *exact* separates the `GC Pause` rows, whose companion numbers come from
    the target's own counters, from the sub-phase rows, whose companions are
    the sampled value scaled by ``F`` and are marked ``~`` for it.
    """
    rows = []
    for gen in sorted(gen_stats.keys()):
        s = gen_stats[gen]
        if s.count() == 0:
            continue

        coverage = stats.coverage(pid, gen)
        factor = stats.scale_factor(pid, gen)
        if exact:
            count = str(stats.exact_count(pid, gen))
            total = f"{dur_to_ms(stats.exact_pause_ns(pid, gen)):.3f}"
            marker = ""
        else:
            count = str(round(s.count() / coverage))
            total = f"{dur_to_ms(s.sum() * factor):.3f}"
            marker = "~"

        cells = _format_stats(
            s, _dual(str(s.count()), count, marker), _dual(f"{dur_to_ms(s.sum()):.3f}", total, marker)
        )
        lost = stats.lost_count(pid, gen)
        rows.append([f"{label}({gen})", *cells, _coverage_cell(coverage, lost), _factor_cell(factor, lost)])
    return rows


def _coverage_cell(coverage: float, lost: int) -> str:
    """``Cov`` must never round to a figure the cells beside it contradict.

    A session that lost eight records in 1771 is at 99.5%, which reads as
    100% at two decimals while ``Count`` plainly shows a gap. Where rounding
    would claim completeness that the numbers deny, say so instead.
    """
    cell = f"{coverage:.1%}"
    return "<100.0%" if lost and cell == "100.0%" else cell


def _factor_cell(factor: float, lost: int) -> str:
    cell = f"{factor:.3f}"
    return ">1.000" if lost and cell == "1.000" else cell


def print_stats(stats: StreamingStats, table_format: TableFormat = TableFormat.PLAIN) -> None:
    all_rows: list[list[str] | Any] = []

    first = True
    has_rows = False
    for metric_key, metric in METRICS.items():
        rows = _build_rows(stats.metrics[metric_key], metric.name, stats, None, metric_key == "pause")
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
            rows = _build_rows(pid_data.get(metric_key, {}), metric.name, stats, pid, metric_key == "pause")
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
        all_rows.append(["", "Read Time", *_format_stats(rt), "", ""])

    if not all_rows:
        print("No GC statistics collected.")
        return

    _print_table(all_rows, table_format=table_format)
    _print_footer(stats)


def _print_footer(stats: StreamingStats) -> None:
    """What the table's two number kinds mean.

    Only printed when something was lost or the target collected before gcmon
    attached. A run that saw everything has nothing to explain.

    Which notes appear depends on the run, so the number, not the order, is
    what separates two that wrap on a narrow terminal. A lone note still
    reads ``1.``.
    """
    covered = [gen for gen in stats.GENS if stats.lost_count(None, gen)]
    lifetime = [gen for gen in stats.GENS if stats.lifetime_count(None, gen)]

    notes: list[str] = []
    if covered:
        parts = ", ".join(
            f"Gen{gen} {_coverage_cell(stats.coverage(None, gen), stats.lost_count(None, gen))}" for gen in covered
        )
        notes.append(f"Coverage: {parts}. Count and Sum read sampled/exact; percentiles are sampled and read high.")
    if lifetime:
        parts = ", ".join(
            f"Gen{gen} {stats.lifetime_count(None, gen)} in {dur_to_ms(stats.lifetime_pause_ns(None, gen)):.3f} ms"
            for gen in lifetime
        )
        # "Since interpreter start" covers the monitored window rather than
        # excluding it, so the note must not read as a figure to add to
        # `Count`. `lifetime_count` is the target's own cumulative counter.
        notes.append(f"Since interpreter start, monitored window included: {parts}.")

    if not notes:
        return

    print("")
    for number, note in enumerate(notes, start=1):
        print(f"{number}. {note}")
