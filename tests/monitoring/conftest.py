"""Shared fixtures for monitoring command tests."""

from __future__ import annotations

import subprocess
import sys
from argparse import Namespace
from collections.abc import Callable
from contextlib import ExitStack
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from gcmon.commands.monitoring_options import MonitoringOptions
from gcmon.stats_output import TableFormat


class MonitorArgsFactory:
    """Factory for creating monitor command Namespace objects."""

    _defaults: dict = {
        "pid": 12345,
        "output": Path("test.json"),
        "rate": 0.1,
        "duration": 0.05,
        "verbose": 1,
        "format": "chrome",
        "thread_id": 0,
        "flush_threshold": 100,
        "stats": False,
        "table_format": None,
        "control_name": None,
    }

    def __call__(self, **overrides: object) -> Namespace:
        kwargs = {**self._defaults, **overrides}
        return Namespace(**kwargs)


@pytest.fixture
def monitor_args() -> MonitorArgsFactory:
    """Factory fixture for creating monitor command Namespace objects.

    Usage:
        args = monitor_args(pid=999, format="jsonl")
    """
    return MonitorArgsFactory()


@pytest.fixture
def mock_monitor() -> MagicMock:
    """Pre-configured MagicMock for create_monitor return value."""
    return MagicMock()


@pytest.fixture
def mock_exporter() -> MagicMock:
    """Pre-configured MagicMock for exporter with get_event_count."""
    exporter = MagicMock()
    exporter.get_event_count.return_value = 5
    return exporter


@pytest.fixture
def mock_thread() -> MagicMock:
    """Pre-configured MagicMock for monitor thread."""
    thread = MagicMock()
    thread.is_running = True
    return thread


@pytest.fixture
def gcmon_cmd() -> list[str]:
    return [sys.executable, "-m", "gcmon.cli"]


@pytest.fixture
def run_monitor(gcmon_cmd: list[str]) -> Callable[..., subprocess.CompletedProcess[str]]:
    """Run gcmon monitor subprocess with common defaults.

    Usage:
        result = run_monitor(["-d", "0.1"], timeout=5)
    """
    def _run(extra_args: list[str] | None = None, **kwargs: object) -> subprocess.CompletedProcess[str]:
        cmd = gcmon_cmd + ["monitor", "12345"] + (extra_args or [])
        return subprocess.run(cmd, capture_output=True, text=True, **kwargs)

    return _run


@pytest.fixture
def run_monitor_self(gcmon_cmd: list[str]) -> Callable[..., subprocess.CompletedProcess[str]]:
    """Run gcmon monitor subprocess with common defaults.

    Usage:
        result = run_monitor(["-d", "0.1"], timeout=5)
    """
    def _run(extra_args: list[str] | None = None, **kwargs: object) -> subprocess.CompletedProcess[str]:
        cmd = gcmon_cmd + ["monitor", "-1"] + (extra_args or [])
        return subprocess.run(cmd, capture_output=True, text=True, **kwargs)

    return _run


@pytest.fixture
def monitoring_options() -> Callable[..., MonitoringOptions]:
    """Factory fixture for MonitoringOptions objects.

    Usage:
        opts = monitoring_options(output_format="stdout")
    """
    def _make(**overrides: Any) -> MonitoringOptions:
        defaults: dict[str, Any] = {
            "output_path": Path("test.json"),
            "rate": 0.1,
            "duration": 0.05,
            "output_format": "chrome",
            "flush_threshold": 100,
            "duration_label": "until interrupted",
            "show_stats": False,
            "table_format": TableFormat.PLAIN,
        }
        return MonitoringOptions(**{**defaults, **overrides})
    return _make


@pytest.fixture
def mock_monitoring_base_deps() -> dict[str, Any]:
    """Patch all dependencies for run_monitoring_loop.

    Returns dict of mocks that tests can customize (e.g. deps["StreamingStats"].return_value.count.return_value = 0).
    """
    patch_targets = [
        "ControlServer",
        "RunnerFactory",
        "EventsExporterFactory",
        "StreamingStats",
        "create_monitor",
        "MonitorLoop",
        "replace_signals",
        "print_stats",
    ]
    with ExitStack() as stack:
        deps: dict[str, Any] = {}
        for name in patch_targets:
            deps[name] = stack.enter_context(patch(f"gcmon.commands.monitoring_base.{name}"))
        mock_control_instance = MagicMock()
        mock_control_instance.address = "/tmp/test-address"
        deps["ControlServer"].return_value = mock_control_instance
        deps["StreamingStats"].return_value.count.return_value = 5
        deps["MonitorLoop"].return_value = MagicMock()
        deps["RunnerFactory"].return_value = MagicMock()
        yield deps
