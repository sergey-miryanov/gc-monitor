"""Tests for pluggable event encoders in ``gcmon.exporters.encoder``."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import Mock

import pytest

from gcmon.exporters.encoder import (
    ProtobufEventEncoder,
    convert_trace_events_to_perfetto,  # noqa: F401  (used via monkeypatch.setattr)
)
from gcmon.model.trace_event import Instant, ProcessTrack


class TestProtobufEventEncoder:
    def test_write_events_empty_no_file_created(self, tmp_path: Path) -> None:
        enc = ProtobufEventEncoder()
        path = tmp_path / "out.perfetto"
        enc.open(path)
        enc.write_events([])
        enc.close()
        assert not path.exists()

    def test_liveness_alone_still_produces_a_trace(self, tmp_path: Path) -> None:
        """``close()`` gates on having packets to emit, not on having
        written earlier: ``record_process_liveness`` reaches the span
        accumulator without passing through ``write_events``."""
        enc = ProtobufEventEncoder()
        path = tmp_path / "out.perfetto"
        enc.open(path)
        enc.record_process_liveness({1234}, 1_400_000_000)
        assert enc._has_written is False
        enc.close()
        assert path.exists() and path.stat().st_size > 0

    def test_reopening_is_refused(self, tmp_path: Path) -> None:
        """One encoder writes one trace. A reused one would drop the
        second trace's descriptors and its whole ``Processes`` track
        without raising -- hence a guard, not just a docstring."""
        enc = ProtobufEventEncoder()
        enc.open(tmp_path / "first.perfetto")
        with pytest.raises(AssertionError, match="one encoder writes one trace"):
            enc.open(tmp_path / "second.perfetto")

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
        enc.write_events([Instant(ProcessTrack(1234), "ev", ts=1_000)])
        enc.close()
        assert not path.exists()
        assert enc._has_written is False
