"""Command-line interface for gc-monitor."""

import argparse
import signal
import sys
import time
from pathlib import Path

from . import TraceExporter, connect


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
        help="Output file path (default: gc_trace.json)",
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

    if verbose:
        print(f"Monitoring PID {pid}")
        print(f"Output: {output_path}")
        print(f"Rate: {rate}s")
        if duration:
            print(f"Duration: {duration}s")
        else:
            print("Duration: until interrupted (Ctrl+C)")

    exporter = TraceExporter(pid=pid, output_path=output_path)
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
            # Run for specified duration
            if verbose:
                print(f"Monitoring for {duration} seconds...")
            time.sleep(duration)
        else:
            # Run until interrupted
            if verbose:
                print("Monitoring... (press Ctrl+C to stop)")
            while not shutdown_requested:
                time.sleep(0.1)
    finally:
        monitor.stop()

    event_count = exporter.get_event_count()
    if verbose:
        print(f"\nMonitoring complete.")
        print(f"Total events: {event_count}")
        print(f"Trace saved to: {output_path}")
    else:
        print(f"Saved {event_count} events to {output_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
