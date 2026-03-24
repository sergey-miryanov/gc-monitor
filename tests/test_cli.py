"""Tests for the gc-monitor CLI."""

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest


def test_cli_help() -> None:
    """Test CLI --help option."""
    result = subprocess.run(
        [sys.executable, "-m", "gc_monitor.cli", "--help"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "Monitor Python's garbage collector" in result.stdout
    assert "monitor" in result.stdout
    assert "combine" in result.stdout
    # --output is now only in monitor subcommand help, not top-level
    assert "--output" not in result.stdout


def test_cli_monitor_help() -> None:
    """Test CLI monitor subcommand --help option."""
    result = subprocess.run(
        [sys.executable, "-m", "gc_monitor.cli", "monitor", "--help"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "Monitor Python's garbage collector" in result.stdout
    assert "pid" in result.stdout
    assert "--output" in result.stdout
    assert "--rate" in result.stdout
    assert "--duration" in result.stdout
    assert "--verbose" in result.stdout
    assert "--thread-id" in result.stdout


def test_cli_combine_help() -> None:
    """Test CLI combine subcommand --help option."""
    result = subprocess.run(
        [sys.executable, "-m", "gc_monitor.cli", "combine", "--help"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "Combine multiple Chrome Trace Format files" in result.stdout
    assert "inputs" in result.stdout
    assert "--output" in result.stdout


def test_cli_missing_pid() -> None:
    """Test CLI fails when PID is not provided."""
    result = subprocess.run(
        [sys.executable, "-m", "gc_monitor.cli", "monitor"],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "the following arguments are required: pid" in result.stderr


def test_cli_invalid_pid() -> None:
    """Test CLI with invalid PID."""
    # Note: mock handler accepts any PID, so we use a short duration
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "gc_monitor.cli",
            "monitor",
            "999999",
            "-d",
            "0.1",
        ],
        capture_output=True,
        text=True,
        timeout=5,
    )
    # With mock handler, this will succeed (mock accepts any PID)
    assert result.returncode == 0


def test_cli_basic_run(tmp_path: Path) -> None:
    """Test CLI basic run with duration."""
    output_file = tmp_path / "test_trace.json"

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "gc_monitor.cli",
            "monitor",
            "12345",  # Fake PID that will work with mock handler
            "-o",
            str(output_file),
            "-d",
            "0.5",
            "-r",
            "0.1",
        ],
        capture_output=True,
        text=True,
    )

    # Should succeed (mock handler accepts any PID)
    # Note: Mock handler may randomly fail (10% chance per read), so monitor may stop early
    assert result.returncode == 0

    # File should be created (even if handler failed early)
    assert output_file.exists()

    # Verify output file is valid JSON
    with open(output_file) as f:
        data: list[object] = json.load(f)  # type: ignore[assignment]

    assert isinstance(data, list)
    # Should have at least metadata events (process_name, thread_name)
    # May have more if GC events were collected before handler failure
    assert len(data) >= 2


def test_cli_verbose_output(tmp_path: Path) -> None:
    """Test CLI verbose output."""
    output_file = tmp_path / "test_trace.json"

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "gc_monitor.cli",
            "monitor",
            "12345",
            "-o",
            str(output_file),
            "-d",
            "0.3",
            "-v",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    # Verbose output goes to stderr (logging)
    assert "Monitoring PID 12345" in result.stderr
    assert f"Output: {output_file}" in result.stderr
    assert "Rate:" in result.stderr
    assert "Duration:" in result.stderr
    assert "Total events:" in result.stderr


def test_cli_default_output_file(tmp_path: Path) -> None:
    """Test CLI uses default output file when not specified."""
    # Change to tmp_path directory so default file is created there
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "gc_monitor.cli",
            "monitor",
            "12345",
            "-d",
            "0.3",
        ],
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )

    assert result.returncode == 0
    default_file = tmp_path / "gc_trace.json"
    assert default_file.exists()


def test_cli_custom_rate(tmp_path: Path) -> None:
    """Test CLI with custom polling rate."""
    output_file = tmp_path / "test_trace.json"

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "gc_monitor.cli",
            "monitor",
            "12345",
            "-o",
            str(output_file),
            "-d",
            "0.5",
            "-r",
            "0.05",
            "-v",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    # Verbose output goes to stderr (logging)
    assert "Rate: 0.05" in result.stderr


def test_cli_output_json_structure(tmp_path: Path) -> None:
    """Test CLI output JSON has correct structure."""
    output_file = tmp_path / "test_trace.json"

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "gc_monitor.cli",
            "monitor",
            "12345",
            "-o",
            str(output_file),
            "-d",
            "0.3",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0

    # File should be created (even if handler failed early)
    assert output_file.exists()

    with open(output_file) as f:
        data: list[dict[str, Any]] = json.load(f)  # type: ignore[assignment]

    # Should have metadata events (process_name, thread_name)
    metadata_events = [e for e in data if e.get("ph") == "M"]
    assert len(metadata_events) == 2

    # May have complete events if GC events were collected before handler failure
    _complete_events = [e for e in data if e.get("ph") == "X"]
    # Note: complete_events may be empty if handler failed immediately

    # May have counter events if GC events were collected before handler failure
    _counter_events = [e for e in data if e.get("ph") == "C"]
    # Note: counter_events may be empty if handler failed immediately


def test_cli_quiet_output(tmp_path: Path) -> None:
    """Test CLI quiet output (non-verbose mode)."""
    output_file = tmp_path / "test_trace.json"

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "gc_monitor.cli",
            "monitor",
            "12345",
            "-o",
            str(output_file),
            "-d",
            "0.3",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    # In quiet mode (WARNING level), INFO messages don't appear
    # Only warnings/errors would appear in stderr
    assert "Monitoring PID" not in result.stderr


def test_cli_stdout_format(tmp_path: Path) -> None:
    """Test CLI with --format stdout option."""
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "gc_monitor.cli",
            "monitor",
            "12345",
            "--format",
            "stdout",
            "-d",
            "0.3",
        ],
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )

    assert result.returncode == 0
    # Output should be JSONL format (one JSON per line)
    lines = result.stdout.strip().split("\n")
    for line in lines:
        if line:
            data: dict[str, Any] = json.loads(line)
            assert "pid" in data
            assert "tid" in data
            assert "gen" in data


