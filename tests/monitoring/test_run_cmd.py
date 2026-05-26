"""Integration tests for the run command."""

import json
import subprocess
import sys
from argparse import Namespace
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest


from monitoring.conftest import MonitorArgsFactory
from tests.helpers import assert_valid_chrome_trace_format


# =============================================================================
# Unit Tests for cmd_run
# =============================================================================


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
            "thread_id": 0,
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
                )

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
                )

    def test_cmd_run_cleanup_called(self) -> None:
        """Test cleanup callback calls runner.terminate()."""
        from gc_monitor.commands import run_cmd

        args = self._make_run_args(module_name="timeit")

        def mock_loop(process, wait_policy, options, cleanup=None):
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


def get_long_running_script(*args:list[Any]) -> str:
    script:str = """
import gc
import sys
import time
gc.collect()
n = 1000
d = {}
for i in range(n):
    for j in range(n):
        d[(i, j)] = i * n + j
gc.collect()

"""
    return script + "\n".join([str(s) for s in args])

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
        capture_output=True,
        text=True,
        timeout=30,
    )
    return proc


class TestRunCommandScriptMode:
    """Integration tests for run command in script mode."""

    def test_run_simple_script(self, tmp_path: Path) -> None:
        """Test running a simple script with GC monitoring."""
        # Create a simple test script
        output_file = tmp_path / "trace.json"
        script_file = tmp_path / "test_script.py"
        script_file.write_text(get_long_running_script("print('Hello')", "sys.exit(42)"))

        # Run gc-monitor run command
        result = run_script(script_file, gc_args=["-v", "-o", str(output_file)])

        # Script exit code should be propagated
        assert result.returncode == 42
        assert "Hello" in result.stdout

        # Output file should be created
        assert_valid_chrome_trace_format(output_file)

    def test_run_script_with_args(self, tmp_path: Path) -> None:
        """Test running a script with arguments."""
        output_file = tmp_path / "trace.json"
        script_file = tmp_path / "test_script.py"
        script_file.write_text(get_long_running_script("print('Args: ', sys.argv[1:])"))

        result = run_script(script_file, "arg1", "arg2", "--flag", gc_args=["-o", str(output_file)])

        # Script should exit successfully
        assert result.returncode == 0
        assert "Args:  ['arg1', 'arg2', '--flag']" in result.stdout
        # Output file should be created
        assert_valid_chrome_trace_format(output_file)

    def test_run_script_jsonl_format(self, tmp_path: Path) -> None:
        """Test running a script with JSONL format."""
        output_file = tmp_path / "trace.jsonl"
        script_file = tmp_path / "test_script.py"
        script_file.write_text(get_long_running_script("print('Done')"))

        result = run_script(script_file, gc_args=["--format", "jsonl", "-o", str(output_file)])

        # Script should exit successfully
        assert result.returncode == 0
        assert "Done" in result.stdout

        # Output file should be created
        assert output_file.exists()

        # JSONL file should have valid JSON lines
        lines = output_file.read_text().strip().split("\n")
        for line in lines:
            if line.strip():
                json.loads(line)  # Should not raise

    def test_run_script_stdout_format(self, tmp_path: Path) -> None:
        """Test running a script with stdout format."""
        script_file = tmp_path / "test_script.py"
        script_file.write_text(get_long_running_script("print('Done')"))

        result = run_script(script_file, gc_args=["--format", "stdout"])

        # Script should exit successfully
        assert result.returncode == 0

        # Should have output to stdout
        assert "Done" in result.stdout

    def test_run_script_with_overlapping_args(self, tmp_path: Path) -> None:
        """Script args after -s are passed verbatim, even if they overlap with gc-monitor options."""
        output_file = tmp_path / "trace.json"
        script_file = tmp_path / "test_script.py"
        script_file.write_text(get_long_running_script("print('Args: ', sys.argv[1:])"))

        # gc-monitor options BEFORE -s, script args AFTER (including overlapping --format, -v)
        proc = subprocess.run(
            [
                sys.executable,
                "-u",
                "-m",
                "gc_monitor",
                "run",
                "-v", "--format", "chrome", "-o", str(output_file),
                "-s", str(script_file.as_posix()),
                "--format", "json", "-v", "--format", "csv",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )

        assert proc.returncode == 0
        assert "Args:  ['--format', 'json', '-v', '--format', 'csv']" in proc.stdout
        assert_valid_chrome_trace_format(output_file)


class TestRunCommandModuleMode:
    """Integration tests for run command in module mode."""

    def test_run_module_short(self, tmp_path: Path) -> None:
        output_file = tmp_path / "trace.json"

        result = run_module("timeit", "-n", "1", "pass", gc_args=["-o", str(output_file)])

        # timeit should exit successfully
        assert result.returncode == 0

        # Output file should be created
        assert_valid_chrome_trace_format(output_file)

    def test_run_module_long(self, tmp_path: Path) -> None:
        output_file = tmp_path / "trace.json"

        result = run_module("test", "test_gc", gc_args=["-vvv", "-o", str(output_file)])

        print(result.stdout)
        print(result.stderr)

        # Should exit (duration expired)
        assert result.returncode == 0

        # Output file should be created
        assert_valid_chrome_trace_format(output_file)

    def test_run_module_with_overlapping_args(self, tmp_path: Path) -> None:
        """Script args after -m are passed verbatim, even if they overlap with gc-monitor options."""
        output_file = tmp_path / "trace.json"

        # gc-monitor options BEFORE -m, script args AFTER (including overlapping --format, -v)
        proc = subprocess.run(
            [
                sys.executable,
                "-u",
                "-m",
                "gc_monitor",
                "run",
                "-v", "--format", "chrome", "-o", str(output_file),
                "-m", "timeit",
                "--format", "json", "-v", "--format", "csv",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )

        # timeit may exit non-zero with unknown args depending on Python version
        # The key assertion is that gc-monitor processed its options correctly
        # and the output file was created
        assert output_file.exists()
        assert_valid_chrome_trace_format(output_file)


class TestRunCommandErrors:
    """Integration tests for run command error handling."""

    def test_run_script_not_found(self, tmp_path: Path) -> None:
        """Test running non-existent script."""
        output_file = tmp_path / "trace.json"

        result = run_script(Path("/nonexistent/script.py"), gc_args=["-o", str(output_file)])

        # Should fail with error
        assert result.returncode != 0
        assert "Failed to start subprocess" in result.stderr or "not found" in result.stderr.lower()

    def test_run_module_not_found(self, tmp_path: Path) -> None:
        """Test running non-existent module."""
        output_file = tmp_path / "trace.json"

        result = run_module("nonexistent_module_xyz", gc_args=["-vvv", "-o", str(output_file)])

        # Should fail with error (returncode != 0)
        assert result.returncode != 0
        # Error message should be in stderr (could be module error or GC monitor error)
        assert result.stderr or result.returncode != 0

    def test_run_script_syntax_error(self, tmp_path: Path) -> None:
        """Test running script with syntax error."""
        output_file = tmp_path / "trace.json"
        script_file = tmp_path / "bad_script.py"
        script_file.write_text("invalid syntax !!!")

        result = run_script(script_file, gc_args=["-vvv", "-o", str(output_file)])

        # Should fail with error
        assert result.returncode != 0
        # Error message should mention syntax error (may be in stdout or stderr)
        combined = (result.stdout + result.stderr).lower()
        assert "syntax" in combined or "error" in combined


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
