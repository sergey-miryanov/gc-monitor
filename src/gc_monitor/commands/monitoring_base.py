"""Shared monitoring logic for run and monitor commands."""

import logging
import os
from contextlib import ExitStack

from gc_monitor.commands.monitoring_options import MonitoringOptions
from gc_monitor.control.control_server import ControlServer
from gc_monitor.exporters import EventsExporterFactory
from gc_monitor.monitor import create_monitor
from gc_monitor.monitor_loop import MonitorLoop
from gc_monitor.run_policy import RunnerFactory
from gc_monitor.stats import StreamingStats
from gc_monitor.stats_output import print_stats
from gc_monitor.utils import replace_signals
from gc_monitor.wait_policy import WaitPolicy
from gc_monitor.target_process import ProcessRunnerFactory

logger = logging.getLogger("gc_monitor")


def run_monitoring_loop(
    factory: ProcessRunnerFactory,
    wait_policy: WaitPolicy,
    options: MonitoringOptions,
    address: str | None = None,
) -> int:

    try:
        logger.info("Self PID: %s", os.getpid())

        with ExitStack() as stack:
            exporter_factory = EventsExporterFactory(
                options.output_format, options.output_path, options.flush_threshold
            )
            exporter = exporter_factory()

            control_server = ControlServer(exporter, address=address)
            control_server.start()
            stack.enter_context(control_server)

            runner = factory(control_server.address)
            stack.enter_context(runner)

            process = runner.start()
            logger.info("Monitoring PID: %s", process.pid)

            stats = StreamingStats()
            monitor = create_monitor(process, exporter, stats)

            stack.enter_context(monitor)

            run_policy = RunnerFactory(options.duration)
            loop = MonitorLoop(monitor, run_policy, wait_policy, rate=options.rate, enabled=control_server.is_enabled)

            def _signal_handler(signum: int, frame: object) -> None:
                loop.close()

            stack.enter_context(replace_signals(_signal_handler))

            loop.run()
            returncode = runner.returncode or 0

        logger.info("Monitoring complete.")
        logger.info("Total events: %s", stats.count())
        if options.output_format != "stdout":
            logger.info("Trace saved to: %s", options.output_path)

        if options.show_stats:
            print_stats(stats, table_format=options.table_format)

        return returncode

    except Exception as e:
        logger.error("Failed to run GC monitor: %s", e, exc_info=True)
        return 1