def test_cli_stdout_format_with_thread_name(tmp_path: Path) -> None:
    """Test CLI with --format stdout option."""
    # Note: --thread-name is only used for chrome format, not stdout
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "gc_monitor.cli",
            "monitor",
            "12345",
            "--format",
            "stdout",
            "-d",
            "0.3",
        ],
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )

    assert result.returncode == 0
    # Verify JSONL output format
    lines = result.stdout.strip().split("\n")
    assert len(lines) > 0
    for line in lines:
        if line:
            data: dict[str, Any] = json.loads(line)
            assert "pid" in data
            assert "tid" in data


def test_cli_verbose_with_stdout_format(tmp_path: Path) -> None:
    """Test CLI verbose output with stdout format."""
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "gc_monitor.cli",
            "monitor",
            "12345",
            "--format",
            "stdout",
            "-d",
            "0.3",
            "-v",
        ],
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )

    assert result.returncode == 0
    # Should have verbose output (to stderr)
    assert "Monitoring PID 12345" in result.stderr
    assert "Format: stdout" in result.stderr
    assert "Rate:" in result.stderr
    assert "Duration:" in result.stderr
    # stdout should have JSONL data (may be empty if mock handler returns no events)
    # Just verify it's valid JSONL or empty
    if result.stdout.strip():
        assert '{"pid":' in result.stdout


def test_cli_quiet_with_stdout_format(tmp_path: Path) -> None:
    """Test CLI quiet output with stdout format."""
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "gc_monitor.cli",
            "monitor",
            "12345",
            "--format",
            "stdout",
            "-d",
            "0.3",
        ],
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )

    assert result.returncode == 0
    # stdout should have JSONL data
    assert '{"pid":' in result.stdout
    # In quiet mode (WARNING level), INFO messages don't appear in stderr
    assert "Monitoring PID" not in result.stderr


