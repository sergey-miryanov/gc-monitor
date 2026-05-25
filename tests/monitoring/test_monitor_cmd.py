"""Tests for the gc-monitor monitor command."""

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from monitoring.conftest import MonitorArgsFactory
from tests.helpers import assert_valid_chrome_trace_format


# =============================================================================
# Unit Tests for cmd_monitor
# =============================================================================


def test_cmd_monitor_connect_failure(caplog: pytest.LogCaptureFixture, monitor_args: MonitorArgsFactory) -> None:
    from gc_monitor.commands import monitor_cmd

    with patch("gc_monitor.commands.monitor_cmd.run_monitoring_loop", return_value=1):
        result = monitor_cmd.cmd_monitor(monitor_args())
        assert result == 1


class TestCmdMonitorFormat:
    @pytest.mark.parametrize("fmt, extra_kwargs", [
        ("stdout", {}),
        ("jsonl", {"thread_id": 99, "flush_threshold": 50}),
    ])
    def test_cmd_monitor_format(
        self, caplog: pytest.LogCaptureFixture, monitor_args: MonitorArgsFactory, fmt: str, extra_kwargs: dict
    ) -> None:
        from gc_monitor.commands import monitor_cmd

        args = monitor_args(format=fmt, output=Path("test.jsonl"), duration=0.05, **extra_kwargs)
        with patch("gc_monitor.commands.monitor_cmd.run_monitoring_loop", return_value=0):
            result = monitor_cmd.cmd_monitor(args)
        assert result == 0
        assert f"Format: {fmt}" in caplog.text


class TestCmdMonitorValidation:
    @pytest.mark.parametrize("override, expected_msg", [
        ({"pid": -2}, "PID must be positive"),
        ({"rate": 0}, "Rate must be positive"),
        ({"duration": 0}, "Duration must be positive"),
        ({"flush_threshold": 0}, "Flush threshold must be positive"),
    ])
    def test_invalid_params(self, caplog: pytest.LogCaptureFixture, monitor_args: MonitorArgsFactory, override: dict, expected_msg: str) -> None:
        from gc_monitor.commands import monitor_cmd

        result = monitor_cmd.cmd_monitor(monitor_args(**override))
        assert result == 1
        assert expected_msg in caplog.text


def test_cmd_monitor_quiet_mode(monitor_args: MonitorArgsFactory) -> None:
    from gc_monitor.commands import monitor_cmd

    with patch("gc_monitor.commands.monitor_cmd.run_monitoring_loop", return_value=0):
        assert monitor_cmd.cmd_monitor(monitor_args(verbose=0, duration=0.05)) == 0


def test_cmd_monitor_self_pid(monitor_args: MonitorArgsFactory) -> None:
    from gc_monitor.commands import monitor_cmd

    with patch("gc_monitor.commands.monitor_cmd.run_monitoring_loop", return_value=0) as mock_loop:
        result = monitor_cmd.cmd_monitor(monitor_args(pid=-1, duration=0.05))
    assert result == 0
    assert mock_loop.call_args[0][0].pid == os.getpid()


# =============================================================================
# Subprocess Tests - Basic Execution
# =============================================================================


def test_cli_monitor_invocation(run_monitor: Any) -> None:
    assert run_monitor(["-d", "0.1"], timeout=5).returncode == 0


class TestCliBasicRun:
    def test_short_duration(self, run_monitor_self: Any, tmp_path: Path) -> None:
        result = run_monitor_self(["-o", str(tmp_path / "test.json"), "-d", "0.01", "-v"], timeout=15)
        assert result.returncode == 0
        assert "Duration: 0.01s" in result.stderr

    def test_creates_valid_trace(self, run_monitor_self: Any, tmp_path: Path) -> None:
        output_file = tmp_path / "test_trace.json"
        result = run_monitor_self(["-o", str(output_file), "-d", "0.5", "-r", "0.1"])
        assert result.returncode == 0
        assert output_file.exists()
        assert len(assert_valid_chrome_trace_format(output_file)) >= 1

    def test_default_output_file(self, run_monitor_self: Any, tmp_path: Path) -> None:
        assert run_monitor_self(["-d", "0.3"], cwd=tmp_path).returncode == 0
        assert (tmp_path / "gc_trace.json").exists()

    def test_custom_rate(self, run_monitor_self: Any, tmp_path: Path) -> None:
        result = run_monitor_self(["-o", str(tmp_path / "test_trace.json"), "-d", "0.5", "-r", "0.05", "-v"])
        assert result.returncode == 0
        assert "Rate: 0.05" in result.stderr

    def test_duration_based(self, run_monitor_self: Any, tmp_path: Path) -> None:
        import time
        start = time.monotonic()
        result = run_monitor_self(["-o", str(tmp_path / "test_trace.json"), "-d", "0.5", "-r", "0.1", "-v"])
        assert result.returncode == 0
        assert time.monotonic() - start >= 0.05
        assert "Duration: 0.5s" in result.stderr


