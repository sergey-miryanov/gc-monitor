"""Tests for monitoring options validation."""

from __future__ import annotations

import logging
from argparse import Namespace
from pathlib import Path

import pytest

from gcmon.commands.monitoring_options import RSS_CAPABLE_FORMATS, MonitoringOptions, get_monitoring_options
from gcmon.stats_output import StatsView, TableFormat


def _make_args(**overrides: object) -> Namespace:
    defaults: dict[str, object] = {
        "output": Path("trace.json"),
        "rate": 0.1,
        "duration": 0.05,
        "format": "chrome",
        "flush_threshold": 100,
        "stats": None,
        "table_format": TableFormat.PLAIN,
        "control_name": None,
        "rss": False,
        "rss_interval": 1.0,
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


class TestRssIntervalWarning:
    def test_rss_interval_shorter_than_rate_warns(self, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.WARNING)
        args = _make_args(rss=True, rss_interval=0.05, rate=0.1)
        result = get_monitoring_options(args)
        assert result is not None
        assert result.rss_enabled
        assert "shorter than poll rate" in caplog.text

    def test_rss_interval_equal_to_rate_no_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.WARNING)
        args = _make_args(rss=True, rss_interval=0.1, rate=0.1)
        result = get_monitoring_options(args)
        assert result is not None
        assert "shorter than poll rate" not in caplog.text

    def test_rss_interval_longer_than_rate_no_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.WARNING)
        args = _make_args(rss=True, rss_interval=2.0, rate=0.1)
        result = get_monitoring_options(args)
        assert result is not None
        assert "shorter than poll rate" not in caplog.text

    def test_rss_disabled_no_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.WARNING)
        args = _make_args(rss=False, rss_interval=0.05, rate=0.1)
        result = get_monitoring_options(args)
        assert result is not None
        assert not result.rss_enabled
        assert "shorter than poll rate" not in caplog.text

    def test_rss_interval_zero_rejected(self) -> None:
        args = _make_args(rss=True, rss_interval=0.0)
        result = get_monitoring_options(args)
        assert result is None

    def test_rss_interval_negative_rejected(self) -> None:
        args = _make_args(rss=True, rss_interval=-1.0)
        result = get_monitoring_options(args)
        assert result is None

    def test_rss_disabled_ignores_zero_interval(self) -> None:
        args = _make_args(rss=False, rss_interval=0.0)
        result = get_monitoring_options(args)
        assert result is not None
        assert not result.rss_enabled

    def test_rss_disabled_ignores_negative_interval(self) -> None:
        args = _make_args(rss=False, rss_interval=-1.0)
        result = get_monitoring_options(args)
        assert result is not None
        assert not result.rss_enabled


class TestRssFormatWarning:
    """--rss is accepted for every format, but only some exporters implement
    add_rss_sample; the rest discard the samples and must say so."""

    @pytest.mark.parametrize("output_format", ["jsonl", "stdout"])
    def test_unsupported_format_warns(self, output_format: str, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.WARNING)
        args = _make_args(rss=True, format=output_format)
        result = get_monitoring_options(args)
        assert result is not None
        assert result.rss_enabled
        assert "RSS tracking is not supported" in caplog.text

    @pytest.mark.parametrize("output_format", list(RSS_CAPABLE_FORMATS))
    def test_supported_format_does_not_warn(self, output_format: str, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.WARNING)
        args = _make_args(rss=True, format=output_format)
        result = get_monitoring_options(args)
        assert result is not None
        assert "RSS tracking is not supported" not in caplog.text

    def test_rss_disabled_does_not_warn_on_unsupported_format(self, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.WARNING)
        args = _make_args(rss=False, format="jsonl")
        result = get_monitoring_options(args)
        assert result is not None
        assert "RSS tracking is not supported" not in caplog.text


