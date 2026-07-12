import types
from pathlib import Path

import pytest

from tests.helpers import DefaultsValue


class TestEnvVarDefaults:
    """Parameterized tests for default env var values."""

    @pytest.mark.parametrize(
        "env_var, getter_suffix, default",
        [
            ("ENV_OUTPUT", "output", Path("gcmon.json")),
            ("ENV_RATE", "rate", 0.1),
            ("ENV_DURATION", "duration", None),
            ("ENV_THREAD_ID", "thread_id", 0),
            ("ENV_FLUSH_THRESHOLD", "flush_threshold", 100),
            ("ENV_SERVER_HOST", "server_host", "localhost"),
            ("ENV_SERVER_PORT", "server_port", 9999),
        ],
    )
    def test_default(
        self,
        monkeypatch: pytest.MonkeyPatch,
        env_module: types.ModuleType,
        env_var: str,
        getter_suffix: str,
        default: DefaultsValue,
    ) -> None:
        getter = getattr(env_module, f"get_env_{getter_suffix}")
        actual_env_name = getattr(env_module, env_var)
        monkeypatch.delenv(actual_env_name, raising=False)
        assert getter() == default


class TestEnvVarCustom:
    """Parameterized tests for custom env var values."""

    @pytest.mark.parametrize(
        "env_var, getter_suffix, custom_val, expected",
        [
            ("ENV_OUTPUT", "output", "custom.json", Path("custom.json")),
            ("ENV_RATE", "rate", "0.05", 0.05),
            ("ENV_DURATION", "duration", "30.0", 30.0),
            ("ENV_THREAD_ID", "thread_id", "9999", 9999),
            ("ENV_FLUSH_THRESHOLD", "flush_threshold", "50", 50),
            ("ENV_SERVER_HOST", "server_host", "127.0.0.1", "127.0.0.1"),
            ("ENV_SERVER_PORT", "server_port", "8888", 8888),
        ],
    )
    def test_custom(
        self,
        monkeypatch: pytest.MonkeyPatch,
        env_module: types.ModuleType,
        env_var: str,
        getter_suffix: str,
        custom_val: str,
        expected: DefaultsValue,
    ) -> None:
        getter = getattr(env_module, f"get_env_{getter_suffix}")
        actual_env_name = getattr(env_module, env_var)
        monkeypatch.setenv(actual_env_name, custom_val)
        assert getter() == expected


class TestEnvVarInvalidValues:
    """Parameterized tests for invalid env var values falling back to defaults."""

    @pytest.mark.parametrize(
        "env_var, getter_suffix, default",
        [
            ("ENV_RATE", "rate", 0.1),
            ("ENV_DURATION", "duration", None),
            ("ENV_THREAD_ID", "thread_id", 0),
            ("ENV_FLUSH_THRESHOLD", "flush_threshold", 100),
            ("ENV_SERVER_PORT", "server_port", 9999),
        ],
    )
    def test_invalid_value_returns_default(
        self,
        monkeypatch: pytest.MonkeyPatch,
        env_module: types.ModuleType,
        env_var: str,
        getter_suffix: str,
        default: DefaultsValue,
    ) -> None:
        getter = getattr(env_module, f"get_env_{getter_suffix}")
        actual_env_name = getattr(env_module, env_var)
        monkeypatch.setenv(actual_env_name, "not-a-number")
        assert getter() == default


class TestEnvVerbose:
    """Tests for GCMON_VERBOSE parsing."""

    def test_default(self, monkeypatch: pytest.MonkeyPatch, env_module: types.ModuleType) -> None:
        monkeypatch.delenv(env_module.ENV_VERBOSE, raising=False)
        assert env_module.get_env_verbose() == 0

    def test_numeric(self, monkeypatch: pytest.MonkeyPatch, env_module: types.ModuleType) -> None:
        monkeypatch.setenv(env_module.ENV_VERBOSE, "2")
        assert env_module.get_env_verbose() == 2

    @pytest.mark.parametrize("value", ["true", "yes", "on", "1"])
    def test_truthy_values(self, monkeypatch: pytest.MonkeyPatch, env_module: types.ModuleType, value: str) -> None:
        monkeypatch.setenv(env_module.ENV_VERBOSE, value)
        assert env_module.get_env_verbose() == 1