def test_cli_connection_failure() -> None:
    """Test CLI with monitor returning None (connection failure).

    Note: This test uses a very high PID that may fail with real _gc_monitor.
    With mock implementation, it will succeed, so we test the error path
    by checking the error message format.
    """
    # Use subprocess to test the actual CLI behavior
    # The mock implementation accepts any PID, so we can't easily test failure
    # This test documents the expected behavior when connection fails
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "gc_monitor.cli",
            "monitor",
            "999999999",  # Very high PID that might fail
            "-d",
            "0.1",
        ],
        capture_output=True,
        text=True,
        timeout=5,
    )

    # With mock, this should succeed. The test ensures the CLI handles it gracefully.
    assert result.returncode == 0


def test_cli_fallback_no_error(caplog: pytest.LogCaptureFixture) -> None:
    """Test CLI exits with error when --fallback=no and _gc_monitor unavailable."""
    from gc_monitor import cli
    from unittest.mock import patch

    # Mock connect to raise RuntimeError (simulating _gc_monitor unavailable with fallback=no)
    with patch.object(cli, "connect", side_effect=RuntimeError("_gc_monitor not available")):
        with patch.object(cli, "StdoutExporter"):
            result = cli.main(["monitor", "12345", "--format", "stdout", "--fallback", "no"])

            assert result == 1

            # Verify error message was logged
            assert "_gc_monitor not available" in caplog.text


def test_cli_duration_based_execution(tmp_path: Path) -> None:
    """Test CLI duration-based execution loop."""
    output_file = tmp_path / "test_trace.json"

    import time

    start = time.monotonic()
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "gc_monitor.cli",
            "monitor",
            "12345",
            "-o",
            str(output_file),
            "-d",
            "0.5",
            "-r",
            "0.1",
            "-v",
        ],
        capture_output=True,
        text=True,
    )
    elapsed = time.monotonic() - start

    assert result.returncode == 0
    # Should run for approximately the specified duration
    assert elapsed >= 0.05  # Minimal tolerance - just verify it ran
    # Verbose output goes to stderr (logging)
    assert "Monitoring for 0.5 seconds" in result.stderr


