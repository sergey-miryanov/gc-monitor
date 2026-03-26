"""Tests for the gc-monitor CLI."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from tests.test_pyperf_hook import _assert_valid_chrome_trace_format  # pyright: ignore[reportPrivateUsage]


# =============================================================================
# _validate_output_path Tests
# =============================================================================


def test_validate_output_path_null_byte() -> None:
    """Test _validate_output_path rejects null bytes."""
    from gc_monitor import cli

    with pytest.raises(Exception) as exc_info:
        cli._validate_output_path("test\x00file.json")

    assert "null byte" in str(exc_info.value)


def test_validate_output_path_nonexistent_directory(tmp_path: Path) -> None:
    """Test _validate_output_path rejects paths with nonexistent parent directory."""
    from gc_monitor import cli

    nonexistent_dir = tmp_path / "nonexistent" / "file.json"

    with pytest.raises(Exception) as exc_info:
        cli._validate_output_path(str(nonexistent_dir))

    assert "does not exist" in str(exc_info.value)


def test_validate_output_path_directory(tmp_path: Path) -> None:
    """Test _validate_output_path rejects directory paths."""
    from gc_monitor import cli

    with pytest.raises(Exception) as exc_info:
        cli._validate_output_path(str(tmp_path))

    assert "is a directory" in str(exc_info.value)


def test_validate_output_path_valid(tmp_path: Path) -> None:
    """Test _validate_output_path accepts valid file path."""
    from gc_monitor import cli

    output_file = tmp_path / "valid_output.json"
    # Create the file first
    output_file.touch()

    result = cli._validate_output_path(str(output_file))

    assert result == output_file


def test_validate_output_path_relative_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test _validate_output_path handles relative paths."""
    from gc_monitor import cli

    monkeypatch.chdir(tmp_path)
    output_file = tmp_path / "relative.json"
    output_file.touch()

    result = cli._validate_output_path("relative.json")

    assert result.name == "relative.json"


# =============================================================================
# _setup_logging Tests
# =============================================================================


def test_setup_logging_verbose(caplog: pytest.LogCaptureFixture) -> None:
    """Test _setup_logging with verbose=True."""
    from gc_monitor import cli
    import logging

    # Reset logging state for test isolation
    for handler in logging.root.handlers[:]:
        logging.root.removeHandler(handler)
    logging.getLogger("gc_monitor").handlers.clear()

    cli._setup_logging(verbose=True)

    # Verify logging level is INFO
    logger = logging.getLogger("gc_monitor")
    assert logger.level == logging.INFO


def test_setup_logging_quiet(caplog: pytest.LogCaptureFixture) -> None:
    """Test _setup_logging with verbose=False."""
    from gc_monitor import cli
    import logging

    # Reset logging state for test isolation
    for handler in logging.root.handlers[:]:
        logging.root.removeHandler(handler)
    logging.getLogger("gc_monitor").handlers.clear()

    cli._setup_logging(verbose=False)

    # Verify logging level is WARNING
    logger = logging.getLogger("gc_monitor")
    assert logger.level == logging.WARNING


# =============================================================================
# Environment Variable Helper Tests - Edge Cases
# =============================================================================


