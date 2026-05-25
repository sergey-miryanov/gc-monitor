"""Shared monitoring logic for run and monitor commands."""

import logging
import os
from collections.abc import Callable

from gc_monitor.commands.monitoring_options import MonitoringOptions
from gc_monitor.exporters import EventsExporterFactory
from gc_monitor.lock_strategy import NoLock
from gc_monitor.monitor import create_monitor
from gc_monitor.monitor_loop import MonitorLoop
from gc_monitor.run_policy import RunnerFactory
from gc_monitor.stats import StreamingStats
from gc_monitor.stats_output import print_stats
from gc_monitor.target_process import TargetProcess
from gc_monitor.utils import replace_signals
from gc_monitor.wait_policy import WaitPolicy

logger = logging.getLogger("gc_monitor")


def run_monitoring_loop(
    process: TargetProcess,
    wait_policy: WaitPolicy,
    options: MonitoringOptions,
    cleanup: Callable[[], None] | None = None,
    enabled: Callable[[int], bool] | None = None,
) -> int:
    """Run monitoring loop.

    Returns:
        Exit code (0 on success)
    """
    try:
        logger.info("Self PID: %s", os.getpid())
        logger.info("Monitoring PID: %s", process.pid)

        run_policy = RunnerFactory(options.duration)
        exporter_factory = EventsExporterFactory(
            NoLock, options.output_format, options.output_path, options.flush_threshold
        )
        stats = StreamingStats()
        monitor = create_monitor(process, exporter_factory, stats)
        loop = MonitorLoop(monitor, run_policy, wait_policy, rate=options.rate, enabled=enabled)

        def _signal_handler(signum: int, frame: object) -> None:
            loop.close()

        with replace_signals(_signal_handler):
            loop.run()

        if cleanup is not None:
            cleanup()

        logger.info("Monitoring complete.")
        logger.info("Total events: %s", stats.count())
        if options.output_format != "stdout":
            logger.info("Trace saved to: %s", options.output_path)

        if options.show_stats:
            print_stats(stats, table_format=options.table_format)

        return 0

    except Exception as e:
        logger.error("Failed to run GC monitor: %s", e, exc_info=True)
        return 1
