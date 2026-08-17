import importlib.metadata
import subprocess
import sys
import types
from pathlib import Path

import msgspec
import pytest


@pytest.fixture
def gcmon_cli() -> list[str]:
    return [sys.executable, "-m", "gcmon.cli"]


@pytest.fixture
def cli_module() -> types.ModuleType:
    from gcmon import cli

    return cli


# =============================================================================
# _setup_logging Tests
# =============================================================================


class TestSetupLogging:
    @pytest.fixture(autouse=True)
    def reset_logging(self) -> None:
        import logging

        for handler in logging.root.handlers[:]:
            logging.root.removeHandler(handler)
        logging.getLogger("gcmon").handlers.clear()

    @pytest.mark.parametrize(
        "verbose_count, expected_level",
        [
            (1, "INFO"),
            (0, "WARNING"),
            (2, "DEBUG"),
        ],
    )
    def test_setup_logging(self, cli_module: types.ModuleType, verbose_count: int, expected_level: str) -> None:
        import logging

        cli_module._setup_logging(verbose_count=verbose_count)
        logger = logging.getLogger("gcmon")
        assert logger.level == getattr(logging, expected_level)


# =============================================================================
# main() Tests - Command Routing
# =============================================================================


def test_main_combine_command(tmp_path: Path) -> None:
    from gcmon import cli
    from gcmon.trace_event import process_meta

    input_file = tmp_path / "input.json"
    input_file.write_bytes(msgspec.json.encode([process_meta(pid=1, name="test")]))

    assert cli.main(["combine", str(input_file), "-o", str(tmp_path / "output.json")]) == 0


# =============================================================================
# CLI Help Tests
# =============================================================================


class TestCliHelp:
    @pytest.mark.parametrize(
        "subcommand, expected_texts",
        [
            (
                "",
                [
                    "Monitor Python's garbage collector",
                    "monitor",
                    "combine",
                    "run",
                ],
            ),
            (
                "monitor",
                [
                    "pid",
                    "--output",
                    "--rate",
                    "--duration",
                    "--verbose",
                    "--stats",
                    "--control-name",
                    "--rss",
                    "--rss-interval",
                ],
            ),
            ("combine", ["Combine multiple Chrome Trace Format or JSONL files", "inputs", "--output"]),
            (
                "run",
                [
                    "Run a Python script or module",
                    "--module",
                    "--script",
                    "--stats",
                    "--control-name",
                    "--rss",
                    "--rss-interval",
                ],
            ),
        ],
    )
    def test_help_subcommand(self, gcmon_cli: list[str], subcommand: str, expected_texts: list[str]) -> None:
        cmd = gcmon_cli + ([subcommand] if subcommand else []) + ["--help"]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        for text in expected_texts:
            assert text in result.stdout

    def test_top_level_no_output_flag(self, gcmon_cli: list[str]) -> None:
        result = subprocess.run([*gcmon_cli, "--help"], capture_output=True, text=True, check=True)
        assert "--output" not in result.stdout


class TestCliVersion:
    def test_version_flag(self, gcmon_cli: list[str]) -> None:
        result = subprocess.run([*gcmon_cli, "--version"], capture_output=True, text=True)
        assert result.returncode == 0
        assert result.stdout.strip() == importlib.metadata.version("gcmon")

    def test_package_attribute_matches_cli(self, gcmon_cli: list[str]) -> None:
        import gcmon

        result = subprocess.run([*gcmon_cli, "--version"], capture_output=True, text=True, check=True)
        assert gcmon.__version__ == result.stdout.strip()

    def test_importing_gcmon_does_not_resolve_the_version(self) -> None:
        # Reading the distribution's metadata stats every sys.path entry (~35 ms here), and only
        # `--version` ever asks for it. A fresh interpreter, so the check does not depend on
        # whether another test already touched the attribute.
        code = "import gcmon; print('__version__' in vars(gcmon))"
        result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
        assert result.stdout.strip() == "False"

    def test_no_fallback_under_a_normal_install(self) -> None:
        import gcmon

        assert gcmon.__version__ != "0.0.0+unknown"


class TestCliMonitor:
    def test_missing_pid(self, gcmon_cli: list[str]) -> None:
        result = subprocess.run([*gcmon_cli, "monitor"], capture_output=True, text=True)
        assert result.returncode != 0
        assert "the following arguments are required: pid" in result.stderr

    def test_explicit_command(self, gcmon_cli: list[str]) -> None:
        result = subprocess.run(
            [*gcmon_cli, "monitor", "12345", "-d", "0.1"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        assert result.returncode == 0
