"""Integration tests for the run command."""

import json
import subprocess
import sys
from argparse import Namespace
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from gc_monitor.exporters.chrome_trace_io import read_jsonl

from gc_monitor.control.control_server import ControlServer
from tests.monitoring.conftest import MonitorArgsFactory
from tests.helpers import assert_valid_chrome_trace_format


# =============================================================================
# Unit Tests for cmd_run
# =============================================================================


def assert_jsonl_format(output_file: Path) -> None:
    assert output_file.exists()
    assert read_jsonl(output_file)


def assert_stdout_format(output: str) -> None:
    assert "pid" in output
    assert "gen" in output
    assert "ts_start" in output
    assert "ts_stop" in output
    assert "heap_size" in output
    assert "collections" in output
    assert "collected" in output
    assert "uncollectable" in output
    assert "candidates" in output
    assert "duration" in output


def get_long_running_script(*args: list[Any]) -> str:
    script: str = """
import gc
import sys
import time
gc.collect()
n = 1000
d = {}
t1 = time.monotonic()
for i in range(n):
    for j in range(n):
        d[(i, j)] = i * n + j
t2 = time.monotonic()
gc.collect()
print('')
print(f'ts={(t2-t1)/1_000.0}')
sys.stdout.flush()

"""
    return script + "\n".join([str(s) for s in args]) + "\nsys.stdout.flush()\nsys.exit(0)"

def run_script(script_file: Path, *script_args: str, gc_args: list[str] | None = None) -> subprocess.CompletedProcess[str]:
    gc_opts = gc_args or []
    proc = subprocess.run(
        [
            sys.executable,
            "-u",
            "-m",
            "gc_monitor",
            "run",
            *gc_opts,
            "-s",
            str(script_file.as_posix()),
        ] + list(script_args),
        capture_output=True,
        text=True,
        timeout=30,
    )
    return proc

def run_module(module_name: str, *script_args: str, gc_args: list[str] | None = None) -> subprocess.CompletedProcess[str]:
    gc_opts = gc_args or []
    proc = subprocess.run(
        [
            sys.executable,
            "-u",
            "-m",
            "gc_monitor",
            "run",
            *gc_opts,
            "-m",
            str(module_name),
        ] + list(script_args),
        # capture_output=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=30,
    )
    return proc



