"""Shared monitoring options for CLI commands."""

import argparse
import logging
from pathlib import Path

from gcmon._env import (
    ENV_CONTROL_NAME,
    ENV_DURATION,
    ENV_FLUSH_THRESHOLD,
    ENV_FORMAT,
    ENV_OUTPUT,
    ENV_RATE,
    ENV_STATS,
    ENV_TABLE_FORMAT,
    ENV_VERBOSE,
    get_env_control_name,
    get_env_duration,
    get_env_flush_threshold,
    get_env_format,
    get_env_output,
    get_env_rate,
    get_env_stats,
    get_env_table_format,
    get_env_verbose,
)
from gcmon.stats_output import TableFormat

logger = logging.getLogger("gcmon")


def _normalize_table_format(val: str) -> TableFormat:
    val = val.lower()
    if val == "md" or val == "markdown":
        return TableFormat.MARKDOWN
    if val == "plain":
        return TableFormat.PLAIN
    raise argparse.ArgumentTypeError(f"expected 'plain', 'markdown', or 'md', got '{val}'")


def add_monitoring_options(parser: argparse.ArgumentParser) -> None:
    """Add common monitoring options to a command parser."""
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=get_env_output(),
        help=f"Output file path (default: gcmon.json, gcmon.jsonl for jsonl format, or {ENV_OUTPUT} env var). Ignored for --format stdout",
    )
    parser.add_argument(
        "-r",
        "--rate",
        type=float,
        default=get_env_rate(),
        help=f"Polling rate in seconds (default: 0.1 or {ENV_RATE} env var)",
    )
    parser.add_argument(
        "-d",
        "--duration",
        type=float,
        default=get_env_duration(),
        help=f"Monitoring duration in seconds (default: run until interrupted or {ENV_DURATION} env var)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=get_env_verbose(),
        help=f"Enable verbose output (use -v for INFO, -vv for DEBUG, or set via {ENV_VERBOSE} env var: 1, 2, true, yes, on)",
    )
    parser.add_argument(
        "--format",
        choices=["chrome", "perfetto", "stdout", "jsonl"],
        default=get_env_format(),
        help=f"Output format: 'chrome' for Chrome DevTools, 'perfetto' for Perfetto binary protobuf, 'stdout' for one-line-per-event JSONL to stdout, 'jsonl' for JSONL file (default: chrome or {ENV_FORMAT} env var)",
    )
    parser.add_argument(
        "--flush-threshold",
        type=int,
        default=get_env_flush_threshold(),
        help=f"Number of events to buffer before flushing to file for JSONL format (default: 100 or {ENV_FLUSH_THRESHOLD} env var)",
    )
    parser.add_argument(
        "--stats",
        action="store_true",
        default=get_env_stats(),
        help=f"Show statistics table at end of monitoring (requires stats extra: pip install gcmon[stats] or {ENV_STATS}=1 env var)",
    )
    parser.add_argument(
        "--table-format",
        type=_normalize_table_format,
        default=get_env_table_format(),
        help=f"Table format: 'plain' for standard dashes, 'markdown' or 'md' for blank separators (default: plain or {ENV_TABLE_FORMAT} env var)",
    )
    parser.add_argument(
        "--control-name",
        default=get_env_control_name(),
        help=f"Control plane name. Full address: gcmon-<name> (default: auto, or {ENV_CONTROL_NAME} env var)",
    )


class MonitoringOptions:
    """Monitoring configuration."""

    def __init__(
        self,
        output_path: Path,
        rate: float,
        duration: float | None,
        output_format: str,
        flush_threshold: int,
        duration_label: str,
        show_stats: bool = False,
        table_format: TableFormat = TableFormat.PLAIN,
    ) -> None:
        self.output_path = output_path
        self.rate = rate
        self.duration = duration
        self.output_format = output_format
        self.flush_threshold = flush_threshold
        self.duration_label = duration_label
        self.show_stats = show_stats
        self.table_format = table_format


def get_monitoring_options(
    args: argparse.Namespace,
    duration_label: str = "until interrupted",
) -> MonitoringOptions | None:
    """Extract and validate monitoring options from parsed args.

    Returns None if validation fails.
    """
    output_path: Path = args.output
    rate = args.rate
    duration = args.duration
    output_format = args.format
    flush_threshold = args.flush_threshold
    show_stats = args.stats
    table_format = args.table_format

    if output_format != "stdout":
        logger.info("Output: %s", output_path)
    logger.info("Format: %s", output_format)
    logger.info("Rate: %ss", rate)
    if duration is not None:
        logger.info("Duration: %ss", duration)
    else:
        logger.info("Duration: %s", duration_label)

    if rate <= 0:
        logger.error("Rate must be positive, got %s", rate)
        return None
    if duration is not None and duration <= 0:
        logger.error("Duration must be positive, got %s", duration)
        return None
    if flush_threshold <= 0:
        logger.error("Flush threshold must be positive, got %s", flush_threshold)
        return None

    if output_format != "stdout":
        resolved = output_path.resolve()
        if not resolved.parent.is_dir():
            logger.error("Output directory does not exist: %s", resolved.parent)
            return None

    return MonitoringOptions(
        output_path=output_path,
        rate=rate,
        duration=duration,
        output_format=output_format,
        flush_threshold=flush_threshold,
        duration_label=duration_label,
        show_stats=show_stats,
        table_format=table_format,
    )
