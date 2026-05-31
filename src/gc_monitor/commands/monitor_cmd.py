"""Monitor command implementation."""

import argparse
import logging
import os
from argparse import Namespace

from gc_monitor.commands.monitoring_base import run_monitoring_loop
from gc_monitor.commands.monitoring_options import add_monitoring_options, get_monitoring_options
from gc_monitor.commands.parser_factory import ParserFactory
from gc_monitor.control.control_server import ControlServer
from gc_monitor.target_process import ExternalProcess
from gc_monitor.wait_policy import NoWaitPolicy

logger = logging.getLogger("gc_monitor")


def add_parser(parser_factory: ParserFactory) -> argparse.ArgumentParser:
    """Add the 'monitor' subparser and return it."""
    parser = parser_factory(
        "monitor",
        help="Monitor a process's garbage collection",
        description="Monitor Python's garbage collector and export statistics.",
    )
    parser.add_argument(
        "pid",
        type=int,
        help="Process ID to monitor",
    )
    add_monitoring_options(parser)
    parser.set_defaults(func=cmd_monitor)
    return parser


def cmd_monitor(args: Namespace) -> int:
    """Execute the monitor command."""
    pid = args.pid

    if pid < -1:
        logger.error("PID must be positive or -1, got %s", pid)
        return 1

    if pid == -1:
        pid = os.getpid()

    options = get_monitoring_options(args, duration_label="until interrupted (Ctrl+C)")
    if options is None:
        return 1

    process = ExternalProcess(pid)
    wait_policy = NoWaitPolicy()
    control = ControlServer(address=args.control_name)
    return run_monitoring_loop(process, wait_policy, options, control_server=control)