class TestCmdRunUnit:
    """Unit tests for cmd_run function."""

    def _make_run_args(self, **overrides: Any) -> Namespace:
        defaults = {
            "module_name": None,
            "script": None,
            "script_args": [],
            "output": Path("test.json"),
            "rate": 0.1,
            "duration": 0.05,
            "verbose": 1,
            "format": "chrome",
            "flush_threshold": 100,
            "stats": False,
            "table_format": None,
        }
        return Namespace(**{**defaults, **overrides})

    def test_cmd_run_both_targets(self, caplog: pytest.LogCaptureFixture) -> None:
        """Test cmd_run rejects both -m and -s specified."""
        from gc_monitor.commands import run_cmd

        args = self._make_run_args(module_name="timeit", script="script.py")

        result = run_cmd.cmd_run(args)

        assert result == 1
        assert "Cannot specify both" in caplog.text

    def test_cmd_run_no_target(self, caplog: pytest.LogCaptureFixture) -> None:
        """Test cmd_run rejects neither -m nor -s specified."""
        from gc_monitor.commands import run_cmd

        args = self._make_run_args()

        result = run_cmd.cmd_run(args)

        assert result == 1
        assert "Must specify either" in caplog.text

    def test_cmd_run_module_mode(self) -> None:
        """Test cmd_run creates ChildProcessRunner with is_module=True."""
        from gc_monitor.commands import run_cmd

        args = self._make_run_args(module_name="timeit", script_args=["-n", "1"])

        with patch("gc_monitor.commands.run_cmd.run_monitoring_loop", return_value=0):
            with patch("gc_monitor.commands.run_cmd.ChildProcessRunner") as mock_runner_cls:
                mock_runner = MagicMock()
                mock_runner.returncode = 0
                mock_runner.__enter__ = MagicMock(return_value=mock_runner)
                mock_runner.__exit__ = MagicMock(return_value=False)
                mock_runner_cls.return_value = mock_runner
                mock_runner.start.return_value = MagicMock(pid=999)

                run_cmd.cmd_run(args)

                mock_runner_cls.assert_called_once_with(
                    target="timeit",
                    is_module=True,
                    passthrough_args=["-n", "1"],
                    control=mock_runner_cls.call_args[1]["control"],
                )
                assert isinstance(mock_runner_cls.call_args[1]["control"], ControlServer)

    def test_cmd_run_script_mode(self) -> None:
        """Test cmd_run creates ChildProcessRunner with is_module=False."""
        from gc_monitor.commands import run_cmd

        args = self._make_run_args(script="myscript.py", script_args=["arg1"])

        with patch("gc_monitor.commands.run_cmd.run_monitoring_loop", return_value=0):
            with patch("gc_monitor.commands.run_cmd.ChildProcessRunner") as mock_runner_cls:
                mock_runner = MagicMock()
                mock_runner.returncode = 0
                mock_runner.__enter__ = MagicMock(return_value=mock_runner)
                mock_runner.__exit__ = MagicMock(return_value=False)
                mock_runner_cls.return_value = mock_runner
                mock_runner.start.return_value = MagicMock(pid=999)

                run_cmd.cmd_run(args)

                mock_runner_cls.assert_called_once_with(
                    target="myscript.py",
                    is_module=False,
                    passthrough_args=["arg1"],
                    control=mock_runner_cls.call_args[1]["control"],
                )
                assert isinstance(mock_runner_cls.call_args[1]["control"], ControlServer)

    def test_cmd_run_cleanup_called(self) -> None:
        """Test cleanup callback calls runner.terminate()."""
        from gc_monitor.commands import run_cmd

        args = self._make_run_args(module_name="timeit")

        def mock_loop(process, wait_policy, options, cleanup=None, **kwargs):
            if cleanup is not None:
                cleanup()
            return 0

        with patch("gc_monitor.commands.run_cmd.run_monitoring_loop", side_effect=mock_loop):
            with patch("gc_monitor.commands.run_cmd.ChildProcessRunner") as mock_runner_cls:
                mock_runner = MagicMock()
                mock_runner.returncode = 0
                mock_runner.__enter__ = MagicMock(return_value=mock_runner)
                mock_runner.__exit__ = MagicMock(return_value=False)
                mock_runner_cls.return_value = mock_runner
                mock_runner.start.return_value = MagicMock(pid=999)

                run_cmd.cmd_run(args)

                mock_runner.terminate.assert_called_once()

    def test_cmd_run_subprocess_returncode(self) -> None:
        """Test non-zero subprocess returncode is propagated."""
        from gc_monitor.commands import run_cmd

        args = self._make_run_args(module_name="timeit")

        with patch("gc_monitor.commands.run_cmd.run_monitoring_loop", return_value=0):
            with patch("gc_monitor.commands.run_cmd.ChildProcessRunner") as mock_runner_cls:
                mock_runner = MagicMock()
                mock_runner.returncode = 42
                mock_runner.__enter__ = MagicMock(return_value=mock_runner)
                mock_runner.__exit__ = MagicMock(return_value=False)
                mock_runner_cls.return_value = mock_runner
                mock_runner.start.return_value = MagicMock(pid=999)

                result = run_cmd.cmd_run(args)

                assert result == 42

    def test_cmd_run_validation_failure(self, caplog: pytest.LogCaptureFixture) -> None:
        """Test cmd_run returns 1 when get_monitoring_options fails."""
        from gc_monitor.commands import run_cmd

        args = self._make_run_args(module_name="timeit", rate=0)

        result = run_cmd.cmd_run(args)

        assert result == 1
        assert "Rate must be positive" in caplog.text


