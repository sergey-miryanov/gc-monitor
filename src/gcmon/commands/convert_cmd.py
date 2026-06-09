"""Combine command implementation."""

import argparse
import json
import logging
from argparse import Namespace
from pathlib import Path

from gcmon.exporters.chrome_trace_io import combine_files

from .parser_factory import ParserFactory

logger = logging.getLogger("gcmon")


def add_parser(parser_factory: ParserFactory) -> argparse.ArgumentParser:
    """Add the 'combine' subparser and return it.

    Args:
        subparsers: Subparsers action from argparse.

    Returns:
        The created combine subparser.
    """
    parser = parser_factory(
        "combine",
        help="Combine multiple trace files into one",
        description="Combine multiple Chrome Trace Format or JSONL files into a single file with optional timestamp normalization.",
    )
    parser.add_argument(
        "inputs",
        nargs="+",
        type=Path,
        help="Input files to combine",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        required=True,
        help="Output file path for the combined trace",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="Enable verbose output (use -v for INFO, -vv for DEBUG)",
    )
    parser.add_argument(
        "-n",
        "--normalize",
        action="store_true",
        help="Normalize timestamps per PID so each process timeline starts at 0",
    )
    parser.add_argument(
        "--input-format",
        choices=["jsonl", "chrome"],
        default="chrome",
        help="Input file format (default: chrome)",
    )
    parser.add_argument(
        "--output-format",
        choices=["jsonl", "chrome"],
        default="chrome",
        help="Output file format (default: chrome)",
    )
    parser.set_defaults(func=cmd_combine)
    return parser


def cmd_combine(args: Namespace) -> int:
    """Execute the combine command.

    Args:
        args: Parsed command-line arguments for combine command

    Returns:
        Exit code (0 for success, non-zero for failure)
    """
    input_paths: list[Path] = args.inputs
    output_path: Path = args.output
    normalize = args.normalize
    input_format = args.input_format
    output_format = args.output_format

    if input_format == "chrome" and output_format == "jsonl":
        logger.error(
            "Input format 'chrome' with output format 'jsonl' is not supported. "
            "Use --output-format chrome instead."
        )
        return 1

    logger.info("Combining %s file(s)...", len(input_paths))
    for input_path in input_paths:
        logger.info("  Input: %s", input_path)
    logger.info("  Output: %s", output_path)
    logger.info("  Input format: %s", input_format)
    logger.info("  Output format: %s", output_format)
    if normalize:
        logger.info("  Normalizing timestamps: yes")

    try:
        combine_files(input_paths, output_path, normalize=normalize, input_format=input_format, output_format=output_format)
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as e:
        logger.error("Error combining files: %s", e)
        return 1

    logger.info("Combine complete.")

    return 0