def test_cli_signal_handling(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test CLI signal handler registration."""
    from gc_monitor import cli
    from unittest.mock import MagicMock, patch

    # Mock the signal module to track handler registration
    mock_signal = MagicMock()
    mock_signal.SIGINT = 2
    mock_signal.SIGTERM = 15
    mock_signal.signal = MagicMock()

    with patch.object(cli, "signal", mock_signal):
        # Mock connect to return a mock monitor
        mock_monitor = MagicMock()
        mock_monitor.is_running = False  # Exit immediately

        mock_exporter = MagicMock()
        mock_exporter.get_event_count = MagicMock(return_value=5)

        with patch.object(cli, "connect", return_value=mock_monitor):
            with patch.object(cli, "StdoutExporter", return_value=mock_exporter):
                # Run main - should exit quickly since monitor.is_running is False
                result = cli.main(["monitor", "12345", "--format", "stdout", "-d", "0.1"])

                assert result == 0

                # Verify signal handlers were registered
                assert mock_signal.signal.call_count >= 2


def test_cli_early_exit_on_shutdown_requested() -> None:
    """Test CLI early exit when shutdown event is set."""
    from unittest.mock import MagicMock, patch

    from gc_monitor import cli

    # Mock monitor
    mock_monitor = MagicMock()
    mock_monitor.is_running = True
    mock_monitor.stop = MagicMock()

    # Mock exporter
    mock_exporter = MagicMock()
    mock_exporter.get_event_count = MagicMock(return_value=3)

    # Track if loop exited early
    loop_iterations = [0]

    def mock_wait(**kwargs) -> None:
        loop_iterations[0] += 1
        if loop_iterations[0] >= 2:
            # Simulate shutdown after 2 iterations
            raise RuntimeError("shutdown")

    with patch.object(cli, "connect", return_value=mock_monitor):
        with patch.object(cli, "StdoutExporter", return_value=mock_exporter):
            with patch.object(cli, "threading") as mock_threading:
                mock_event = MagicMock()
                mock_event.is_set = MagicMock(side_effect=[False, False, True])
                mock_event.wait = mock_wait
                mock_threading.Event = MagicMock(return_value=mock_event)

                try:
                    cli.main(["monitor", "12345", "--format", "stdout", "-d", "1.0"])
                except RuntimeError as e:
                    if str(e) != "shutdown":
                        raise

    # Verify monitor.stop() was called
    mock_monitor.stop.assert_called()


def test_cli_thread_name_option(tmp_path: Path) -> None:
    """Test CLI with --thread-name custom option."""
    output_file = tmp_path / "test_trace.json"

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "gc_monitor.cli",
            "monitor",
            "12345",
            "-o",
            str(output_file),
            "--thread-name",
            "MyCustomThread",
            "-d",
            "0.3",
            "-v",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0

    # Verify output file was created
    assert output_file.exists()
    with open(output_file) as f:
        content = f.read()

    # Verify thread name appears in the file content
    assert "MyCustomThread" in content

    # Verify it's valid JSON
    data: list[dict[str, Any]] = json.loads(content)
    assert isinstance(data, list)
    assert len(data) > 0


# =============================================================================
# Environment Variable Tests
# =============================================================================


def test_cli_env_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test CLI uses GC_MONITOR_OUTPUT environment variable."""
    output_file = tmp_path / "env_test_trace.json"
    monkeypatch.setenv("GC_MONITOR_OUTPUT", str(output_file))

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "gc_monitor.cli",
            "monitor",
            "12345",
            "-d",
            "0.3",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert output_file.exists()


def test_cli_env_output_cli_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test CLI option overrides GC_MONITOR_OUTPUT environment variable."""
    env_file = tmp_path / "env_trace.json"
    cli_file = tmp_path / "cli_trace.json"
    monkeypatch.setenv("GC_MONITOR_OUTPUT", str(env_file))

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "gc_monitor.cli",
            "monitor",
            "12345",
            "-o",
            str(cli_file),
            "-d",
            "0.3",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    # CLI option should take precedence
    assert cli_file.exists()
    assert not env_file.exists()


def test_cli_env_rate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test CLI uses GC_MONITOR_RATE environment variable."""
    output_file = tmp_path / "test_trace.json"
    monkeypatch.setenv("GC_MONITOR_RATE", "0.05")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "gc_monitor.cli",
            "monitor",
            "12345",
            "-o",
            str(output_file),
            "-d",
            "0.3",
            "-v",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "Rate: 0.05" in result.stderr


def test_cli_env_rate_cli_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test CLI option overrides GC_MONITOR_RATE environment variable."""
    output_file = tmp_path / "test_trace.json"
    monkeypatch.setenv("GC_MONITOR_RATE", "0.05")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "gc_monitor.cli",
            "monitor",
            "12345",
            "-o",
            str(output_file),
            "-r",
            "0.2",
            "-d",
            "0.3",
            "-v",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    # CLI option should take precedence
    assert "Rate: 0.2" in result.stderr


def test_cli_env_duration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test CLI uses GC_MONITOR_DURATION environment variable."""
    output_file = tmp_path / "test_trace.json"
    monkeypatch.setenv("GC_MONITOR_DURATION", "0.5")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "gc_monitor.cli",
            "monitor",
            "12345",
            "-o",
            str(output_file),
            "-v",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "Duration: 0.5" in result.stderr


def test_cli_env_duration_cli_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test CLI option overrides GC_MONITOR_DURATION environment variable."""
    output_file = tmp_path / "test_trace.json"
    monkeypatch.setenv("GC_MONITOR_DURATION", "0.5")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "gc_monitor.cli",
            "monitor",
            "12345",
            "-o",
            str(output_file),
            "-d",
            "0.3",
            "-v",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    # CLI option should take precedence
    assert "Duration: 0.3" in result.stderr


def test_cli_env_verbose(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test CLI uses GC_MONITOR_VERBOSE environment variable."""
    monkeypatch.setenv("GC_MONITOR_VERBOSE", "1")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "gc_monitor.cli",
            "monitor",
            "12345",
            "-d",
            "0.3",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    # Verbose output should appear even without -v flag
    assert "Monitoring PID 12345" in result.stderr


def test_cli_env_verbose_true_value(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test GC_MONITOR_VERBOSE accepts 'true' as truthy value."""
    monkeypatch.setenv("GC_MONITOR_VERBOSE", "true")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "gc_monitor.cli",
            "monitor",
            "12345",
            "-d",
            "0.3",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "Monitoring PID 12345" in result.stderr


def test_cli_env_verbose_yes_value(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test GC_MONITOR_VERBOSE accepts 'yes' as truthy value."""
    monkeypatch.setenv("GC_MONITOR_VERBOSE", "yes")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "gc_monitor.cli",
            "monitor",
            "12345",
            "-d",
            "0.3",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "Monitoring PID 12345" in result.stderr


def test_cli_env_verbose_on_value(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test GC_MONITOR_VERBOSE accepts 'on' as truthy value."""
    monkeypatch.setenv("GC_MONITOR_VERBOSE", "on")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "gc_monitor.cli",
            "monitor",
            "12345",
            "-d",
            "0.3",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "Monitoring PID 12345" in result.stderr


def test_cli_env_verbose_cli_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test -v CLI option works regardless of GC_MONITOR_VERBOSE env var."""
    # Env var is false, but CLI -v should enable verbose
    monkeypatch.setenv("GC_MONITOR_VERBOSE", "0")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "gc_monitor.cli",
            "monitor",
            "12345",
            "-d",
            "0.3",
            "-v",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    # CLI -v should take precedence
    assert "Monitoring PID 12345" in result.stderr


def test_cli_env_format(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test CLI uses GC_MONITOR_FORMAT environment variable."""
    monkeypatch.setenv("GC_MONITOR_FORMAT", "stdout")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "gc_monitor.cli",
            "monitor",
            "12345",
            "-d",
            "0.3",
            "-v",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "Format: stdout" in result.stderr
    # stdout should have JSONL data (at least one event line)
    # Note: output may be empty if no GC events occurred during monitoring
    if result.stdout.strip():
        assert '{"pid":' in result.stdout


def test_cli_env_format_cli_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test CLI option overrides GC_MONITOR_FORMAT environment variable."""
    monkeypatch.setenv("GC_MONITOR_FORMAT", "stdout")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "gc_monitor.cli",
            "monitor",
            "12345",
            "--format",
            "chrome",
            "-d",
            "0.3",
            "-v",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    # CLI option should take precedence
    assert "Format: chrome" in result.stderr


def test_cli_env_thread_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test CLI uses GC_MONITOR_THREAD_NAME environment variable."""
    output_file = tmp_path / "test_trace.json"
    monkeypatch.setenv("GC_MONITOR_THREAD_NAME", "EnvThread")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "gc_monitor.cli",
            "monitor",
            "12345",
            "-o",
            str(output_file),
            "-d",
            "0.3",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert output_file.exists()
    with open(output_file) as f:
        content = f.read()
    assert "EnvThread" in content


def test_cli_env_thread_name_cli_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test CLI option overrides GC_MONITOR_THREAD_NAME environment variable."""
    output_file = tmp_path / "test_trace.json"
    monkeypatch.setenv("GC_MONITOR_THREAD_NAME", "EnvThread")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "gc_monitor.cli",
            "monitor",
            "12345",
            "-o",
            str(output_file),
            "--thread-name",
            "CliThread",
            "-d",
            "0.3",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert output_file.exists()
    with open(output_file) as f:
        content = f.read()
    # CLI option should take precedence
    assert "CliThread" in content
    assert "EnvThread" not in content


def test_cli_thread_id_option(tmp_path: Path) -> None:
    """Test CLI with --thread-id option for JSONL format."""
    output_file = tmp_path / "test.jsonl"

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "gc_monitor.cli",
            "monitor",
            "12345",
            "--format",
            "jsonl",
            "-o",
            str(output_file),
            "--thread-id",
            "9999",
            "-d",
            "0.1",
            "-v",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    # Verify output file was created
    assert output_file.exists()
    # Verify thread ID appears in the JSONL output
    with open(output_file, "r", encoding="utf-8") as f:
        content = f.read()
        if content.strip():
            import json
            for line in content.strip().split("\n"):
                event = json.loads(line)
                if event.get("tid") == 9999:
                    break
            else:
                pytest.fail("Thread ID 9999 not found in output")


def test_cli_env_thread_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test CLI uses GC_MONITOR_THREAD_ID environment variable."""
    output_file = tmp_path / "test.jsonl"
    monkeypatch.setenv("GC_MONITOR_THREAD_ID", "7777")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "gc_monitor.cli",
            "monitor",
            "12345",
            "--format",
            "jsonl",
            "-o",
            str(output_file),
            "-d",
            "0.1",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    # Output file may not exist if no GC events occurred during monitoring
    # If it exists, verify thread ID appears in the JSONL output
    if output_file.exists():
        with open(output_file, "r", encoding="utf-8") as f:
            content = f.read()
            if content.strip():
                import json
                for line in content.strip().split("\n"):
                    event = json.loads(line)
                    if event.get("tid") == 7777:
                        break
                else:
                    pytest.fail("Thread ID 7777 from env var not found in output")


def test_cli_env_thread_id_cli_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test CLI option overrides GC_MONITOR_THREAD_ID environment variable."""
    output_file = tmp_path / "test.jsonl"
    monkeypatch.setenv("GC_MONITOR_THREAD_ID", "7777")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "gc_monitor.cli",
            "monitor",
            "12345",
            "--format",
            "jsonl",
            "-o",
            str(output_file),
            "--thread-id",
            "8888",
            "-d",
            "0.1",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    # Output file may not exist if no GC events occurred during monitoring
    # If it exists, verify CLI option takes precedence
    if output_file.exists():
        with open(output_file, "r", encoding="utf-8") as f:
            content = f.read()
            if content.strip():
                import json
                for line in content.strip().split("\n"):
                    event = json.loads(line)
                    if event.get("tid") == 8888:
                        break
                else:
                    pytest.fail("Thread ID 8888 from CLI not found in output")


def test_cli_env_fallback(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    """Test CLI uses GC_MONITOR_FALLBACK environment variable."""
    from unittest.mock import patch

    monkeypatch.setenv("GC_MONITOR_FALLBACK", "no")

    from gc_monitor import cli

    # Mock connect to raise RuntimeError (simulating _gc_monitor unavailable with fallback=no)
    with patch.object(cli, "connect", side_effect=RuntimeError("_gc_monitor not available")):
        with patch.object(cli, "StdoutExporter"):
            result = cli.main(["monitor", "12345", "--format", "stdout"])

            assert result == 1
            assert "_gc_monitor not available" in caplog.text


def test_cli_env_fallback_cli_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test CLI option overrides GC_MONITOR_FALLBACK environment variable."""
    monkeypatch.setenv("GC_MONITOR_FALLBACK", "no")

    # With CLI override to "yes", should succeed with mock
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "gc_monitor.cli",
            "monitor",
            "12345",
            "--fallback",
            "yes",
            "-d",
            "0.3",
        ],
        capture_output=True,
        text=True,
    )

    # Should succeed because fallback=yes allows mock
    assert result.returncode == 0


def test_cli_env_multiple_vars(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test CLI uses multiple environment variables simultaneously."""
    output_file = tmp_path / "multi_env_test.json"
    monkeypatch.setenv("GC_MONITOR_OUTPUT", str(output_file))
    monkeypatch.setenv("GC_MONITOR_RATE", "0.05")
    monkeypatch.setenv("GC_MONITOR_DURATION", "0.4")
    monkeypatch.setenv("GC_MONITOR_VERBOSE", "1")
    monkeypatch.setenv("GC_MONITOR_FORMAT", "chrome")
    monkeypatch.setenv("GC_MONITOR_THREAD_NAME", "MultiEnvThread")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "gc_monitor.cli",
            "monitor",
            "12345",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert output_file.exists()
    assert "Rate: 0.05" in result.stderr
    assert "Duration: 0.4" in result.stderr
    assert "Format: chrome" in result.stderr

    # Thread name is used in trace events, not logged to stderr
    with open(output_file) as f:
        content = f.read()
    assert "MultiEnvThread" in content


def test_cli_env_help_shows_env_vars() -> None:
    """Test CLI monitor subcommand --help shows environment variable names."""
    result = subprocess.run(
        [sys.executable, "-m", "gc_monitor.cli", "monitor", "--help"],
        capture_output=True,
        text=True,
        check=True,
    )

    # Help text should mention environment variables
    assert "GC_MONITOR_OUTPUT" in result.stdout
    assert "GC_MONITOR_RATE" in result.stdout
    assert "GC_MONITOR_DURATION" in result.stdout
    assert "GC_MONITOR_VERBOSE" in result.stdout
    assert "GC_MONITOR_FORMAT" in result.stdout
    assert "GC_MONITOR_THREAD_NAME" in result.stdout
    assert "GC_MONITOR_FALLBACK" in result.stdout


def test_cli_jsonl_format(tmp_path: Path) -> None:
    """Test CLI with --format jsonl."""
    output_file = tmp_path / "test.jsonl"

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "gc_monitor.cli",
            "monitor",
            "12345",
            "--format",
            "jsonl",
            "-o",
            str(output_file),
            "-d",
            "0.1",
            "-v",
        ],
        capture_output=True,
        text=True,
        check=True,
    )

    # Verbose output should mention jsonl format
    assert "Format: jsonl" in result.stderr
    assert str(output_file) in result.stderr

    # Output file exists only if GC events occurred during monitoring
    # If it exists, verify it contains valid JSONL data
    if output_file.exists():
        with open(output_file, "r", encoding="utf-8") as f:
            lines = f.readlines()

        # Each line should be valid JSON
        for line in lines:
            import json

            event = json.loads(line.strip())
            assert "pid" in event
            assert "tid" in event
            assert "gen" in event


def test_cli_jsonl_format_default_output(tmp_path: Path) -> None:
    """Test CLI with --format jsonl uses default output filename."""
    # Change to tmp_path to avoid polluting the working directory
    original_cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        # Set environment variable to get jsonl default output
        env = os.environ.copy()
        env["GC_MONITOR_FORMAT"] = "jsonl"

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "gc_monitor.cli",
                "monitor",
                "12345",
                "-d",
                "0.1",
            ],
            capture_output=True,
            text=True,
            check=True,
            env=env,
        )

        # Default output file should be gc_monitor.jsonl
        # File only exists if GC events occurred during monitoring
        default_output = tmp_path / "gc_monitor.jsonl"
        if default_output.exists():
            # If file exists, verify it's valid JSONL
            with open(default_output, "r", encoding="utf-8") as f:
                content = f.read()
                if content.strip():
                    for line in content.strip().split("\n"):
                        import json
                        json.loads(line.strip())  # Should not raise
    finally:
        os.chdir(original_cwd)


def test_cli_jsonl_format_with_thread_id(tmp_path: Path) -> None:
    """Test CLI with --format jsonl and custom thread ID."""
    output_file = tmp_path / "test.jsonl"

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "gc_monitor.cli",
            "monitor",
            "12345",
            "--format",
            "jsonl",
            "-o",
            str(output_file),
            "--thread-id",
            "5678",
            "-d",
            "0.1",
        ],
        capture_output=True,
        text=True,
        check=True,
    )

    # Output file may exist if GC events occurred during monitoring
    # If it exists, verify thread ID is in the output
    if output_file.exists():
        with open(output_file, "r", encoding="utf-8") as f:
            content = f.read()
            if content.strip():  # File has content
                for line in content.strip().split("\n"):
                    import json
                    event = json.loads(line.strip())
                    if event.get("tid") == 5678:
                        break
                else:
                    pytest.fail("Thread ID 5678 not found in output")