class TestRunCommandScriptMode:
    """Integration tests for run command in script mode."""

    def test_run_script_no_args(self, tmp_path: Path) -> None:
        """Test running a simple script with GC monitoring."""
        output_file = tmp_path / "trace.json"
        script_file = tmp_path / "test_script.py"
        script_file.write_text(get_long_running_script("print('Hello')", "sys.exit(42)"))

        result = run_script(script_file, gc_args=["-vvv", "-o", str(output_file)])

        assert result.returncode == 42
        assert "Hello" in result.stdout

        assert_valid_chrome_trace_format(output_file)

    def test_run_script_with_args_chrome_trace_format(self, tmp_path: Path) -> None:
        """Test running a script with arguments."""
        output_file = tmp_path / "trace.json"
        script_file = tmp_path / "test_script.py"
        script_file.write_text(get_long_running_script("print('Args: ', sys.argv[1:])"))

        gc_args = ["-vvv", "--format", "chrome", "-o", str(output_file)]
        result = run_script(script_file, "arg1", "arg2", "--flag", gc_args=gc_args)

        assert result.returncode == 0
        assert "Args:  ['arg1', 'arg2', '--flag']" in result.stdout

        assert_valid_chrome_trace_format(output_file)

    def test_run_script_with_args_jsonl_format(self, tmp_path: Path) -> None:
        """Test running a script with JSONL format."""
        output_file = tmp_path / "trace.jsonl"
        script_file = tmp_path / "test_script.py"
        script_file.write_text(get_long_running_script("print('Args: ', sys.argv[1:])"))

        gc_args = ["-vvv", "--format", "jsonl", "-o", str(output_file)]
        result = run_script(script_file, "arg1", "arg2", "--flag", gc_args=gc_args)

        assert result.returncode == 0
        assert "Args:  ['arg1', 'arg2', '--flag']" in result.stdout

        assert_jsonl_format(output_file)

    def test_run_script_with_args_stdout_format(self, tmp_path: Path) -> None:
        """Test running a script with stdout format."""
        script_file = tmp_path / "test_script.py"
        script_file.write_text(get_long_running_script("print('Args: ', sys.argv[1:])"))

        gc_args = ["-vvv", "--format", "stdout"]
        result = run_script(script_file, "arg1", "arg2", "--flag", gc_args=gc_args)

        output = (result.stdout + result.stderr).lower()

        assert result.returncode == 0
        assert "Args:  ['arg1', 'arg2', '--flag']" in result.stdout

        assert_stdout_format(output)

    def test_run_script_with_overlapping_args(self, tmp_path: Path) -> None:
        """Script args after -s are passed verbatim, even if they overlap with gc-monitor options."""
        output_file = tmp_path / "trace.json"
        script_file = tmp_path / "test_script.py"
        script_file.write_text(get_long_running_script("print('Args: ', sys.argv[1:])"))

        # gc-monitor options BEFORE -s, script args AFTER (including overlapping --format, -v)
        gc_args = ["-vvv", "--format", "chrome", "-o", str(output_file)]
        result = run_script(script_file, "--format", "json", "-v", "--format", "csv", gc_args=gc_args)

        assert result.returncode == 0
        assert "Args:  ['--format', 'json', '-v', '--format', 'csv']" in result.stdout

        assert_valid_chrome_trace_format(output_file)


