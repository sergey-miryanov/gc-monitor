"""Command-line interface for gc-monitor."""

import argparse
import signal
import sys
import time
from pathlib import Path

from . import TraceExporter, connect
from .stdout_exporter import StdoutExporter


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

    pid = args.pid
    output_path = args.output
    rate = args.rate
    duration = args.duration
    verbose = args.verbose
    output_format = args.format
    thread_name = args.thread_name

    if verbose:
        print(f"Monitoring PID {pid}")
        if output_format != "stdout":
            print(f"Output: {output_path}")
        print(f"Format: {output_format}")
        print(f"Rate: {rate}s")
        if duration:
            print(f"Duration: {duration}s")
        else:
            print("Duration: until interrupted (Ctrl+C)")

    # Create appropriate exporter based on format
    if output_format == "stdout":
        exporter = StdoutExporter(pid=pid)
    else:
        exporter = TraceExporter(pid=pid, output_path=output_path, thread_name=thread_name)

    monitor = connect(pid, exporter=exporter, rate=rate)

    if monitor is None:
        print(f"Failed to connect to GC monitor for PID {pid}", file=sys.stderr)
        return 1

    # Handle graceful shutdown on SIGINT/SIGTERM
    shutdown_requested = False

    def _signal_handler(signum: int, frame: object) -> None:
        nonlocal shutdown_requested
        shutdown_requested = True
        if verbose:
            print("\nShutdown requested...")

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    try:
        if duration:
            # Run for specified duration or until target process ends
            if verbose:
                print(f"Monitoring for {duration} seconds...")
            start_time = time.monotonic()
            while not shutdown_requested and monitor.is_running:
                elapsed = time.monotonic() - start_time
                if elapsed >= duration:
                    break
                time.sleep(0.1)
        else:
            # Run until interrupted or target process ends
            if verbose:
                print("Monitoring... (press Ctrl+C to stop)")
            while not shutdown_requested and monitor.is_running:
                time.sleep(0.1)
    finally:
        # Monitor may have already stopped if target process ended
        # stop() is safe to call multiple times
        monitor.stop()

    event_count = exporter.get_event_count()
    if verbose:
        print(f"\nMonitoring complete.")
        print(f"Total events: {event_count}")
        if output_format != "stdout":
            print(f"Trace saved to: {output_path}")
    else:
        if output_format == "stdout":
            # Print summary to stderr for stdout format
            print(f"Exported {event_count} events to stdout", file=sys.stderr)
        else:
            print(f"Saved {event_count} events to {output_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
