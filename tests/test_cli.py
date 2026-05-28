"""Tests for the gc-monitor CLI core (parser, logging, main routing)."""

import json
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.fixture
def gc_monitor_cli() -> list[str]:
    return [sys.executable, "-m", "gc_monitor.cli"]


@pytest.fixture
def cli_module():
    from gc_monitor import cli
    return cli


# =============================================================================
# _setup_logging Tests
# =============================================================================


class TestSetupLogging:
    @pytest.fixture(autouse=True)
    def reset_logging(self):
        import logging
        for handler in logging.root.handlers[:]:
            logging.root.removeHandler(handler)
        logging.getLogger("gc_monitor").handlers.clear()

    @pytest.mark.parametrize("verbose_count, expected_level", [
        (1, "INFO"),
        (0, "WARNING"),
        (2, "DEBUG"),
    ])
    def test_setup_logging(self, cli_module, verbose_count: int, expected_level: str) -> None:
        import logging
        cli_module._setup_logging(verbose_count=verbose_count)
        logger = logging.getLogger("gc_monitor")
        assert logger.level == getattr(logging, expected_level)


# =============================================================================
# main() Tests - Command Routing
# =============================================================================


def test_main_combine_command(tmp_path: Path) -> None:
    from gc_monitor import cli
    from gc_monitor.exporters.chrome_trace_format import process_meta

    input_file = tmp_path / "input.json"
    input_file.write_text(json.dumps([process_meta(pid=1, name="test")]))

    assert cli.main(["combine", str(input_file), "-o", str(tmp_path / "output.json")]) == 0


# =============================================================================
# CLI Help Tests
# =============================================================================


class TestCliHelp:
    @pytest.mark.parametrize("subcommand, expected_texts", [
        ("", [
            "Monitor Python's garbage collector",
            "monitor", "combine", "run",
        ]),
        ("monitor", ["pid", "--output", "--rate", "--duration", "--verbose", "--stats"]),
        ("combine", ["Combine multiple Chrome Trace Format or JSONL files", "inputs", "--output"]),
        ("run", ["Run a Python script or module", "--module", "--script", "--stats"]),
    ])
    def test_help_subcommand(self, gc_monitor_cli, subcommand: str, expected_texts: list[str]) -> None:
        cmd = gc_monitor_cli + ([subcommand] if subcommand else []) + ["--help"]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        for text in expected_texts:
            assert text in result.stdout

    def test_top_level_no_output_flag(self, gc_monitor_cli) -> None:
        result = subprocess.run(gc_monitor_cli + ["--help"], capture_output=True, text=True, check=True)
        assert "--output" not in result.stdout


class TestCliMonitor:
    def test_missing_pid(self, gc_monitor_cli) -> None:
        result = subprocess.run(gc_monitor_cli + ["monitor"], capture_output=True, text=True)
        assert result.returncode != 0
        assert "the following arguments are required: pid" in result.stderr

    def test_explicit_command(self, gc_monitor_cli) -> None:
        result = subprocess.run(
            gc_monitor_cli + ["monitor", "12345", "-d", "0.1"],
            capture_output=True, text=True, timeout=5,
        )
        assert result.returncode == 0