class TestRunCommandModuleMode:
    """Integration tests for run command in module mode."""

    def test_run_module_short(self, tmp_path: Path) -> None:
        output_file = tmp_path / "trace.json"

        result = run_module("timeit", "-n", "1", "pass", gc_args=["-vvv", "-o", str(output_file)])

        assert result.returncode == 0
        assert_valid_chrome_trace_format(output_file)

    def test_run_module_long_running_chrome_trace_format(self, tmp_path: Path) -> None:
        output_file = tmp_path / "trace.json"

        gc_args = ["-vvv", "--format", "chrome", "-o", str(output_file)]
        result = run_module("test", "test_gc", "-v", gc_args=gc_args)

        assert result.returncode == 0
        assert_valid_chrome_trace_format(output_file)

    def test_run_module_long_running_jsonl_format(self, tmp_path: Path) -> None:
        output_file = tmp_path / "trace.jsonl"

        gc_args = ["-vvv", "--format", "jsonl", "-o", str(output_file)]
        result = run_module("test", "test_gc", "-v", gc_args=gc_args)

        assert result.returncode == 0
        assert_jsonl_format(output_file)

    def test_run_module_long_running_stdout_format(self) -> None:
        gc_args = ["-vvv", "--format", "stdout"]
        result = run_module("test", "test_gc", "-v", gc_args=gc_args)

        output = (result.stdout + result.stderr).lower()

        assert result.returncode == 0
        assert_stdout_format(output)

    def test_run_module_with_overlapping_args(self, tmp_path: Path) -> None:
        """Script args after -m are passed verbatim, even if they overlap with gc-monitor options."""
        output_file = tmp_path / "trace.json"

        # gc-monitor options BEFORE -m, script args AFTER (including overlapping --format, -v)
        gc_args = ["-v", "--format", "chrome", "-o", str(output_file)]
        run_module("test", "test_gc", "-v", gc_args=gc_args)

        assert output_file.exists()
        assert_valid_chrome_trace_format(output_file)


class TestRunCommandErrors:
    """Integration tests for run command error handling."""

    def test_run_script_not_found(self, tmp_path: Path) -> None:
        """Test running non-existent script."""
        output_file = tmp_path / "trace.json"

        result = run_script(Path("/nonexistent/script.py"), gc_args=["-vvv", "-o", str(output_file)])

        output = (result.stdout + result.stderr).lower()

        assert result.returncode != 0
        assert "script not found" in output

    def test_run_module_not_found(self, tmp_path: Path) -> None:
        """Test running non-existent module."""
        output_file = tmp_path / "trace.json"

        result = run_module("nonexistent_module_xyz", gc_args=["-vvv", "-o", str(output_file)])

        output = (result.stdout + result.stderr).lower()

        assert result.returncode != 0
        assert "no module named nonexistent_module_xyz" in output

    def test_run_script_syntax_error(self, tmp_path: Path) -> None:
        """Test running script with syntax error."""
        output_file = tmp_path / "trace.json"
        script_file = tmp_path / "bad_script.py"
        script_file.write_text(get_long_running_script("invalid !!!"))

        result = run_script(script_file, gc_args=["-vvv", "-o", str(output_file)])

        output = (result.stdout + result.stderr).lower()

        assert result.returncode != 0
        assert "syntax" in output or "error" in output


class TestRunCommandHelp:
    """Tests for run command help."""

    def test_run_help(self) -> None:
        """Test run command help output."""
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "gc_monitor",
                "run",
                "--help",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )

        # Help should be displayed
        assert result.returncode == 0
        assert "Run a Python script or module" in result.stdout
        assert "-m" in result.stdout
        assert "--module" in result.stdout
        assert "--format" in result.stdout
        assert "-s" in result.stdout
        assert "--script" in result.stdout

    def test_mutually_exclusive_target(self) -> None:
        """Test that script and -m are mutually exclusive.

        Note: This is tested via unit test (TestCmdRunUnit.test_cmd_run_both_targets)
        because the CLI arg splitting makes subprocess testing impossible.
        This test verifies the help text mentions both options.
        """
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "gc_monitor",
                "run",
                "--help",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )

        assert result.returncode == 0
        assert "--module" in result.stdout
        assert "--script" in result.stdout