class TestCliOutput:
    def test_verbose(self, run_monitor: Any, tmp_path: Path) -> None:
        output_file = tmp_path / "test_trace.json"
        result = run_monitor(["-o", str(output_file), "-d", "0.3", "-v"])
        assert result.returncode == 0
        assert "Monitoring PID: 12345" in result.stderr
        assert str(output_file) in result.stderr

    def test_quiet(self, run_monitor: Any, tmp_path: Path) -> None:
        result = run_monitor(["-o", str(tmp_path / "test_trace.json"), "-d", "0.3"])
        assert "Monitoring PID" not in result.stderr

    def test_json_structure(self, run_monitor: Any, tmp_path: Path) -> None:
        output_file = tmp_path / "test_trace.json"
        assert run_monitor(["-o", str(output_file), "-d", "0.3"]).returncode == 0
        with open(output_file) as f:
            data: list[dict[str, Any]] = json.load(f)
        assert len([e for e in data if e.get("ph") == "M"]) >= 1

    def test_path_traversal_warning(self, run_monitor: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        output_file = tmp_path / "subdir" / "output.json"
        output_file.parent.mkdir()
        output_file.touch()
        other = tmp_path / "other"
        other.mkdir()
        monkeypatch.chdir(other)
        result = run_monitor(["-o", str(output_file), "-d", "0.1", "-v"], timeout=5)
        assert "outside" in result.stderr or result.returncode == 0


class TestCliStdoutFormat:
    def test_jsonl_output(self, run_monitor: Any, tmp_path: Path) -> None:
        result = run_monitor(["--format", "stdout", "-d", "0.3"], cwd=tmp_path)
        assert result.returncode == 0
        for line in result.stdout.strip().split("\n"):
            line = line.strip()
            if line.startswith("{"):
                data: dict[str, Any] = json.loads(line)
                assert "pid" in data

    def test_verbose(self, run_monitor: Any, tmp_path: Path) -> None:
        result = run_monitor(["--format", "stdout", "-d", "0.3", "-v"], cwd=tmp_path)
        assert "Monitoring PID: 12345" in result.stderr
        assert "Format: stdout" in result.stderr

    def test_quiet(self, run_monitor: Any, tmp_path: Path) -> None:
        result = run_monitor(["--format", "stdout", "-d", "0.5"], cwd=tmp_path)
        assert "Monitoring PID" not in result.stderr


class TestCliJsonlFormat:
    def test_basic(self, run_monitor: Any, tmp_path: Path) -> None:
        output_file = tmp_path / "test.jsonl"
        result = run_monitor(["--format", "jsonl", "-o", str(output_file), "-d", "0.1", "-v"])
        assert "Format: jsonl" in result.stderr

    def test_cli_overrides_env(self, run_monitor: Any, tmp_path: Path) -> None:
        output_file = tmp_path / "test.json"
        env = os.environ.copy()
        env["GC_MONITOR_FORMAT"] = "jsonl"
        result = run_monitor(["--format", "chrome", "-o", str(output_file), "-d", "0.1"], env=env)
        assert output_file.exists()
        assert output_file.read_text().strip().startswith("[")


# =============================================================================
# Environment Variable Tests
# =============================================================================


class TestCliEnvVars:
    """CLI integration with individual environment variables."""

    def test_output(self, monkeypatch: pytest.MonkeyPatch, run_monitor: Any, tmp_path: Path) -> None:
        output_file = tmp_path / "env_test_trace.json"
        monkeypatch.setenv("GC_MONITOR_OUTPUT", str(output_file))
        assert run_monitor(["-d", "0.3"]).returncode == 0
        assert output_file.exists()

    def test_output_cli_override(self, monkeypatch: pytest.MonkeyPatch, run_monitor: Any, tmp_path: Path) -> None:
        monkeypatch.setenv("GC_MONITOR_OUTPUT", str(tmp_path / "env_trace.json"))
        cli_file = tmp_path / "cli_trace.json"
        assert run_monitor(["-o", str(cli_file), "-d", "0.3"]).returncode == 0
        assert cli_file.exists()
        assert not (tmp_path / "env_trace.json").exists()

    def test_rate(self, monkeypatch: pytest.MonkeyPatch, run_monitor: Any, tmp_path: Path) -> None:
        monkeypatch.setenv("GC_MONITOR_RATE", "0.05")
        result = run_monitor(["-o", str(tmp_path / "test_trace.json"), "-d", "0.3", "-v"])
        assert "Rate: 0.05" in result.stderr

    def test_rate_cli_override(self, monkeypatch: pytest.MonkeyPatch, run_monitor: Any, tmp_path: Path) -> None:
        monkeypatch.setenv("GC_MONITOR_RATE", "0.05")
        result = run_monitor(["-o", str(tmp_path / "test_trace.json"), "-r", "0.2", "-d", "0.3", "-v"])
        assert "Rate: 0.2" in result.stderr

    def test_duration(self, monkeypatch: pytest.MonkeyPatch, run_monitor: Any, tmp_path: Path) -> None:
        monkeypatch.setenv("GC_MONITOR_DURATION", "0.5")
        result = run_monitor(["-o", str(tmp_path / "test_trace.json"), "-v"])
        assert "Duration: 0.5" in result.stderr

    def test_duration_cli_override(self, monkeypatch: pytest.MonkeyPatch, run_monitor: Any, tmp_path: Path) -> None:
        monkeypatch.setenv("GC_MONITOR_DURATION", "0.5")
        result = run_monitor(["-o", str(tmp_path / "test_trace.json"), "-d", "0.3", "-v"])
        assert "Duration: 0.3" in result.stderr

    def test_format(self, monkeypatch: pytest.MonkeyPatch, run_monitor: Any, tmp_path: Path) -> None:
        monkeypatch.setenv("GC_MONITOR_FORMAT", "stdout")
        result = run_monitor(["-d", "0.3", "-v"])
        assert "Format: stdout" in result.stderr

    def test_format_cli_override(self, monkeypatch: pytest.MonkeyPatch, run_monitor: Any, tmp_path: Path) -> None:
        monkeypatch.setenv("GC_MONITOR_FORMAT", "stdout")
        result = run_monitor(["--format", "chrome", "-d", "0.3", "-v"])
        assert "Format: chrome" in result.stderr

    @pytest.mark.parametrize("value", ["1", "true", "yes", "on"])
    def test_verbose_truthy_values(self, monkeypatch: pytest.MonkeyPatch, run_monitor: Any, value: str) -> None:
        monkeypatch.setenv("GC_MONITOR_VERBOSE", value)
        result = run_monitor(["-d", "0.3"])
        assert "Monitoring PID: 12345" in result.stderr

    def test_verbose_cli_override(self, monkeypatch: pytest.MonkeyPatch, run_monitor: Any) -> None:
        monkeypatch.setenv("GC_MONITOR_VERBOSE", "0")
        result = run_monitor(["-d", "0.3", "-v"])
        assert "Monitoring PID: 12345" in result.stderr

    def test_multiple_vars(self, monkeypatch: pytest.MonkeyPatch, run_monitor: Any, tmp_path: Path) -> None:
        output_file = tmp_path / "multi_env_test.json"
        monkeypatch.setenv("GC_MONITOR_OUTPUT", str(output_file))
        monkeypatch.setenv("GC_MONITOR_RATE", "0.05")
        monkeypatch.setenv("GC_MONITOR_DURATION", "0.4")
        monkeypatch.setenv("GC_MONITOR_VERBOSE", "1")
        monkeypatch.setenv("GC_MONITOR_FORMAT", "chrome")
        result = run_monitor([])
        assert output_file.exists()
        assert "Rate: 0.05" in result.stderr
        assert "Duration: 0.4" in result.stderr

    def test_flush_threshold(self, monkeypatch: pytest.MonkeyPatch, run_monitor: Any, tmp_path: Path) -> None:
        output_file = tmp_path / "test.jsonl"
        monkeypatch.setenv("GC_MONITOR_FLUSH_THRESHOLD", "50")
        assert run_monitor(["--format", "jsonl", "-o", str(output_file), "-d", "0.1", "-v"]).returncode == 0

    def test_flush_threshold_cli_override(self, monkeypatch: pytest.MonkeyPatch, run_monitor: Any, tmp_path: Path) -> None:
        output_file = tmp_path / "test.jsonl"
        monkeypatch.setenv("GC_MONITOR_FLUSH_THRESHOLD", "50")
        assert run_monitor(["--format", "jsonl", "-o", str(output_file), "--flush-threshold", "200", "-d", "0.1"]).returncode == 0

    def test_env_output_default_format_jsonl(self, monkeypatch: pytest.MonkeyPatch, run_monitor: Any, tmp_path: Path) -> None:
        monkeypatch.setenv("GC_MONITOR_FORMAT", "jsonl")
        assert run_monitor(["-d", "0.1"], cwd=tmp_path).returncode == 0


class TestCliEnvHelp:
    def test_monitor_help_shows_env_vars(self, gc_monitor_cmd: list[str]) -> None:
        result = subprocess.run(
            gc_monitor_cmd + ["monitor", "--help"],
            capture_output=True, text=True, check=True,
        )
        for var in ("GC_MONITOR_OUTPUT", "GC_MONITOR_RATE", "GC_MONITOR_DURATION", "GC_MONITOR_VERBOSE", "GC_MONITOR_FORMAT"):
            assert var in result.stdout
