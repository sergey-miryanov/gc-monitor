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
        enc._track_state.update_process_lifetime(1234, 1_000)

        enc._ensure_cmdline(1234, 1)
        enc._ensure_cmdline(1234, 1)

        assert provider.call_count == 1

    def test_a_process_whose_span_has_closed_is_not_asked(self) -> None:
        """The provider reads whatever holds the pid now, which for a
        process that has exited is its successor or nothing. An absent
        command line beats a wrong one (ADR-0010)."""
        provider = Mock(return_value=["python", "-m", "successor"])
        enc = ProtobufEventEncoder(cmdline_provider=provider)
        enc._track_state.observe_process_liveness({1234}, 1_000)
        enc._track_state.observe_process_liveness(set(), 2_000)

        enc._ensure_cmdline(1234, 1)

        assert provider.call_count == 0
        assert enc._track_state.get_cmdline(1234, 1) is None

    def test_a_process_that_cannot_be_asked_is_asked_once(self) -> None:
        """Skipping the read still marks it done, so a straggling pid
        costs one check rather than one per flush for the rest of the
        run."""
        provider = Mock(return_value=["python", "app.py"])
        enc = ProtobufEventEncoder(cmdline_provider=provider)
        enc._track_state.observe_process_liveness({1234}, 1_000)
        enc._track_state.observe_process_liveness(set(), 2_000)

        enc._ensure_cmdline(1234, 1)
        enc._ensure_cmdline(1234, 1)

        assert provider.call_count == 0

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

        # Asserted before ``close()``: the event was folded into the span
        # accumulator on the way in, so closing has a ``Processes`` track
        # to write whatever the converter returned.
        assert not path.exists()
        assert enc._has_written is False

    def test_a_batch_is_folded_in_before_the_command_line_is_read(self, tmp_path: Path) -> None:
        """The span accumulator knows about a batch before anything asks
        it a question about that batch.

        Reading a command line is the first thing ``write_events`` does
        with a pid, and which process holds that pid is a question only
        the accumulator can answer. Ask it before the fold and it answers
        from a trace that has not seen these events.
        """
        folded: list[bool] = []

        def provider(pid: int) -> list[str]:
            folded.append(enc._track_state.has_process_lifetime(pid, 1))
            return []

        enc = ProtobufEventEncoder(cmdline_provider=provider)
        enc.open(tmp_path / "out.perfetto")

        enc.write_events([Instant(ProcessTrack(1234), "ev", ts=1_000)])

        assert folded == [True]

    def test_a_command_line_is_read_once_per_process(self, tmp_path: Path) -> None:
        """A pid handed on is two processes, and each is asked what it was
        running.

        Read once per trace, the answer the first process gave went on the
        second one's row, naming a program it never ran.
        """
        provider = Mock(return_value=["python", "app.py"])
        enc = ProtobufEventEncoder(cmdline_provider=provider)
        enc.open(tmp_path / "out.perfetto")

        enc.record_process_liveness({1234}, 500)
        enc.write_events([Instant(ProcessTrack(1234), "ev", ts=1_000)])
        enc.record_process_liveness(set(), 2_000)
        enc.write_events([Instant(ProcessTrack(1234), "ev", ts=3_000)])

        assert provider.call_count == 2