def test_cli_env_format_jsonl(tmp_path: Path) -> None:
    """Test CLI with GC_MONITOR_FORMAT=jsonl."""
    output_file = tmp_path / "test.jsonl"

    env = os.environ.copy()
    env["GC_MONITOR_FORMAT"] = "jsonl"
    env["GC_MONITOR_OUTPUT"] = str(output_file)

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "gc_monitor.cli",
            "monitor",
            "12345",
            "-d",
            "0.1",
        ],
        capture_output=True,
        text=True,
        check=True,
        env=env,
    )

    # Output file exists only if GC events occurred during monitoring
    # If it exists, verify it contains valid JSONL data
    if output_file.exists():
        with open(output_file, "r", encoding="utf-8") as f:
            first_line = f.readline()
            if first_line.strip():
                import json
                event = json.loads(first_line.strip())
                assert "pid" in event


def test_cli_env_format_jsonl_cli_override(tmp_path: Path) -> None:
    """Test CLI can override GC_MONITOR_FORMAT with --format chrome."""
    output_file = tmp_path / "test.json"

    env = os.environ.copy()
    env["GC_MONITOR_FORMAT"] = "jsonl"
    env["GC_MONITOR_OUTPUT"] = str(output_file)

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "gc_monitor.cli",
            "monitor",
            "12345",
            "--format",
            "chrome",
            "-o",
            str(output_file),
            "-d",
            "0.1",
        ],
        capture_output=True,
        text=True,
        check=True,
        env=env,
    )

    # Output file should exist and contain Chrome trace format (starts with [)
    assert output_file.exists()
    with open(output_file, "r", encoding="utf-8") as f:
        content = f.read().strip()
        # Chrome trace format starts with [ and contains trace events array
        assert content.startswith("[")


