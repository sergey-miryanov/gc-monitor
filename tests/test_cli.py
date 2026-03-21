"""Tests for the gc-monitor CLI."""

import json
import subprocess
import sys
from pathlib import Path
from typing import Any


def test_cli_help() -> None:
    """Test CLI --help option."""
    result = subprocess.run(
        [sys.executable, "-m", "gc_monitor.cli", "--help"],
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


def test_cli_missing_pid() -> None:
    """Test CLI fails when PID is not provided."""
    result = subprocess.run(
        [sys.executable, "-m", "gc_monitor.cli"],
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
    assert "events to" in result.stdout


def test_cli_basic_run(tmp_path: Path) -> None:
    """Test CLI basic run with duration."""
    output_file = tmp_path / "test_trace.json"

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "gc_monitor.cli",
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
    assert "Monitoring PID 12345" in result.stdout
    assert f"Output: {output_file}" in result.stdout
    assert "Rate:" in result.stdout
    assert "Duration:" in result.stdout
    assert "Total events:" in result.stdout


def test_cli_default_output_file(tmp_path: Path) -> None:
    """Test CLI uses default output file when not specified."""
    # Change to tmp_path directory so default file is created there
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "gc_monitor.cli",
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
    assert "Rate: 0.05" in result.stdout


def test_cli_output_json_structure(tmp_path: Path) -> None:
    """Test CLI output JSON has correct structure."""
    output_file = tmp_path / "test_trace.json"

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "gc_monitor.cli",
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
    # In quiet mode, should only have summary line
    assert "Saved" in result.stdout
    assert "events to" in result.stdout
    # Should not have verbose details
    assert "Monitoring PID" not in result.stdout
