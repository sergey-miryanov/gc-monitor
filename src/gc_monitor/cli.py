"""Command-line interface for gc-monitor."""

import argparse
import logging
import signal
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING

from . import TraceExporter, connect
from .stdout_exporter import StdoutExporter

if TYPE_CHECKING:
    from .exporter import GCMonitorExporter

logger = logging.getLogger("gc_monitor")


def _setup_logging(verbose: bool) -> None:
    """Configure logging for the CLI.
    
    Args:
        verbose: If True, set log level to INFO; otherwise WARNING.
    """
    level = logging.INFO if verbose else logging.WARNING
    logging.basicConfig(
        level=level,
        format="%(levelname)s: %(message)s",
    )


def _create_parser() -> argparse.ArgumentParser:
    """Create the argument parser."""
    parser = argparse.ArgumentParser(
        prog="gc-monitor",
        description="Monitor Python's garbage collector and export statistics.",
    )
    parser.add_argument(
        "pid",
        type=int,
        help="Process ID to monitor",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path("gc_trace.json"),
        help="Output file path (default: gc_trace.json). Ignored for --format stdout",
    )
    parser.add_argument(
        "-r",
        "--rate",
        type=float,
        default=0.1,
        help="Polling rate in seconds (default: 0.1)",
    )
    parser.add_argument(
        "-d",
        "--duration",
        type=float,
        default=None,
        help="Monitoring duration in seconds (default: run until interrupted)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose output",
    )
    parser.add_argument(
        "--format",
        choices=["chrome", "stdout"],
        default="chrome",
        help="Output format: 'chrome' for Chrome DevTools, 'stdout' for one-line-per-event JSONL (default: chrome)",
    )
    parser.add_argument(
        "--thread-name",
        type=str,
        default="GC Monitor",
        help="Thread name for trace events (default: 'GC Monitor')",
    )
    parser.add_argument(
        "--fallback",
        choices=["yes", "no"],
        default="yes",
        help="Use mock implementation if _gc_monitor module not available (default: yes)",
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

    pid = args.pid
    output_path = args.output
    rate = args.rate
    duration = args.duration
    verbose = args.verbose
    output_format = args.format
    thread_name = args.thread_name
    fallback = args.fallback

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
    else:
        exporter = TraceExporter(pid=pid, output_path=output_path, thread_name=thread_name)

    use_fallback = fallback == "yes"
    try:
        monitor = connect(pid, exporter=exporter, rate=rate, use_fallback=use_fallback)
    except RuntimeError as e:
        logger.error("Error: %s", e)
        return 1

    # Handle graceful shutdown on SIGINT/SIGTERM
    shutdown_requested = False

    def _signal_handler(signum: int, frame: object) -> None:
        nonlocal shutdown_requested
        shutdown_requested = True

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    try:
        if duration:
            # Run for specified duration or until target process ends
            if verbose:
                logger.info("Monitoring for %s seconds...", duration)
            start_time = time.monotonic()
            while not shutdown_requested and monitor.is_running:
                elapsed = time.monotonic() - start_time
                if elapsed >= duration:
                    break
                time.sleep(0.1)
        else:
            # Run until interrupted or target process ends
            if verbose:
                logger.info("Monitoring... (press Ctrl+C to stop)")
            while not shutdown_requested and monitor.is_running:
                time.sleep(0.1)
    finally:
        # Monitor may have already stopped if target process ended
        # stop() is safe to call multiple times
        monitor.stop()

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


if __name__ == "__main__":
    sys.exit(main())
