"""Command-line interface for gc-monitor."""

import argparse
import logging
import os
import signal
import sys
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING

from . import TraceExporter, connect
from .chrome_trace_exporter import combine_files
from .jsonl_exporter import JsonlExporter
from .stdout_exporter import StdoutExporter

if TYPE_CHECKING:
    from .exporter import GCMonitorExporter

logger = logging.getLogger("gc_monitor")

# Environment variable names for CLI options
ENV_PREFIX = "GC_MONITOR"
ENV_OUTPUT = f"{ENV_PREFIX}_OUTPUT"
ENV_RATE = f"{ENV_PREFIX}_RATE"
ENV_DURATION = f"{ENV_PREFIX}_DURATION"
ENV_VERBOSE = f"{ENV_PREFIX}_VERBOSE"
ENV_FORMAT = f"{ENV_PREFIX}_FORMAT"
ENV_THREAD_NAME = f"{ENV_PREFIX}_THREAD_NAME"
ENV_THREAD_ID = f"{ENV_PREFIX}_THREAD_ID"
ENV_FALLBACK = f"{ENV_PREFIX}_FALLBACK"
ENV_FLUSH_THRESHOLD = f"{ENV_PREFIX}_FLUSH_THRESHOLD"


def _setup_logging(verbose: bool) -> None:
    """Configure logging for the CLI.

    Args:
        verbose: If True, set log level to INFO; otherwise WARNING.
    """
    level = logging.INFO if verbose else logging.WARNING
    logging.basicConfig(
        level=level,
        format="[%(name)s] %(levelname)s: %(message)s",
    )


def _get_env_output() -> Path:
    """Get output path from environment variable.

    Returns:
        Path from GC_MONITOR_OUTPUT env var, or default Path("gc_trace.json").
    """
    output_str = os.environ.get(ENV_OUTPUT)
    if output_str:
        return Path(output_str)
    # Check format for default filename
    format_str = os.environ.get(ENV_FORMAT)
    if format_str and format_str.lower() == "jsonl":
        return Path("gc_monitor.jsonl")
    return Path("gc_trace.json")


def _get_env_rate() -> float:
    """Get polling rate from environment variable.

    Returns:
        Rate from GC_MONITOR_RATE env var, or default 0.1.
    """
    rate_str = os.environ.get(ENV_RATE)
    if rate_str:
        try:
            return float(rate_str)
        except ValueError:
            pass
    return 0.1


def _get_env_duration() -> float | None:
    """Get monitoring duration from environment variable.

    Returns:
        Duration from GC_MONITOR_DURATION env var, or None (run until interrupted).
    """
    duration_str = os.environ.get(ENV_DURATION)
    if duration_str:
        try:
            return float(duration_str)
        except ValueError:
            pass
    return None


def _get_env_verbose() -> bool:
    """Get verbose flag from environment variable.

    Returns:
        True if GC_MONITOR_VERBOSE is set to a truthy value, False otherwise.
    """
    verbose_str = os.environ.get(ENV_VERBOSE, "").lower()
    return verbose_str in ("1", "true", "yes", "on")


def _get_env_format() -> str:
    """Get output format from environment variable.

    Returns:
        Format from GC_MONITOR_FORMAT env var, or default "chrome".
    """
    format_str = os.environ.get(ENV_FORMAT)
    if format_str:
        format_lower = format_str.lower()
        if format_lower in ("chrome", "stdout", "jsonl"):
            return format_lower
    return "chrome"


def _get_env_thread_name() -> str:
    """Get thread name from environment variable.

    Returns:
        Thread name from GC_MONITOR_THREAD_NAME env var, or default "GC Monitor".
    """
    thread_name = os.environ.get(ENV_THREAD_NAME)
    if thread_name:
        return thread_name
    return "GC Monitor"


def _get_env_thread_id() -> int:
    """Get thread ID from environment variable.

    Returns:
        Thread ID from GC_MONITOR_THREAD_ID env var, or default 0.
    """
    thread_id_str = os.environ.get(ENV_THREAD_ID)
    if thread_id_str:
        try:
            return int(thread_id_str)
        except ValueError:
            pass
    return 0


def _get_env_fallback() -> str:
    """Get fallback setting from environment variable.

    Returns:
        Fallback from GC_MONITOR_FALLBACK env var, or default "yes".
    """
    fallback_str = os.environ.get(ENV_FALLBACK)
    if fallback_str:
        fallback_lower = fallback_str.lower()
        if fallback_lower in ("yes", "no"):
            return fallback_lower
    return "yes"