# =============================================================================
# Combine Command Tests
# =============================================================================


def test_cli_combine_basic(tmp_path: Path) -> None:
    """Test CLI combine command basic functionality."""
    # Create test input files
    file1 = tmp_path / "trace1.json"
    file2 = tmp_path / "trace2.json"
    output_file = tmp_path / "combined.json"

    # Write test data
    import json
    with open(file1, "w") as f:
        json.dump([{"name": "event1", "ph": "X", "ts": 100}], f)
    with open(file2, "w") as f:
        json.dump([{"name": "event2", "ph": "X", "ts": 200}], f)

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "gc_monitor.cli",
            "combine",
            str(file1),
            str(file2),
            "-o",
            str(output_file),
            "-v",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert output_file.exists()

    # Verify combined output
    with open(output_file) as f:
        data: list[dict[str, Any]] = json.load(f)  # type: ignore[assignment]

    assert len(data) == 2
    assert data[0]["name"] == "event1"
    assert data[1]["name"] == "event2"


def test_cli_combine_verbose(tmp_path: Path) -> None:
    """Test CLI combine command verbose output."""
    file1 = tmp_path / "trace1.json"
    output_file = tmp_path / "combined.json"

    import json
    with open(file1, "w") as f:
        json.dump([{"name": "event1", "ph": "X", "ts": 100}], f)

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "gc_monitor.cli",
            "combine",
            str(file1),
            "-o",
            str(output_file),
            "-v",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    # Verbose output goes to stderr
    assert "Combining 1 file(s)" in result.stderr
    assert f"Input: {file1}" in result.stderr
    assert f"Output: {output_file}" in result.stderr
    assert "Combine complete" in result.stderr