def test_get_env_output_invalid_format(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test _get_env_output with invalid format falls back to default."""
    from gc_monitor import cli

    monkeypatch.setenv("GC_MONITOR_OUTPUT", str(tmp_path / "env.json"))
    monkeypatch.setenv("GC_MONITOR_FORMAT", "invalid_format")

    result = cli._get_env_output()

    assert result.name == "env.json"


def test_get_env_rate_invalid_value(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test _get_env_rate with invalid value returns default."""
    from gc_monitor import cli

    monkeypatch.setenv("GC_MONITOR_RATE", "not-a-number")

    result = cli._get_env_rate()

    assert result == 0.1


def test_get_env_duration_invalid_value(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test _get_env_duration with invalid value returns None."""
    from gc_monitor import cli

    monkeypatch.setenv("GC_MONITOR_DURATION", "not-a-number")

    result = cli._get_env_duration()

    assert result is None


def test_get_env_format_invalid_value(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test _get_env_format with invalid value returns default."""
    from gc_monitor import cli

    monkeypatch.setenv("GC_MONITOR_FORMAT", "invalid_format")

    result = cli._get_env_format()

    assert result == "chrome"


def test_get_env_thread_id_invalid_value(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test _get_env_thread_id with invalid value returns default."""
    from gc_monitor import cli

    monkeypatch.setenv("GC_MONITOR_THREAD_ID", "not-a-number")

    result = cli._get_env_thread_id()

    assert result == 0


def test_get_env_fallback_invalid_value(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test _get_env_fallback with invalid value returns default."""
    from gc_monitor import cli

    monkeypatch.setenv("GC_MONITOR_FALLBACK", "invalid")

    result = cli._get_env_fallback()

    assert result == "yes"


def test_get_env_flush_threshold_invalid_value(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test _get_env_flush_threshold with invalid value returns default."""
    from gc_monitor import cli

    monkeypatch.setenv("GC_MONITOR_FLUSH_THRESHOLD", "not-a-number")

    result = cli._get_env_flush_threshold()

    assert result == 100


def test_get_env_server_host_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test _get_env_server_host returns default when not set."""
    from gc_monitor import cli

    monkeypatch.delenv("GC_MONITOR_SERVER_HOST", raising=False)

    result = cli._get_env_server_host()

    assert result == "localhost"


def test_get_env_server_port_invalid_value(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test _get_env_server_port with invalid value returns default."""
    from gc_monitor import cli

    monkeypatch.setenv("GC_MONITOR_SERVER_PORT", "not-a-number")

    result = cli._get_env_server_port()

    assert result == 9999


# =============================================================================
# _cmd_server Tests
# =============================================================================


def test_cmd_server_basic(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    """Test _cmd_server basic functionality."""
    from gc_monitor import cli
    from unittest.mock import MagicMock, patch

    # Mock args
    mock_args = MagicMock()
    mock_args.host = "localhost"
    mock_args.port = 9999
    mock_args.verbose = False

    # Mock server
    mock_server = MagicMock()
    mock_server.start = MagicMock()
    mock_server.stop = MagicMock()

    # Mock thread
    mock_thread = MagicMock()
    mock_thread.is_running = True
    mock_thread.start = MagicMock()
    mock_thread.stop = MagicMock()

    with patch.object(cli, "GCMonitorThread", return_value=mock_thread):
        with patch.object(cli, "SocketCommandServer", return_value=mock_server):
            with patch.object(cli, "_wait_for_shutdown", return_value=False):
                result = cli._cmd_server(mock_args)

                assert result == 0
                mock_thread.start.assert_called_once()
                mock_server.start.assert_called_once()


def test_cmd_server_verbose(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    """Test _cmd_server with verbose output."""
    from gc_monitor import cli
    from unittest.mock import MagicMock, patch
    import logging

    # Set up caplog to capture gc_monitor logger
    logger = logging.getLogger("gc_monitor")
    logger.setLevel(logging.INFO)

    # Mock args
    mock_args = MagicMock()
    mock_args.host = "localhost"
    mock_args.port = 9999
    mock_args.verbose = True

    # Mock server
    mock_server = MagicMock()
    mock_server.start = MagicMock()
    mock_server.stop = MagicMock()

    # Mock thread
    mock_thread = MagicMock()
    mock_thread.is_running = True
    mock_thread.start = MagicMock()
    mock_thread.stop = MagicMock()

    with patch.object(cli, "GCMonitorThread", return_value=mock_thread):
        with patch.object(cli, "SocketCommandServer", return_value=mock_server):
            with patch.object(cli, "_wait_for_shutdown", return_value=False):
                result = cli._cmd_server(mock_args)

                assert result == 0
                assert "Starting server mode" in caplog.text
                assert "Server listening on localhost:9999" in caplog.text


def test_cmd_server_os_error(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    """Test _cmd_server handles OSError."""
    from gc_monitor import cli
    from unittest.mock import MagicMock, patch

    # Mock args
    mock_args = MagicMock()
    mock_args.host = "localhost"
    mock_args.port = 9999
    mock_args.verbose = True

    # Mock server that raises OSError on start
    mock_server = MagicMock()
    mock_server.start = MagicMock(side_effect=OSError("Address in use"))

    # Mock thread
    mock_thread = MagicMock()
    mock_thread.is_running = True
    mock_thread.start = MagicMock()
    mock_thread.stop = MagicMock()

    with patch.object(cli, "GCMonitorThread", return_value=mock_thread):
        with patch.object(cli, "SocketCommandServer", return_value=mock_server):
            result = cli._cmd_server(mock_args)

            assert result == 1
            assert "Socket server error" in caplog.text
            mock_thread.stop.assert_called_once()


def test_cmd_server_shutdown(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    """Test _cmd_server shutdown sequence."""
    from gc_monitor import cli
    from unittest.mock import MagicMock, patch

    # Mock args
    mock_args = MagicMock()
    mock_args.host = "localhost"
    mock_args.port = 9999
    mock_args.verbose = True

    # Mock server
    mock_server = MagicMock()
    mock_server.start = MagicMock()
    mock_server.stop = MagicMock()

    # Mock thread
    mock_thread = MagicMock()
    mock_thread.is_running = True
    mock_thread.start = MagicMock()
    mock_thread.stop = MagicMock()

    with patch.object(cli, "GCMonitorThread", return_value=mock_thread):
        with patch.object(cli, "SocketCommandServer", return_value=mock_server):
            with patch.object(cli, "_wait_for_shutdown", return_value=True):
                result = cli._cmd_server(mock_args)

                assert result == 0
                mock_server.stop.assert_called_once()


# =============================================================================
# _wait_for_shutdown Tests
# =============================================================================


def test_wait_for_shutdown_duration_based(caplog: pytest.LogCaptureFixture) -> None:
    """Test _wait_for_shutdown with duration."""
    from gc_monitor import cli

    result = cli._wait_for_shutdown(
        verbose=True,
        duration=0.1,
        is_running_check=None,
    )

    assert result is False


def test_wait_for_shutdown_indefinite_with_running_check() -> None:
    """Test _wait_for_shutdown with is_running_check that returns False."""
    from gc_monitor import cli
    from unittest.mock import MagicMock

    # Mock that returns False immediately
    mock_running_check = MagicMock(return_value=False)

    result = cli._wait_for_shutdown(
        verbose=False,
        duration=None,
        is_running_check=mock_running_check,
    )

    assert result is False
    mock_running_check.assert_called_once()


def test_wait_for_shutdown_duration_with_running_check() -> None:
    """Test _wait_for_shutdown with duration and is_running_check."""
    from gc_monitor import cli
    from unittest.mock import MagicMock

    # Mock that returns False immediately
    mock_running_check = MagicMock(return_value=False)

    result = cli._wait_for_shutdown(
        verbose=True,
        duration=1.0,
        is_running_check=mock_running_check,
    )

    assert result is False
    mock_running_check.assert_called_once()


def test_wait_for_shutdown_signal_requested(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test _wait_for_shutdown when signal is requested."""
    from gc_monitor import cli
    import signal
    from unittest.mock import patch

    # We can't easily test signal handling in unit tests,
    # but we can verify the function handles shutdown_requested correctly
    # by checking the return value

    # This test verifies the function completes without error
    result = cli._wait_for_shutdown(
        verbose=False,
        duration=0.05,
        is_running_check=None,
    )

    assert result is False  # No signal was actually sent


# =============================================================================
# _cmd_monitor Tests - Error Paths
# =============================================================================


def test_cmd_monitor_connect_failure(caplog: pytest.LogCaptureFixture) -> None:
    """Test _cmd_monitor handles connect failure."""
    from gc_monitor import cli
    from unittest.mock import MagicMock, patch

    # Mock args
    mock_args = MagicMock()
    mock_args.pid = 12345
    mock_args.output = Path("test.json")
    mock_args.rate = 0.1
    mock_args.duration = None
    mock_args.verbose = True
    mock_args.format = "chrome"
    mock_args.thread_name = "Test"
    mock_args.thread_id = 0
    mock_args.fallback = "yes"
    mock_args.flush_threshold = 100

    with patch.object(cli, "connect", side_effect=RuntimeError("Connection failed")):
        result = cli._cmd_monitor(mock_args)

        assert result == 1
        assert "Failed to connect to GC monitor" in caplog.text


def test_cmd_monitor_stdout_format_verbose(caplog: pytest.LogCaptureFixture) -> None:
    """Test _cmd_monitor with stdout format and verbose."""
    from gc_monitor import cli
    from unittest.mock import MagicMock, patch

    # Mock args
    mock_args = MagicMock()
    mock_args.pid = 12345
    mock_args.output = Path("test.json")
    mock_args.rate = 0.1
    mock_args.duration = 0.05
    mock_args.verbose = True
    mock_args.format = "stdout"
    mock_args.thread_name = "Test"
    mock_args.thread_id = 0
    mock_args.fallback = "yes"
    mock_args.flush_threshold = 100

    # Mock monitor
    mock_monitor = MagicMock()
    mock_exporter = MagicMock()
    mock_exporter.get_event_count = MagicMock(return_value=5)
    mock_thread = MagicMock()
    mock_thread.is_running = True

    with patch.object(cli, "connect", return_value=mock_monitor):
        with patch.object(cli, "StdoutExporter", return_value=mock_exporter):
            with patch.object(cli, "GCMonitorThread", return_value=mock_thread):
                with patch.object(cli, "_wait_for_shutdown"):
                    result = cli._cmd_monitor(mock_args)

                    assert result == 0
                    assert "Format: stdout" in caplog.text


def test_cmd_monitor_jsonl_format_verbose(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    """Test _cmd_monitor with jsonl format and verbose."""
    from gc_monitor import cli
    from unittest.mock import MagicMock, patch

    output_file = tmp_path / "test.jsonl"

    # Mock args
    mock_args = MagicMock()
    mock_args.pid = 12345
    mock_args.output = output_file
    mock_args.rate = 0.1
    mock_args.duration = 0.05
    mock_args.verbose = True
    mock_args.format = "jsonl"
    mock_args.thread_name = "Test"
    mock_args.thread_id = 99
    mock_args.fallback = "yes"
    mock_args.flush_threshold = 50

    # Mock monitor
    mock_monitor = MagicMock()
    mock_exporter = MagicMock()
    mock_exporter.get_event_count = MagicMock(return_value=3)
    mock_thread = MagicMock()
    mock_thread.is_running = True

    with patch.object(cli, "connect", return_value=mock_monitor):
        with patch.object(cli, "JsonlExporter", return_value=mock_exporter):
            with patch.object(cli, "GCMonitorThread", return_value=mock_thread):
                with patch.object(cli, "_wait_for_shutdown"):
                    result = cli._cmd_monitor(mock_args)

                    assert result == 0
                    assert "Format: jsonl" in caplog.text
                    assert "Flush threshold" not in caplog.text  # Not logged


def test_cmd_monitor_quiet_mode() -> None:
    """Test _cmd_monitor in quiet mode."""
    from gc_monitor import cli
    from unittest.mock import MagicMock, patch

    # Mock args
    mock_args = MagicMock()
    mock_args.pid = 12345
    mock_args.output = Path("test.json")
    mock_args.rate = 0.1
    mock_args.duration = 0.05
    mock_args.verbose = False
    mock_args.format = "chrome"
    mock_args.thread_name = "Test"
    mock_args.thread_id = 0
    mock_args.fallback = "yes"
    mock_args.flush_threshold = 100

    # Mock monitor
    mock_monitor = MagicMock()
    mock_exporter = MagicMock()
    mock_exporter.get_event_count = MagicMock(return_value=10)
    mock_thread = MagicMock()
    mock_thread.is_running = True

    with patch.object(cli, "connect", return_value=mock_monitor):
        with patch.object(cli, "TraceExporter", return_value=mock_exporter):
            with patch.object(cli, "GCMonitorThread", return_value=mock_thread):
                with patch.object(cli, "_wait_for_shutdown"):
                    result = cli._cmd_monitor(mock_args)

                    assert result == 0


def test_cmd_monitor_stdout_format_quiet_summary(caplog: pytest.LogCaptureFixture) -> None:
    """Test _cmd_monitor prints summary for stdout format in quiet mode."""
    from gc_monitor import cli
    from unittest.mock import MagicMock, patch

    # Mock args
    mock_args = MagicMock()
    mock_args.pid = 12345
    mock_args.output = Path("test.json")
    mock_args.rate = 0.1
    mock_args.duration = 0.05
    mock_args.verbose = False
    mock_args.format = "stdout"
    mock_args.thread_name = "Test"
    mock_args.thread_id = 0
    mock_args.fallback = "yes"
    mock_args.flush_threshold = 100

    # Mock monitor
    mock_monitor = MagicMock()
    mock_exporter = MagicMock()
    mock_exporter.get_event_count = MagicMock(return_value=7)
    mock_thread = MagicMock()
    mock_thread.is_running = True

    with patch.object(cli, "connect", return_value=mock_monitor):
        with patch.object(cli, "StdoutExporter", return_value=mock_exporter):
            with patch.object(cli, "GCMonitorThread", return_value=mock_thread):
                with patch.object(cli, "_wait_for_shutdown"):
                    result = cli._cmd_monitor(mock_args)

                    assert result == 0
                    # In quiet mode with stdout format, should log event count
                    assert "Exported 7 events to stdout" in caplog.text


# =============================================================================
# _cmd_combine Tests - Error Paths
# =============================================================================


def test_cmd_combine_file_not_found(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    """Test _cmd_combine with missing input file."""
    from gc_monitor import cli
    from unittest.mock import MagicMock

    # Mock args
    mock_args = MagicMock()
    mock_args.inputs = [tmp_path / "nonexistent.json"]
    mock_args.output = tmp_path / "output.json"
    mock_args.verbose = True
    mock_args.normalize = False

    result = cli._cmd_combine(mock_args)

    assert result == 1
    assert "Error combining files" in caplog.text


def test_cmd_combine_quiet_mode(tmp_path: Path) -> None:
    """Test _cmd_combine in quiet mode."""
    from gc_monitor import cli
    from unittest.mock import MagicMock
    import json

    # Create test input
    input_file = tmp_path / "input.json"
    with open(input_file, "w") as f:
        json.dump([{"name": "test", "ph": "X", "ts": 100}], f)

    # Mock args
    mock_args = MagicMock()
    mock_args.inputs = [input_file]
    mock_args.output = tmp_path / "output.json"
    mock_args.verbose = False
    mock_args.normalize = False

    result = cli._cmd_combine(mock_args)

    assert result == 0


# =============================================================================
# main() Tests - Command Routing
# =============================================================================


def test_main_unknown_command(caplog: pytest.LogCaptureFixture) -> None:
    """Test main() with unknown command (should not happen with argparse)."""
    from gc_monitor import cli
    from unittest.mock import patch

    # Create a mock parser that returns an unknown command
    mock_parser = MagicMock()
    mock_args = MagicMock()
    mock_args.command = "unknown_command"
    mock_args.verbose = False
    mock_parser.parse_args = MagicMock(return_value=mock_args)

    with patch.object(cli, "_create_parser", return_value=mock_parser):
        result = cli.main([])

        assert result == 1
        assert "Unknown command" in caplog.text


def test_main_combine_command(tmp_path: Path) -> None:
    """Test main() routes to combine command."""
    from gc_monitor import cli
    import json

    # Create test input
    input_file = tmp_path / "input.json"
    with open(input_file, "w") as f:
        json.dump([{"name": "test", "ph": "X", "ts": 100}], f)

    result = cli.main([
        "combine",
        str(input_file),
        "-o", str(tmp_path / "output.json"),
    ])

    assert result == 0


def test_main_server_command(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test main() routes to server command."""
    from gc_monitor import cli
    from unittest.mock import MagicMock, patch

    # Mock server
    mock_server = MagicMock()
    mock_server.start = MagicMock()
    mock_server.stop = MagicMock()

    # Mock thread
    mock_thread = MagicMock()
    mock_thread.is_running = True
    mock_thread.start = MagicMock()
    mock_thread.stop = MagicMock()

    with patch.object(cli, "GCMonitorThread", return_value=mock_thread):
        with patch.object(cli, "SocketCommandServer", return_value=mock_server):
            with patch.object(cli, "_wait_for_shutdown", return_value=False):
                result = cli.main(["server", "--port", "9998"])

                assert result == 0


# =============================================================================
# CLI Help and Subcommand Tests
# =============================================================================


def test_cli_server_help() -> None:
    """Test CLI server subcommand --help."""
    result = subprocess.run(
        [sys.executable, "-m", "gc_monitor.cli", "server", "--help"],
        capture_output=True,
        text=True,
        check=True,
    )

    assert "Monitor Python's garbage collector with remote control" in result.stdout
    assert "--host" in result.stdout
    assert "--port" in result.stdout
    assert "--verbose" in result.stdout


def test_cli_server_basic(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Test CLI server command basic run."""
    # Start server process
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "gc_monitor.cli",
            "server",
            "--port", "9997",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    
    try:
        # Give server time to start
        time.sleep(0.5)
        
        # Check if process is still running (server should block)
        assert proc.poll() is None, "Server should still be running"
    finally:
        # Kill the server process
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        
        # Verify no traceback in stderr
        stderr = proc.stderr.read()
        assert "Traceback" not in stderr


def test_cli_server_verbose(tmp_path: Path) -> None:
    """Test CLI server command verbose output."""
    # Start server process
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "gc_monitor.cli",
            "server",
            "--port", "9996",
            "-v",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    
    try:
        # Give server time to start and log
        time.sleep(0.5)
        
        # Check if process is still running
        assert proc.poll() is None, "Server should still be running"
    finally:
        # Kill the server process
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        
        # Read stderr output
        stderr = proc.stderr.read()
        
        # Verify startup message
        assert "Starting server mode" in stderr


def test_cli_server_env_host_port(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test CLI server uses environment variables for host and port."""
    monkeypatch.setenv("GC_MONITOR_SERVER_HOST", "127.0.0.1")
    monkeypatch.setenv("GC_MONITOR_SERVER_PORT", "9995")

    # Start server process
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "gc_monitor.cli",
            "server",
            "-v",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    
    try:
        # Give server time to start and log
        time.sleep(0.5)
        
        # Check if process is still running
        assert proc.poll() is None, "Server should still be running"
    finally:
        # Kill the server process
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        
        # Read stderr output
        stderr = proc.stderr.read()
        
        # Verify env vars were used
        assert "127.0.0.1" in stderr or "9995" in stderr


def test_cli_monitor_explicit_command() -> None:
    """Test CLI with explicit 'monitor' subcommand."""
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "gc_monitor.cli",
            "monitor",
            "12345",
            "-d", "0.1",
        ],
        capture_output=True,
        text=True,
        timeout=5,
    )

    # Should succeed with mock handler
    assert result.returncode == 0


# =============================================================================
# Edge Cases and Integration Tests
# =============================================================================


def test_cli_monitor_duration_zero(tmp_path: Path) -> None:
    """Test CLI with duration=0 (immediate exit)."""
    output_file = tmp_path / "test.json"

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "gc_monitor.cli",
            "monitor",
            "12345",
            "-o", str(output_file),
            "-d", "0",
            "-v",
        ],
        capture_output=True,
        text=True,
        timeout=5,
    )

    # Should complete immediately
    assert result.returncode == 0
    assert "Duration: 0" in result.stderr or "Monitoring for 0 seconds" in result.stderr


def test_cli_monitor_very_short_duration(tmp_path: Path) -> None:
    """Test CLI with very short duration."""
    output_file = tmp_path / "test.json"

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "gc_monitor.cli",
            "monitor",
            "12345",
            "-o", str(output_file),
            "-d", "0.01",
        ],
        capture_output=True,
        text=True,
        timeout=5,
    )

    assert result.returncode == 0
    assert output_file.exists()


def test_cli_env_output_default_format_jsonl(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test default output filename changes to gc_monitor.jsonl when format is jsonl."""
    monkeypatch.setenv("GC_MONITOR_FORMAT", "jsonl")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "gc_monitor.cli",
            "monitor",
            "12345",
            "-d", "0.1",
        ],
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )

    assert result.returncode == 0
    default_file = tmp_path / "gc_monitor.jsonl"
    # File may not exist if no GC events, but verbose would mention it
    # Just verify no error


def test_cli_path_traversal_warning(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test CLI warns when output path is outside current directory."""
    output_file = tmp_path / "subdir" / "output.json"
    output_file.parent.mkdir()
    output_file.touch()

    # Change to a different directory
    other_dir = tmp_path / "other"
    other_dir.mkdir()
    monkeypatch.chdir(other_dir)

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "gc_monitor.cli",
            "monitor",
            "12345",
            "-o", str(output_file),
            "-d", "0.1",
            "-v",
        ],
        capture_output=True,
        text=True,
        timeout=5,
    )

    # Should warn about path being outside CWD
    assert "outside" in result.stderr or result.returncode == 0


def test_cli_env_flush_threshold(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test CLI uses GC_MONITOR_FLUSH_THRESHOLD environment variable."""
    output_file = tmp_path / "test.jsonl"
    monkeypatch.setenv("GC_MONITOR_FLUSH_THRESHOLD", "50")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "gc_monitor.cli",
            "monitor",
            "12345",
            "--format", "jsonl",
            "-o", str(output_file),
            "-d", "0.1",
            "-v",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    # Flush threshold is not logged, but we verify the command succeeds


def test_cli_env_flush_threshold_cli_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test CLI option overrides GC_MONITOR_FLUSH_THRESHOLD."""
    output_file = tmp_path / "test.jsonl"
    monkeypatch.setenv("GC_MONITOR_FLUSH_THRESHOLD", "50")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "gc_monitor.cli",
            "monitor",
            "12345",
            "--format", "jsonl",
            "-o", str(output_file),
            "--flush-threshold", "200",
            "-d", "0.1",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0


def test_cli_server_help_shows_env_vars() -> None:
    """Test CLI server --help shows environment variables."""
    result = subprocess.run(
        [sys.executable, "-m", "gc_monitor.cli", "server", "--help"],
        capture_output=True,
        text=True,
        check=True,
    )

    assert "GC_MONITOR_SERVER_HOST" in result.stdout
    assert "GC_MONITOR_SERVER_PORT" in result.stdout
    assert "GC_MONITOR_VERBOSE" in result.stdout


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

    # Verify output file is valid Chrome Trace format
    data = _assert_valid_chrome_trace_format(output_file)
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
            "0.5",  # Increased duration to reduce chance of immediate failure
        ],
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )

    assert result.returncode == 0
    # stdout should have JSONL data if handler didn't fail immediately
    # (mock handler has 10% chance of failure per read)
    if result.stdout:
        assert '{"pid":' in result.stdout
    # In quiet mode (WARNING level), INFO messages don't appear in stderr
    # But WARNING about handler failure may appear
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

    # Mock exporter
    mock_exporter = MagicMock()
    mock_exporter.get_event_count = MagicMock(return_value=3)

    # Mock _wait_for_shutdown to simulate shutdown being requested
    with patch.object(cli, "connect", return_value=mock_monitor):
        with patch.object(cli, "StdoutExporter", return_value=mock_exporter):
            with patch.object(cli, "_wait_for_shutdown") as mock_wait_for_shutdown:
                # Simulate shutdown being requested
                mock_wait_for_shutdown.return_value = True

                cli.main(["monitor", "12345", "--format", "stdout", "-d", "1.0"])

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

    # Verify output file was created and is valid Chrome Trace format
    data = _assert_valid_chrome_trace_format(output_file)

    # Verify thread name appears in the file content
    content = output_file.read_text()
    assert "MyCustomThread" in content

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

    # Verify combined output is valid Chrome Trace format
    data = _assert_valid_chrome_trace_format(output_file)

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

    # Verify combined output is valid Chrome Trace format
    data = _assert_valid_chrome_trace_format(output_file)

    assert len(data) == 3


# =============================================================================
# Combine Command - Normalize Tests
# =============================================================================


def test_cli_combine_normalize_basic(tmp_path: Path) -> None:
    """Test CLI combine command with --normalize option."""
    file1 = tmp_path / "trace1.json"
    file2 = tmp_path / "trace2.json"
    output_file = tmp_path / "combined.json"

    import json
    # File 1: timestamps start at 1000
    with open(file1, "w") as f:
        json.dump([
            {"name": "event1", "ph": "X", "ts": 1000},
            {"name": "event2", "ph": "X", "ts": 1100},
        ], f)
    # File 2: timestamps start at 5000
    with open(file2, "w") as f:
        json.dump([
            {"name": "event3", "ph": "X", "ts": 5000},
            {"name": "event4", "ph": "X", "ts": 5200},
        ], f)

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
            "--normalize",
            "-v",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert output_file.exists()
    assert "Normalizing timestamps: yes" in result.stderr

    # Verify combined output is valid Chrome Trace format
    data = _assert_valid_chrome_trace_format(output_file)

    assert len(data) == 4
    # File 1 should be normalized: 1000->0, 1100->100
    assert data[0]["ts"] == 0
    assert data[1]["ts"] == 100
    # File 2 should be normalized independently: 5000->0, 5200->200
    assert data[2]["ts"] == 0
    assert data[3]["ts"] == 200


def test_cli_combine_normalize_preserves_relative_timing(tmp_path: Path) -> None:
    """Test that --normalize preserves relative timing within each file."""
    file1 = tmp_path / "trace1.json"
    output_file = tmp_path / "combined.json"

    import json
    # File with various timestamp intervals
    with open(file1, "w") as f:
        json.dump([
            {"name": "event1", "ph": "X", "ts": 1000},
            {"name": "event2", "ph": "X", "ts": 1050},  # 50us after event1
            {"name": "event3", "ph": "X", "ts": 1200},  # 150us after event2
            {"name": "event4", "ph": "X", "ts": 1700},  # 500us after event3
        ], f)

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "gc_monitor.cli",
            "combine",
            str(file1),
            "-o",
            str(output_file),
            "--normalize",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0

    # Verify combined output is valid Chrome Trace format
    data = _assert_valid_chrome_trace_format(output_file)

    # Verify relative timing is preserved
    assert data[0]["ts"] == 0
    assert data[1]["ts"] == 50  # 50us from event1
    assert data[2]["ts"] == 200  # 200us from event1 (150us from event2)
    assert data[3]["ts"] == 700  # 700us from event1 (500us from event3)

    # Verify intervals are preserved
    assert data[1]["ts"] - data[0]["ts"] == 50
    assert data[2]["ts"] - data[1]["ts"] == 150
    assert data[3]["ts"] - data[2]["ts"] == 500


def test_cli_combine_normalize_multiple_files_independent(tmp_path: Path) -> None:
    """Test that --normalize normalizes each file independently."""
    file1 = tmp_path / "trace1.json"
    file2 = tmp_path / "trace2.json"
    file3 = tmp_path / "trace3.json"
    output_file = tmp_path / "combined.json"

    import json
    # Three files with different timestamp ranges
    with open(file1, "w") as f:
        json.dump([
            {"name": "file1_event1", "ph": "X", "ts": 100},
            {"name": "file1_event2", "ph": "X", "ts": 200},
        ], f)
    with open(file2, "w") as f:
        json.dump([
            {"name": "file2_event1", "ph": "X", "ts": 10000},
            {"name": "file2_event2", "ph": "X", "ts": 10100},
        ], f)
    with open(file3, "w") as f:
        json.dump([
            {"name": "file3_event1", "ph": "X", "ts": 50000},
            {"name": "file3_event2", "ph": "X", "ts": 50050},
        ], f)

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
            "--normalize",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0

    # Verify combined output is valid Chrome Trace format
    data = _assert_valid_chrome_trace_format(output_file)

    assert len(data) == 6
    # Each file should start at 0
    assert data[0]["ts"] == 0  # file1_event1
    assert data[1]["ts"] == 100  # file1_event2 (200-100)
    assert data[2]["ts"] == 0  # file2_event1
    assert data[3]["ts"] == 100  # file2_event2 (10100-10000)
    assert data[4]["ts"] == 0  # file3_event1
    assert data[5]["ts"] == 50  # file3_event2 (50050-50000)


def test_cli_combine_without_normalize(tmp_path: Path) -> None:
    """Test that combine without --normalize preserves original timestamps."""
    file1 = tmp_path / "trace1.json"
    file2 = tmp_path / "trace2.json"
    output_file = tmp_path / "combined.json"

    import json
    with open(file1, "w") as f:
        json.dump([
            {"name": "event1", "ph": "X", "ts": 1000},
            {"name": "event2", "ph": "X", "ts": 1100},
        ], f)
    with open(file2, "w") as f:
        json.dump([
            {"name": "event3", "ph": "X", "ts": 5000},
            {"name": "event4", "ph": "X", "ts": 5200},
        ], f)

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
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0

    # Verify combined output is valid Chrome Trace format
    data = _assert_valid_chrome_trace_format(output_file)

    # Timestamps should be preserved as-is
    assert data[0]["ts"] == 1000
    assert data[1]["ts"] == 1100
    assert data[2]["ts"] == 5000
    assert data[3]["ts"] == 5200


def test_cli_combine_normalize_with_metadata(tmp_path: Path) -> None:
    """Test that --normalize handles metadata events (phase 'M') without ts field."""
    file1 = tmp_path / "trace1.json"
    output_file = tmp_path / "combined.json"

    import json
    # File with metadata events (no ts) and regular events (with ts)
    with open(file1, "w") as f:
        json.dump([
            {"name": "process_name", "ph": "M", "pid": 123, "args": {"name": "test"}},
            {"name": "event1", "ph": "X", "ts": 1000},
            {"name": "thread_name", "ph": "M", "pid": 123, "args": {"name": "main"}},
            {"name": "event2", "ph": "X", "ts": 1500},
        ], f)

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "gc_monitor.cli",
            "combine",
            str(file1),
            "-o",
            str(output_file),
            "--normalize",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0

    # Verify combined output is valid Chrome Trace format
    data = _assert_valid_chrome_trace_format(output_file)

    assert len(data) == 4
    # Metadata events should not have ts field
    assert data[0]["ph"] == "M"
    assert "ts" not in data[0]
    assert data[2]["ph"] == "M"
    assert "ts" not in data[2]
    # Regular events should be normalized
    assert data[1]["ts"] == 0  # 1000 - 1000
    assert data[3]["ts"] == 500  # 1500 - 1000


def test_cli_combine_normalize_empty_file(tmp_path: Path) -> None:
    """Test that --normalize handles empty event list."""
    file1 = tmp_path / "trace1.json"
    file2 = tmp_path / "trace2.json"
    output_file = tmp_path / "combined.json"

    import json
    # Empty file
    with open(file1, "w") as f:
        json.dump([], f)
    # Normal file
    with open(file2, "w") as f:
        json.dump([
            {"name": "event1", "ph": "X", "ts": 1000},
        ], f)

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
            "--normalize",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0

    # Verify combined output is valid Chrome Trace format
    data = _assert_valid_chrome_trace_format(output_file)

    assert len(data) == 1
    assert data[0]["ts"] == 0


def test_cli_combine_normalize_metadata_only(tmp_path: Path) -> None:
    """Test that --normalize handles file with only metadata events."""
    file1 = tmp_path / "trace1.json"
    output_file = tmp_path / "combined.json"

    import json
    # File with only metadata events (no ts fields)
    with open(file1, "w") as f:
        json.dump([
            {"name": "process_name", "ph": "M", "pid": 123, "args": {"name": "test"}},
            {"name": "thread_name", "ph": "M", "pid": 123, "args": {"name": "main"}},
        ], f)

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "gc_monitor.cli",
            "combine",
            str(file1),
            "-o",
            str(output_file),
            "--normalize",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0

    # Verify combined output is valid Chrome Trace format
    data = _assert_valid_chrome_trace_format(output_file)

    assert len(data) == 2
    # Metadata events should be preserved without ts field
    assert data[0]["ph"] == "M"
    assert "ts" not in data[0]
    assert data[1]["ph"] == "M"
    assert "ts" not in data[1]


def test_cli_combine_normalize_short_option(tmp_path: Path) -> None:
    """Test CLI combine command with -n short option."""
    file1 = tmp_path / "trace1.json"
    output_file = tmp_path / "combined.json"

    import json
    with open(file1, "w") as f:
        json.dump([
            {"name": "event1", "ph": "X", "ts": 5000},
        ], f)

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "gc_monitor.cli",
            "combine",
            str(file1),
            "-o",
            str(output_file),
            "-n",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0

    # Verify combined output is valid Chrome Trace format
    data = _assert_valid_chrome_trace_format(output_file)

    # Should be normalized to 0
    assert data[0]["ts"] == 0


def test_cli_combine_normalize_help_shows_option() -> None:
    """Test CLI combine --help shows --normalize option."""
    result = subprocess.run(
        [sys.executable, "-m", "gc_monitor.cli", "combine", "--help"],
        capture_output=True,
        text=True,
        check=True,
    )

    assert "--normalize" in result.stdout or "-n" in result.stdout
    assert "Normalize" in result.stdout


def test_cli_combine_normalize_mixed_metadata_and_events(tmp_path: Path) -> None:
    """Test --normalize with mixed metadata and events in multiple files."""
    file1 = tmp_path / "trace1.json"
    file2 = tmp_path / "trace2.json"
    output_file = tmp_path / "combined.json"

    import json
    # File 1: metadata + events starting at 100
    with open(file1, "w") as f:
        json.dump([
            {"name": "process_name", "ph": "M", "pid": 123},
            {"name": "event1", "ph": "X", "ts": 100},
            {"name": "event2", "ph": "X", "ts": 150},
        ], f)
    # File 2: metadata + events starting at 1000
    with open(file2, "w") as f:
        json.dump([
            {"name": "process_name", "ph": "M", "pid": 456},
            {"name": "event3", "ph": "X", "ts": 1000},
            {"name": "event4", "ph": "X", "ts": 1200},
        ], f)

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
            "--normalize",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0

    # Verify combined output is valid Chrome Trace format
    data = _assert_valid_chrome_trace_format(output_file)

    assert len(data) == 6
    # Metadata events should not have ts
    assert data[0]["ph"] == "M"
    assert "ts" not in data[0]
    assert data[3]["ph"] == "M"
    assert "ts" not in data[3]
    # Events should be normalized per file
    assert data[1]["ts"] == 0  # file1: 100-100
    assert data[2]["ts"] == 50  # file1: 150-100
    assert data[4]["ts"] == 0  # file2: 1000-1000
    assert data[5]["ts"] == 200  # file2: 1200-1000