class TestEnvFormat:
    """Tests for GCMON_FORMAT parsing."""

    def test_default(self, monkeypatch: pytest.MonkeyPatch, env_module: types.ModuleType) -> None:
        monkeypatch.delenv(env_module.ENV_FORMAT, raising=False)
        assert env_module.get_env_format() == "chrome"

    @pytest.mark.parametrize("value, expected", [("stdout", "stdout"), ("jsonl", "jsonl")])
    def test_valid_values(
        self, monkeypatch: pytest.MonkeyPatch, env_module: types.ModuleType, value: str, expected: str
    ) -> None:
        monkeypatch.setenv(env_module.ENV_FORMAT, value)
        assert env_module.get_env_format() == expected

    def test_invalid_value_returns_default(self, monkeypatch: pytest.MonkeyPatch, env_module: types.ModuleType) -> None:
        monkeypatch.setenv(env_module.ENV_FORMAT, "invalid_format")
        assert env_module.get_env_format() == "chrome"


class TestEnvOutputSpecialCases:
    """Tests for GCMON_OUTPUT special behavior."""

    def test_default_format_jsonl(self, monkeypatch: pytest.MonkeyPatch, env_module: types.ModuleType) -> None:
        monkeypatch.setenv(env_module.ENV_FORMAT, "jsonl")
        monkeypatch.delenv(env_module.ENV_OUTPUT, raising=False)
        assert env_module.get_env_output() == Path("gcmon.jsonl")


class TestEnvStats:
    """Tests for GCMON_STATS parsing."""

    def test_default(self, monkeypatch: pytest.MonkeyPatch, env_module: types.ModuleType) -> None:
        monkeypatch.delenv(env_module.ENV_STATS, raising=False)
        assert env_module.get_env_stats() is False

    @pytest.mark.parametrize("value", ["1", "true", "yes", "on"])
    def test_truthy_values(self, monkeypatch: pytest.MonkeyPatch, env_module: types.ModuleType, value: str) -> None:
        monkeypatch.setenv(env_module.ENV_STATS, value)
        assert env_module.get_env_stats() is True

    def test_false_value(self, monkeypatch: pytest.MonkeyPatch, env_module: types.ModuleType) -> None:
        monkeypatch.setenv(env_module.ENV_STATS, "0")
        assert env_module.get_env_stats() is False

    def test_random_string(self, monkeypatch: pytest.MonkeyPatch, env_module: types.ModuleType) -> None:
        monkeypatch.setenv(env_module.ENV_STATS, "nope")
        assert env_module.get_env_stats() is False


class TestEnvTableFormat:
    """Tests for GCMON_TABLE_FORMAT parsing."""

    def test_default(self, monkeypatch: pytest.MonkeyPatch, env_module: types.ModuleType) -> None:
        monkeypatch.delenv(env_module.ENV_TABLE_FORMAT, raising=False)
        assert env_module.get_env_table_format() == env_module.TableFormat.PLAIN

    @pytest.mark.parametrize("value", ["md", "markdown", "MD", "Markdown"])
    def test_markdown_values(self, monkeypatch: pytest.MonkeyPatch, env_module: types.ModuleType, value: str) -> None:
        monkeypatch.setenv(env_module.ENV_TABLE_FORMAT, value)
        assert env_module.get_env_table_format() == env_module.TableFormat.MARKDOWN

    def test_invalid_value_returns_default(self, monkeypatch: pytest.MonkeyPatch, env_module: types.ModuleType) -> None:
        monkeypatch.setenv(env_module.ENV_TABLE_FORMAT, "html")
        assert env_module.get_env_table_format() == env_module.TableFormat.PLAIN


class TestEnvVarEmpty:
    """Tests for env vars set to empty string (different from unset)."""

    @pytest.mark.parametrize(
        "env_var, getter_suffix, default",
        [
            ("ENV_OUTPUT", "output", Path("gcmon.json")),
            ("ENV_RATE", "rate", 0.1),
            ("ENV_DURATION", "duration", None),
            ("ENV_THREAD_ID", "thread_id", 0),
            ("ENV_FLUSH_THRESHOLD", "flush_threshold", 100),
            ("ENV_SERVER_HOST", "server_host", "localhost"),
            ("ENV_SERVER_PORT", "server_port", 9999),
            ("ENV_VERBOSE", "verbose", 0),
            ("ENV_FORMAT", "format", "chrome"),
            ("ENV_STATS", "stats", False),
            ("ENV_TABLE_FORMAT", "table_format", None),
            ("ENV_CONTROL_NAME", "control_name", None),
        ],
    )
    def test_empty_string_returns_default(
        self,
        monkeypatch: pytest.MonkeyPatch,
        env_module: types.ModuleType,
        env_var: str,
        getter_suffix: str,
        default: DefaultsValue,
    ) -> None:
        getter = getattr(env_module, f"get_env_{getter_suffix}")
        actual_env_name = getattr(env_module, env_var)
        monkeypatch.setenv(actual_env_name, "")
        result = getter()
        if getter_suffix == "table_format":
            assert result == env_module.TableFormat.PLAIN
        else:
            assert result == default