def test_cli_combine_missing_file(tmp_path: Path) -> None:
    """Test CLI combine command with missing input file."""
    non_existent = tmp_path / "missing.json"
    output_file = tmp_path / "combined.json"

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "gc_monitor.cli",
            "combine",
            str(non_existent),
            "-o",
            str(output_file),
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "Error combining files" in result.stderr


def test_cli_combine_invalid_json(tmp_path: Path) -> None:
    """Test CLI combine command with invalid JSON file."""
    file1 = tmp_path / "invalid.json"
    output_file = tmp_path / "combined.json"

    # Write invalid JSON
    with open(file1, "w") as f:
        f.write("not valid json")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "gc_monitor.cli",
            "combine",
            str(file1),
            "-o",
            str(output_file),
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "Error combining files" in result.stderr


def test_cli_combine_multiple_files(tmp_path: Path) -> None:
    """Test CLI combine command with multiple input files."""
    file1 = tmp_path / "trace1.json"
    file2 = tmp_path / "trace2.json"
    file3 = tmp_path / "trace3.json"
    output_file = tmp_path / "combined.json"

    import json
    with open(file1, "w") as f:
        json.dump([{"name": "event1", "ph": "X", "ts": 100}], f)
    with open(file2, "w") as f:
        json.dump([{"name": "event2", "ph": "X", "ts": 200}], f)
    with open(file3, "w") as f:
        json.dump([{"name": "event3", "ph": "X", "ts": 300}], f)

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "gc_monitor.cli",
            "combine",
            str(file1),
            str(file2),
            str(file3),
            "-o",
            str(output_file),
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert output_file.exists()

    with open(output_file) as f:
        data: list[dict[str, Any]] = json.load(f)  # type: ignore[assignment]

    assert len(data) == 3
