"""Tests for pluggable event encoders in ``gcmon.exporters.encoder``."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import Mock

import pytest

from gcmon.exporters.encoder import (
    JsonEventEncoder,
    ProtobufEventEncoder,
    convert_trace_events_to_perfetto,  # noqa: F401  (used via monkeypatch.setattr)
)
from gcmon.trace_event import process_meta


class TestJsonEventEncoder:
    def test_write_events_empty_no_file_created(self, tmp_path: Path) -> None:
        enc = JsonEventEncoder()
        path = tmp_path / "out.json"
        enc.open(path)
        enc.write_events([])
        enc.close()
        assert path.read_bytes() == b"[]\n"


class TestProtobufEventEncoder:
    def test_write_events_empty_no_file_created(self, tmp_path: Path) -> None:
        enc = ProtobufEventEncoder()
        path = tmp_path / "out.perfetto"
        enc.open(path)
        enc.write_events([])
        enc.close()
        assert not path.exists()

    def test_default_cmdline_provider_returns_cmdline(self) -> None:
        result = ProtobufEventEncoder._default_cmdline_provider(os.getpid())
        assert isinstance(result, list)

    def test_ensure_cmdline_skips_already_set(self) -> None:
        provider = Mock(return_value=["python", "app.py"])
        enc = ProtobufEventEncoder(cmdline_provider=provider)
        enc._ensure_cmdline(1234)
        enc._ensure_cmdline(1234)
        assert provider.call_count == 1

    def test_write_events_returns_early_when_converter_produces_no_output(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        enc = ProtobufEventEncoder()
        path = tmp_path / "out.perfetto"
        enc.open(path)
        monkeypatch.setattr(
            "gcmon.exporters.encoder.convert_trace_events_to_perfetto",
            Mock(return_value=([], [])),
        )
        enc.write_events([process_meta(pid=1234, name="app")])
        enc.close()
        assert not path.exists()
        assert enc._has_written is False
