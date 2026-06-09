from __future__ import annotations

import importlib.util
import textwrap
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / ".github" / "scripts" / "extract_changelog.py"


def _load_module() -> Any:
    spec = importlib.util.spec_from_file_location("extract_changelog", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


extract_changelog = _load_module()


@pytest.fixture
def fake_changelog(tmp_path: Path) -> Path:
    p = tmp_path / "CHANGELOG.md"
    p.write_text(
        textwrap.dedent("""\
            # Changelog

            ## WIP
            - upcoming stuff

            ## Version 0.2.0
            - new feature
            - bug fix

            ## Version 0.1.0 (2026-05-22)
            - initial release
        """),
        encoding="utf-8",
    )
    return p


@pytest.fixture
def fake_pyproject(tmp_path: Path) -> Path:
    p = tmp_path / "pyproject.toml"
    p.write_bytes(b'[tool.poetry]\nversion = "0.2.0"\n')
    return p


class TestResolveVersion:
    def test_strips_v_prefix(self) -> None:
        assert extract_changelog.resolve_version("v0.2.0") == "0.2.0"

    def test_keeps_pep440_suffix(self) -> None:
        assert extract_changelog.resolve_version("v0.2.0a1") == "0.2.0a1"

    def test_falls_back_to_pyproject_when_tag_missing(self, fake_pyproject: Path) -> None:
        original = extract_changelog.PYPROJECT_PATH
        extract_changelog.PYPROJECT_PATH = fake_pyproject
        try:
            assert extract_changelog.resolve_version(None) == "0.2.0"
        finally:
            extract_changelog.PYPROJECT_PATH = original

    def test_falls_back_to_pyproject_when_tag_lacks_v_prefix(self, fake_pyproject: Path) -> None:
        original = extract_changelog.PYPROJECT_PATH
        extract_changelog.PYPROJECT_PATH = fake_pyproject
        try:
            assert extract_changelog.resolve_version("refs/heads/main") == "0.2.0"
        finally:
            extract_changelog.PYPROJECT_PATH = original


class TestExtract:
    def test_returns_section_body(self, fake_changelog: Path) -> None:
        original = extract_changelog.CHANGELOG_PATH
        extract_changelog.CHANGELOG_PATH = fake_changelog
        try:
            assert extract_changelog.extract("0.2.0") == "- new feature\n- bug fix"
        finally:
            extract_changelog.CHANGELOG_PATH = original

    def test_handles_version_with_date_suffix(self, fake_changelog: Path) -> None:
        original = extract_changelog.CHANGELOG_PATH
        extract_changelog.CHANGELOG_PATH = fake_changelog
        try:
            assert extract_changelog.extract("0.1.0") == "- initial release"
        finally:
            extract_changelog.CHANGELOG_PATH = original

    def test_returns_empty_on_miss(self, fake_changelog: Path, capsys: pytest.CaptureFixture[str]) -> None:
        original = extract_changelog.CHANGELOG_PATH
        extract_changelog.CHANGELOG_PATH = fake_changelog
        try:
            result = extract_changelog.extract("9.9.9")
        finally:
            extract_changelog.CHANGELOG_PATH = original
        assert result == ""
        captured = capsys.readouterr()
        assert "No changelog section" in captured.err
        assert "0.2.0" in captured.err
        assert "0.1.0" in captured.err

    def test_word_boundary_prevents_false_match(self, fake_changelog: Path) -> None:
        original = extract_changelog.CHANGELOG_PATH
        extract_changelog.CHANGELOG_PATH = fake_changelog
        try:
            result = extract_changelog.extract("0.2")
        finally:
            extract_changelog.CHANGELOG_PATH = original
        assert result == ""

    def test_substring_does_not_match_unrelated_version(self, fake_changelog: Path) -> None:
        original = extract_changelog.CHANGELOG_PATH
        extract_changelog.CHANGELOG_PATH = fake_changelog
        try:
            result = extract_changelog.extract("0.10")
        finally:
            extract_changelog.CHANGELOG_PATH = original
        assert result == ""


class TestMain:
    def test_prints_body_and_exits_zero(
        self,
        fake_changelog: Path,
        fake_pyproject: Path,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        original_changelog = extract_changelog.CHANGELOG_PATH
        original_pyproject = extract_changelog.PYPROJECT_PATH
        extract_changelog.CHANGELOG_PATH = fake_changelog
        extract_changelog.PYPROJECT_PATH = fake_pyproject
        monkeypatch.setattr("sys.argv", ["extract_changelog.py"])
        try:
            rc = extract_changelog.main()
        finally:
            extract_changelog.CHANGELOG_PATH = original_changelog
            extract_changelog.PYPROJECT_PATH = original_pyproject
        assert rc == 0
        assert capsys.readouterr().out.strip() == "- new feature\n- bug fix"

    def test_uses_tag_when_provided(
        self,
        fake_changelog: Path,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        original_changelog = extract_changelog.CHANGELOG_PATH
        extract_changelog.CHANGELOG_PATH = fake_changelog
        monkeypatch.setattr("sys.argv", ["extract_changelog.py", "v0.1.0"])
        try:
            rc = extract_changelog.main()
        finally:
            extract_changelog.CHANGELOG_PATH = original_changelog
        assert rc == 0
        assert capsys.readouterr().out.strip() == "- initial release"
