"""Command-line interface for gc-monitor."""

import argparse
import logging
import sys

from .commands import (
    add_combine_parser,
    add_monitor_parser,
    add_run_parser,
)

logger = logging.getLogger("gc_monitor")


def _create_parser() -> argparse.ArgumentParser:
    """Create the argument parser with subcommands."""
    parser = argparse.ArgumentParser(
        prog="gc-monitor",
        description="Monitor Python's garbage collector and export statistics.",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    add_monitor_parser(subparsers.add_parser)
    add_combine_parser(subparsers.add_parser)
    add_run_parser(subparsers.add_parser)

    return parser


def _setup_logging(verbose_count: int) -> None:
    """Configure logging for the CLI.

    Args:
        verbose_count: Verbose level count:
            0 = WARNING (default)
            1 = INFO (-v)
            2+ = DEBUG (-vv or more)
    """
    if verbose_count >= 2:
        level = logging.DEBUG
    elif verbose_count == 1:
        level = logging.INFO
    else:
        level = logging.WARNING
    logger = logging.getLogger("gc_monitor")
    logger.setLevel(level)

    # Only add handler if none exists
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setLevel(level)
        formatter = logging.Formatter("[%(name)s] %(levelname)s: %(message)s")
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    else:
        # Update existing handlers
        for handler in logger.handlers:  # type: ignore[assignment]
            handler.setLevel(level)


def _split_run_args(argv: list[str]) -> tuple[list[str], list[str]]:
    """Split run command args at the first target option (-m/-s/--module/--script).

    Everything up to and including the target option + value goes to gc-monitor.
    Everything after is passed verbatim to the script.

    Args:
        argv: Command-line arguments starting with "run"

    Returns:
        Tuple of (gc-monitor args, script args)
    """
    target_options = {"-m", "--module", "-s", "--script"}
    for i, arg in enumerate(argv):
        if arg in target_options:
            # -m value or -s value → split after the value
            return argv[: i + 2], argv[i + 2 :]
        if arg.startswith("--module=") or arg.startswith("--script="):
            # --module=value or --script=value → split after this arg
            return argv[: i + 1], argv[i + 1 :]
    # No target option found — all args go to gc-monitor
    return argv, []


def main(argv: list[str] | None = None) -> int:
    """Main entry point for the CLI.

    Args:
        argv: Command-line arguments (defaults to sys.argv[1:])

    Returns:
        Exit code (0 for success, non-zero for failure)
    """
    parser = _create_parser()

    # Check if "run" command is being used - need special handling for script args
    if argv is None:
        argv = sys.argv[1:]

    # For run command, split args at the first target option (-m/-s/--module/--script)
    # Everything before goes to gc-monitor, everything after goes to the script
    if argv and argv[0] == "run":
        gc_args, script_args = _split_run_args(argv)
        args = parser.parse_args(gc_args)
        args.script_args = script_args
    else:
        args = parser.parse_args(argv)

    # Setup logging before any logging calls
    _setup_logging(args.verbose)

    # Dispatch via args.func (set by each subparser's set_defaults)
    if hasattr(args, "func"):
        return int(args.func(args))

    # No command specified — default to monitor
    if args.command is None:
        return main(["monitor"], *argv)

    # Unknown command (should not happen due to argparse)
    logger.error("Unknown command: %s", args.command)
    return 1


if __name__ == "__main__":
    sys.exit(main())
