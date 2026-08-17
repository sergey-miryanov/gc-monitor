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
    ENV_RSS,
    ENV_RSS_INTERVAL,
    ENV_STATS,
    ENV_TABLE_FORMAT,
    ENV_VERBOSE,
    get_env_control_name,
    get_env_duration,
    get_env_flush_threshold,
    get_env_format,
    get_env_output,
    get_env_rate,
    get_env_rss,
    get_env_rss_interval,
    get_env_stats,
    get_env_table_format,
    get_env_verbose,
)
from gcmon.stats_output import StatsView, TableFormat

logger = logging.getLogger("gcmon")

# Formats whose exporters implement EventsExporter.add_rss_sample. The others
# inherit the no-op base implementation and silently discard RSS samples.
RSS_CAPABLE_FORMATS = ("chrome", "trace", "perfetto", "chrome+perfetto")

# Values of `--stats` and GCMON_STATS that ask for no table. They are the falsy
# complements of the truthy set the variable took while it was a switch, so a
# variable already set to `0` keeps meaning what it meant. The truthy set does
# not come back: "no table" is one outcome, while "a table" is two, and which
# one `1` asks for is the question the two view names exist to make explicit.
STATS_OFF_WORDS = ("no", "off", "false", "0")


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
        choices=["chrome", "perfetto", "stdout", "jsonl", "chrome+perfetto"],
        default=get_env_format(),
        help=(
            f"Output format: 'chrome' for Chrome DevTools, 'perfetto' for Perfetto binary protobuf, "
            f"'chrome+perfetto' for both Chrome JSON and Perfetto binary outputs (the `-o` argument is a "
            f"base name; `.json` and `.pftrace` extensions are appended), 'stdout' for one-line-per-event "
            f"JSONL to stdout, 'jsonl' for JSONL file (default: chrome or {ENV_FORMAT} env var)"
        ),
    )
    parser.add_argument(
        "--flush-threshold",
        type=int,
        default=get_env_flush_threshold(),
        help=f"Number of events to buffer before flushing to file for JSONL format (default: 100 or {ENV_FLUSH_THRESHOLD} env var)",
    )
    # A required value, not `nargs="?"` with a `const`: `monitor` takes the pid
    # as a required positional, and argparse decides whether an optional with
    # `nargs="?"` eats the next token by whether it starts with `-`, which a
    # pid does not. An alias would break `gcmon monitor --stats 12345` while
    # appearing to keep every spelling working.
    parser.add_argument(
        "--stats",
        choices=[view.value for view in StatsView] + list(STATS_OFF_WORDS),
        default=get_env_stats(),
        help=(
            f"Show a statistics table at the end of monitoring: 'total' for the run-wide block, "
            f"'full' for that plus one block per interpreter. 'no', 'off', 'false' or '0' asks "
            f"for none, which is what an unset flag asks for and is the way to overrule "
            f"{ENV_STATS} for one run. {ENV_STATS} takes the same words. High-accuracy "
            f"percentiles need the stats extra: pip install gcmon[stats]"
        ),
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
    parser.add_argument(
        "--rss",
        action="store_true",
        default=get_env_rss(),
        help=(
            f"Track RSS (Resident Set Size) of monitored process (supported for --format chrome, "
            f"perfetto, chrome+perfetto; requires psutil; or {ENV_RSS}=1 env var)"
        ),
    )
    parser.add_argument(
        "--rss-interval",
        type=float,
        default=get_env_rss_interval(),
        help=f"RSS sampling interval in seconds (default: 1.0 or {ENV_RSS_INTERVAL} env var)",
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
        stats_view: StatsView | None = None,
        table_format: TableFormat = TableFormat.PLAIN,
        rss_enabled: bool = False,
        rss_interval: float = 1.0,
    ) -> None:
        self.output_path = output_path
        self.rate = rate
        self.duration = duration
        self.output_format = output_format
        self.flush_threshold = flush_threshold
        self.duration_label = duration_label
        # `None` is no table at all: the run an operator who typed nothing
        # asked for, and the run one of `STATS_OFF_WORDS` asks for out loud.
        self.stats_view = stats_view
        self.table_format = table_format
        self.rss_enabled = rss_enabled
        self.rss_interval = rss_interval


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
    table_format = args.table_format
    rss_enabled = args.rss
    rss_interval = args.rss_interval

    if output_format != "stdout":
        logger.info("Output: %s", output_path)
    logger.info("Format: %s", output_format)
    logger.info("Rate: %ss", rate)
    if duration is not None:
        logger.info("Duration: %ss", duration)
    else:
        logger.info("Duration: %s", duration_label)
    if rss_enabled:
        logger.info("RSS tracking: enabled (interval: %ss)", rss_interval)
        if output_format not in RSS_CAPABLE_FORMATS:
            logger.warning(
                "RSS tracking is not supported for --format %s; RSS samples will be discarded.",
                output_format,
            )
        if rss_interval < rate:
            logger.warning(
                "RSS interval (%ss) is shorter than poll rate (%ss); "
                "RSS will be sampled at the poll rate, not the RSS interval.",
                rss_interval,
                rate,
            )

    # `--stats` is checked against the same words at parse time, so the only
    # value that can be unknown here is one the environment carried in. Case
    # and surrounding space are forgiven, as `GCMON_TABLE_FORMAT` and
    # `GCMON_FORMAT` forgive them: an env file keeps a trailing space and a
    # compose block is as likely to say `Total`. The word itself is not.
    stats_view: StatsView | None = None
    if args.stats is not None:
        word = args.stats.strip().lower()
        if word not in STATS_OFF_WORDS:
            try:
                stats_view = StatsView(word)
            except ValueError:
                logger.error(
                    "%s must be 'total', 'full', or one of %s to ask for no table, got '%s'",
                    ENV_STATS,
                    ", ".join(f"'{off}'" for off in STATS_OFF_WORDS),
                    args.stats,
                )
                return None

    if rate <= 0:
        logger.error("Rate must be positive, got %s", rate)
        return None
    if duration is not None and duration <= 0:
        logger.error("Duration must be positive, got %s", duration)
        return None
    if flush_threshold <= 0:
        logger.error("Flush threshold must be positive, got %s", flush_threshold)
        return None
    if rss_enabled and rss_interval <= 0:
        logger.error("RSS interval must be positive, got %s", rss_interval)
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
        stats_view=stats_view,
        table_format=table_format,
        rss_enabled=rss_enabled,
        rss_interval=rss_interval,
    )
