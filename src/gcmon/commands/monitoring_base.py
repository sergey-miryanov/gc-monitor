"""Shared monitoring logic for run and monitor commands."""

import logging
import os
from contextlib import ExitStack

from gcmon.commands.monitoring_options import MonitoringOptions
from gcmon.control.control_server import ControlServer
from gcmon.exporters import EventsExporterFactory
from gcmon.monitor import EventsMonitor
from gcmon.monitor_loop import MonitorLoop
from gcmon.rss_sampler import RssSampler
from gcmon.run_policy import RunnerFactory
from gcmon.stats import StreamingStats
from gcmon.stats_output import print_stats, summary_lines
from gcmon.target_process import ProcessRunnerFactory
from gcmon.utils import replace_signals
from gcmon.wait_policy import WaitPolicyFactory

logger = logging.getLogger("gcmon")


def run_monitoring_loop(
    factory: ProcessRunnerFactory,
    wait_policy_factory: WaitPolicyFactory,
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
            monitor = EventsMonitor(
                process,
                exporter,
                stats,
                wait_policy_factory=wait_policy_factory,
                is_pid_enabled=control_server.is_enabled,
            )

            stack.enter_context(monitor)

            run_policy = RunnerFactory(options.duration)

            rss_sampler: RssSampler | None = None
            if options.rss_enabled:
                rss_sampler = RssSampler(exporter, interval=options.rss_interval)

            loop = MonitorLoop(
                monitor,
                run_policy,
                rate=options.rate,
                rss_sampler=rss_sampler,
            )

            def _signal_handler(signum: int, frame: object) -> None:
                loop.close()

            stack.enter_context(replace_signals(_signal_handler))

            loop.run()

            # Wait for the subprocess to fully exit before reading return code.
            # The monitoring loop may break before the process has fully terminated
            # (e.g. GC state becomes NULL during interpreter shutdown while the
            # process is still cleaning up after sys.exit()).
            runner.wait(timeout=2.0)
            returncode = runner.returncode or 0

        trace_path = None if options.output_format == "stdout" else options.output_path
        for line in summary_lines(stats, trace_path, show_stats=options.show_stats):
            logger.info("%s", line)

        if options.show_stats:
            print_stats(stats, table_format=options.table_format)

        return returncode

    except Exception as e:
        logger.error("Failed to run GC monitor: %s", e, exc_info=True)
        return 1