class TestTheStatsFlagCarriesTheView:
    """`--stats` names which blocks to print and refuses to guess: a value is
    required, and it is one of two words.
    """

    def _parse(self, argv: list[str]) -> Namespace:
        from gcmon.cli import _create_parser

        return _create_parser().parse_args(argv)

    @pytest.mark.parametrize("word, view", [("total", StatsView.TOTAL), ("full", StatsView.FULL)])
    def test_each_word_selects_its_view(self, word: str, view: StatsView) -> None:
        result = get_monitoring_options(self._parse(["monitor", "12345", f"--stats={word}"]))

        assert result is not None
        assert result.stats_view is view

    def test_no_flag_asks_for_no_table(self) -> None:
        result = get_monitoring_options(self._parse(["monitor", "12345"]))

        assert result is not None
        assert result.stats_view is None

    @pytest.mark.parametrize(
        "argv",
        [
            ["monitor", "12345", "--stats=total"],
            ["monitor", "12345", "--stats", "total"],
            ["monitor", "--stats", "total", "12345"],
        ],
    )
    def test_the_pid_survives_the_flag(self, argv: list[str]) -> None:
        """The ordering an alias would have eaten: `--stats` immediately
        before a positional that does not start with `-`. Requiring the value
        means every spelling reads the same way."""
        assert self._parse(argv).pid == 12345

    def test_a_bare_flag_is_refused(self, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit) as exit_info:
            self._parse(["monitor", "12345", "--stats"])

        assert exit_info.value.code != 0
        err = capsys.readouterr().err
        assert "total" in err
        assert "full" in err

    def test_an_unknown_value_is_refused(self, capsys: pytest.CaptureFixture[str]) -> None:
        """`all` reads as the wider view and is not one, so it fails where the
        fix is the next thing typed rather than at the end of a capture."""
        with pytest.raises(SystemExit) as exit_info:
            self._parse(["monitor", "12345", "--stats=all"])

        assert exit_info.value.code != 0
        err = capsys.readouterr().err
        assert "total" in err
        assert "full" in err

    def test_run_takes_the_same_two_words(self) -> None:
        result = get_monitoring_options(self._parse(["run", "--stats=total", "-m", "timeit"]))

        assert result is not None
        assert result.stats_view is StatsView.TOTAL


class TestTheStatsEnvironmentVariable:
    """`GCMON_STATS` takes the same two words, and an unreadable value fails
    the run rather than falling back: a variable choosing between two named
    views has no default that is safely one of them.
    """

    def _options(self, argv: list[str]) -> MonitoringOptions | None:
        from gcmon.cli import _create_parser

        # The parser reads the variable while it is being built, so it has to
        # be built after the test sets it.
        return get_monitoring_options(_create_parser().parse_args(argv))

    @pytest.mark.parametrize("word, view", [("total", StatsView.TOTAL), ("full", StatsView.FULL)])
    def test_each_word_selects_its_view(self, monkeypatch: pytest.MonkeyPatch, word: str, view: StatsView) -> None:
        monkeypatch.setenv("GCMON_STATS", word)

        result = self._options(["monitor", "12345"])

        assert result is not None
        assert result.stats_view is view

    def test_the_flag_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GCMON_STATS", "full")

        result = self._options(["monitor", "12345", "--stats=total"])

        assert result is not None
        assert result.stats_view is StatsView.TOTAL

    def test_unset_asks_for_no_table(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("GCMON_STATS", raising=False)

        result = self._options(["monitor", "12345"])

        assert result is not None
        assert result.stats_view is None

    @pytest.mark.parametrize("value", ["1", "true", "all", "brief"])
    def test_a_value_it_does_not_know_fails_the_run(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, value: str
    ) -> None:
        """`GCMON_STATS=1` from an older release stops the run at startup
        rather than at the end of a long capture."""
        caplog.set_level(logging.ERROR)
        monkeypatch.setenv("GCMON_STATS", value)

        assert self._options(["monitor", "12345"]) is None
        assert "total" in caplog.text
        assert "full" in caplog.text
        assert value in caplog.text

    def test_the_message_names_the_variable(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Nothing else can reach this: the flag's own values are checked at
        parse time, so the operator is told which of the two to edit."""
        caplog.set_level(logging.ERROR)
        monkeypatch.setenv("GCMON_STATS", "1")

        self._options(["monitor", "12345"])

        assert "GCMON_STATS" in caplog.text
