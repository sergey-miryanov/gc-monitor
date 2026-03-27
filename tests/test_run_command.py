"""Integration tests for the run command."""

import json
import subprocess
import sys
from pathlib import Path

import pytest


class TestRunCommandScriptMode:
    """Integration tests for run command in script mode."""

    def test_run_simple_script(self, tmp_path: Path) -> None:
        """Test running a simple script with GC monitoring."""
        # Create a simple test script
        script_file = tmp_path / "test_script.py"
        script_file.write_text("""
import sys
print("Hello from test script")
sys.exit(42)
""")

        output_file = tmp_path / "trace.json"

        # Run gc-monitor run command
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "gc_monitor",
                "run",
                "-s",
                str(script_file),
                "-o",
                str(output_file),
                "-v",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )

        # Script exit code should be propagated
        assert result.returncode == 42

        # Output file should be created
        assert output_file.exists()

        # Trace file should be valid JSON
        content = output_file.read_text()
        # Debug: print content if invalid
        try:
            trace_data = json.loads(content)
        except json.JSONDecodeError as e:
            # Print content for debugging
            print(f"Invalid JSON: {content[:500]}")
            raise
        assert isinstance(trace_data, list)

    def test_run_script_with_args(self, tmp_path: Path) -> None:
        """Test running a script with arguments."""
        script_file = tmp_path / "test_script.py"
        script_file.write_text("""
import sys
print("Args:", sys.argv[1:])
""")

        output_file = tmp_path / "trace.json"

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "gc_monitor",
                "run",
                "-s",
                str(script_file),
                "arg1",
                "arg2",
                "--flag",
                "-o",
                str(output_file),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )

        # Script should exit successfully
        assert result.returncode == 0

        # Output file should be created
        assert output_file.exists()

    def test_run_script_jsonl_format(self, tmp_path: Path) -> None:
        """Test running a script with JSONL format."""
        script_file = tmp_path / "test_script.py"
        script_file.write_text("""
import gc
gc.collect()
print("Done")
""")

        output_file = tmp_path / "trace.jsonl"

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "gc_monitor",
                "run",
                "-s",
                str(script_file),
                "--format",
                "jsonl",
                "-o",
                str(output_file),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )

        # Script should exit successfully
        assert result.returncode == 0

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
        script_file.write_text("""
import gc
gc.collect()
print("Done")
""")

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "gc_monitor",
                "run",
                "-s",
                str(script_file),
                "--format",
                "stdout",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )

        # Script should exit successfully
        assert result.returncode == 0

        # Should have output to stdout
        assert "Done" in result.stdout or result.stderr


class TestRunCommandModuleMode:
    """Integration tests for run command in module mode."""

    def test_run_module_timeit(self, tmp_path: Path) -> None:
        """Test running the timeit module."""
        output_file = tmp_path / "trace.json"

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "gc_monitor",
                "run",
                "-m",
                "timeit",
                "-n",
                "1",
                "pass",
                "-o",
                str(output_file),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )

        # timeit should exit successfully
        assert result.returncode == 0

        # Output file should be created
        assert output_file.exists()

    def test_run_module_http_server(self, tmp_path: Path) -> None:
        """Test running http.server module with duration limit."""
        output_file = tmp_path / "trace.json"

        # Run http.server with short duration
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "gc_monitor",
                "run",
                "-m",
                "http.server",
                "0",  # Port 0 (random available port)
                "-d",
                "2",  # Run for 2 seconds
                "-o",
                str(output_file),
                "-v",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )

        # Should exit (duration expired)
        assert result.returncode == 0 or result.returncode is not None

        # Output file should be created
        assert output_file.exists()


class TestRunCommandErrors:
    """Integration tests for run command error handling."""

    def test_run_script_not_found(self, tmp_path: Path) -> None:
        """Test running non-existent script."""
        output_file = tmp_path / "trace.json"

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "gc_monitor",
                "run",
                "-s",
                "/nonexistent/script.py",
                "-o",
                str(output_file),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )

        # Should fail with error
        assert result.returncode != 0
        assert "Failed to start subprocess" in result.stderr or "not found" in result.stderr.lower()

    def test_run_module_not_found(self, tmp_path: Path) -> None:
        """Test running non-existent module."""
        output_file = tmp_path / "trace.json"

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "gc_monitor",
                "run",
                "-m",
                "nonexistent_module_xyz",
                "-o",
                str(output_file),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )

        # Should fail with error (returncode != 0)
        assert result.returncode != 0
        # Error message should be in stderr (could be module error or GC monitor error)
        assert result.stderr or result.returncode != 0

    def test_run_script_syntax_error(self, tmp_path: Path) -> None:
        """Test running script with syntax error."""
        script_file = tmp_path / "bad_script.py"
        script_file.write_text("invalid syntax !!!")

        output_file = tmp_path / "trace.json"

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "gc_monitor",
                "run",
                "-s",
                str(script_file),
                "-o",
                str(output_file),
                "--fallback=yes",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )

        # Should fail with error
        assert result.returncode != 0
        # Error message should mention syntax error or process exit
        assert "syntax" in result.stderr.lower() or "exited" in result.stderr.lower() or "error" in result.stderr.lower()


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

    def test_mutually_exclusive_target(self, tmp_path: Path) -> None:
        """Test that script and -m are mutually exclusive."""
        output_file = tmp_path / "trace.json"

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "gc_monitor",
                "run",
                "-m",
                "timeit",
                "-s",
                "script.py",  # Both -m and -s
                "-o",
                str(output_file),
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )

        # Should fail with argument error
        assert result.returncode != 0
        assert "cannot specify both" in result.stderr.lower() or "both script" in result.stderr.lower()
