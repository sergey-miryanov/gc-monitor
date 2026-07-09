"""Tests for monitoring options validation."""

from __future__ import annotations

from argparse import Namespace
from pathlib import Path

import pytest

from gcmon.commands.monitoring_options import get_monitoring_options
from gcmon.stats_output import TableFormat


def _make_args(**overrides: object) -> Namespace:
    defaults: dict[str, object] = {
        "output": Path("trace.json"),
        "rate": 0.1,
        "duration": 0.05,
        "format": "chrome",
        "flush_threshold": 100,
        "stats": False,
        "table_format": TableFormat.PLAIN,
        "control_name": None,
    }
    return Namespace(**{**defaults, **overrides})


class TestOutputPathValidation:
    def test_valid_path_in_cwd(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.chdir(tmp_path)
        args = _make_args(output=Path("trace.json"))
        result = get_monitoring_options(args)
        assert result is not None
        assert result.output_path == Path("trace.json")

    def test_valid_path_in_subdirectory(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "subdir").mkdir()
        args = _make_args(output=Path("subdir/trace.json"))
        result = get_monitoring_options(args)
        assert result is not None

    def test_missing_parent_directory_rejected(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.chdir(tmp_path)
        args = _make_args(output=Path("nonexistent/trace.json"))
        result = get_monitoring_options(args)
        assert result is None

    def test_stdout_format_skips_path_validation(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.chdir(tmp_path)
        args = _make_args(output=Path("nonexistent/trace.json"), format="stdout")
        result = get_monitoring_options(args)
        assert result is not None

    def test_dot_path_resolves_to_cwd(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.chdir(tmp_path)
        args = _make_args(output=Path("."))
        result = get_monitoring_options(args)
        assert result is not None

    def test_absolute_path_passes(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.chdir(tmp_path)
        path = tmp_path / "trace.json"
        args = _make_args(output=path)
        result = get_monitoring_options(args)
        assert result is not None

    def test_path_traversal_parent_exists_passes(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.chdir(tmp_path)
        args = _make_args(output=Path("../trace.json"))
        result = get_monitoring_options(args)
        assert result is not None