def _get_env_flush_threshold() -> int:
    """Get flush threshold from environment variable.

    Returns:
        Flush threshold from GC_MONITOR_FLUSH_THRESHOLD env var, or default 100.
    """
    threshold_str = os.environ.get(ENV_FLUSH_THRESHOLD)
    if threshold_str:
        try:
            return int(threshold_str)
        except ValueError:
            pass
    return 100


def _create_parser() -> argparse.ArgumentParser:
    """Create the argument parser with subcommands."""
    parser = argparse.ArgumentParser(
        prog="gc-monitor",
        description="Monitor Python's garbage collector and export statistics.",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Monitor command (default behavior)
    monitor_parser = subparsers.add_parser(
        "monitor",
        help="Monitor a process's garbage collection",
        description="Monitor Python's garbage collector and export statistics.",
    )
    monitor_parser.add_argument(
        "pid",
        type=int,
        help="Process ID to monitor",
    )
    monitor_parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=_get_env_output(),
        help=f"Output file path (default: gc_trace.json, gc_monitor.jsonl for jsonl format, or {ENV_OUTPUT} env var). Ignored for --format stdout",
    )
    monitor_parser.add_argument(
        "-r",
        "--rate",
        type=float,
        default=_get_env_rate(),
        help=f"Polling rate in seconds (default: 0.1 or {ENV_RATE} env var)",
    )
    monitor_parser.add_argument(
        "-d",
        "--duration",
        type=float,
        default=_get_env_duration(),
        help=f"Monitoring duration in seconds (default: run until interrupted or {ENV_DURATION} env var)",
    )
    monitor_parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        default=_get_env_verbose(),
        help=f"Enable verbose output (can also be set via {ENV_VERBOSE} env var: 1, true, yes, on)",
    )
    monitor_parser.add_argument(
        "--format",
        choices=["chrome", "stdout", "jsonl"],
        default=_get_env_format(),
        help=f"Output format: 'chrome' for Chrome DevTools, 'stdout' for one-line-per-event JSONL to stdout, 'jsonl' for JSONL file (default: chrome or {ENV_FORMAT} env var)",
    )
    monitor_parser.add_argument(
        "--thread-name",
        type=str,
        default=_get_env_thread_name(),
        help=f"Thread name for trace events (default: 'GC Monitor' or {ENV_THREAD_NAME} env var)",
    )
    monitor_parser.add_argument(
        "--thread-id",
        type=int,
        default=_get_env_thread_id(),
        help=f"Thread ID for JSONL trace events (default: 0 or {ENV_THREAD_ID} env var)",
    )
    monitor_parser.add_argument(
        "--fallback",
        choices=["yes", "no"],
        default=_get_env_fallback(),
        help=f"Use mock implementation if _gc_monitor module not available (default: yes or {ENV_FALLBACK} env var)",
    )
    monitor_parser.add_argument(
        "--flush-threshold",
        type=int,
        default=_get_env_flush_threshold(),
        help=f"Number of events to buffer before flushing to file for JSONL format (default: 100 or {ENV_FLUSH_THRESHOLD} env var)",
    )

    # Combine command
    combine_parser = subparsers.add_parser(
        "combine",
        help="Combine multiple Chrome Trace Format files into one",
        description="Combine multiple Chrome Trace Format files into a single file without normalization or metadata processing.",
    )
    combine_parser.add_argument(
        "inputs",
        nargs="+",
        type=Path,
        help="Input Chrome Trace Format files to combine",
    )
    combine_parser.add_argument(
        "-o",
        "--output",
        type=Path,
        required=True,
        help="Output file path for the combined trace",
    )
    combine_parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose output",
    )
    combine_parser.add_argument(
        "-n",
        "--normalize",
        action="store_true",
        help="Normalize timestamps for each input file independently (each file starts at timestamp 0)",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    """Main entry point for the CLI.

    Args:
        argv: Command-line arguments (defaults to sys.argv[1:])

    Returns:
        Exit code (0 for success, non-zero for failure)
    """
    parser = _create_parser()
    args = parser.parse_args(argv)

    # Setup logging before any logging calls
    _setup_logging(args.verbose)

    # Handle subcommands
    if args.command == "combine":
        return _cmd_combine(args)

    # Default to monitor command if no command specified
    if args.command is None or args.command == "monitor":
        return _cmd_monitor(args)

    # Unknown command (should not happen due to argparse)
    logger.error("Unknown command: %s", args.command)
    return 1


def _cmd_monitor(args: argparse.Namespace) -> int:
    """Execute the monitor command.

    Args:
        args: Parsed command-line arguments for monitor command

    Returns:
        Exit code (0 for success, non-zero for failure)
    """
    pid = args.pid
    output_path = args.output
    rate = args.rate
    duration = args.duration
    verbose = args.verbose
    output_format = args.format
    thread_name = args.thread_name
    thread_id = args.thread_id
    fallback = args.fallback
    flush_threshold = args.flush_threshold

    if verbose:
        logger.info("Monitoring PID %s", pid)
        if output_format != "stdout":
            logger.info("Output: %s", output_path)
        logger.info("Format: %s", output_format)
        logger.info("Rate: %ss", rate)
        if duration:
            logger.info("Duration: %ss", duration)
        else:
            logger.info("Duration: until interrupted (Ctrl+C)")
        logger.info("Fallback: %s", fallback)

    # Create appropriate exporter based on format
    exporter: GCMonitorExporter
    if output_format == "stdout":
        exporter = StdoutExporter(pid=pid)
    elif output_format == "jsonl":
        exporter = JsonlExporter(
            pid=pid, output_path=output_path, thread_id=thread_id, flush_threshold=flush_threshold
        )
    else:
        exporter = TraceExporter(pid=pid, output_path=output_path, thread_name=thread_name)

    use_fallback = fallback == "yes"
    try:
        monitor = connect(pid, exporter=exporter, rate=rate, use_fallback=use_fallback)
    except RuntimeError as e:
        logger.error("Error: %s", e)
        return 1

    # Handle graceful shutdown on SIGINT/SIGTERM
    shutdown_event = threading.Event()
    shutdown_requested = False

    def _signal_handler(signum: int, frame: object) -> None:
        nonlocal shutdown_requested
        shutdown_requested = True
        shutdown_event.set()

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    try:
        if duration:
            # Run for specified duration or until target process ends
            if verbose:
                logger.info("Monitoring for %s seconds...", duration)
            start_time = time.monotonic()
            while not shutdown_event.is_set() and monitor.is_running:
                elapsed = time.monotonic() - start_time
                if elapsed >= duration:
                    shutdown_event.set()
                    break
                # Use wait() for responsive shutdown
                shutdown_event.wait(timeout=0.1)
        else:
            # Run until interrupted or target process ends
            if verbose:
                logger.info("Monitoring... (press Ctrl+C to stop)")
            while not shutdown_event.is_set() and monitor.is_running:
                # Use wait() for responsive shutdown
                shutdown_event.wait(timeout=0.1)
    finally:
        # Monitor may have already stopped if target process ended
        # stop() is safe to call multiple times
        monitor.stop()

    if shutdown_requested:
        logger.info("Shutdown was requested...")

    event_count = exporter.get_event_count()
    if verbose:
        logger.info("Monitoring complete.")
        logger.info("Total events: %s", event_count)
        if output_format != "stdout":
            logger.info("Trace saved to: %s", output_path)
    else:
        if output_format == "stdout":
            # Print summary to stderr for stdout format
            logger.info("Exported %s events to stdout", event_count)
        else:
            logger.info("Saved %s events to %s", event_count, output_path)

    return 0


def _cmd_combine(args: argparse.Namespace) -> int:
    """Execute the combine command.

    Args:
        args: Parsed command-line arguments for combine command

    Returns:
        Exit code (0 for success, non-zero for failure)
    """
    import json

    input_paths = args.inputs
    output_path = args.output
    verbose = args.verbose
    normalize = args.normalize

    if verbose:
        logger.info("Combining %s file(s)...", len(input_paths))
        for input_path in input_paths:
            logger.info("  Input: %s", input_path)
        logger.info("  Output: %s", output_path)
        if normalize:
            logger.info("  Normalizing timestamps: yes")

    try:
        combine_files(input_paths, output_path, normalize=normalize)
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as e:
        logger.error("Error combining files: %s", e)
        return 1

    if verbose:
        logger.info("Combine complete.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
