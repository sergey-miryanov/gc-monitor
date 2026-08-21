"""Combined chrome + perfetto exporter that fans out to two sub-exporters."""

from __future__ import annotations

from collections.abc import Set
from pathlib import Path
from typing import override

from ..model.protocol import TGCStatsInfo, TInstantMsg, TLossMsg
from .exporter import EventsExporter

__all__ = ["CombinedTraceExporter", "derive_combined_paths"]


def derive_combined_paths(base_path: Path) -> tuple[Path, Path]:
    """Derive (chrome_path, perfetto_path) from a user-supplied base path.

    The chrome path uses the ``.json`` extension; the perfetto path uses
    ``.pftrace`` (Perfetto's official file extension).
    The parent directory is preserved; the last extension of ``base_path``
    is stripped via :py:meth:`pathlib.PurePath.stem`.

    Examples:
        >>> derive_combined_paths(Path("trace"))
        (WindowsPath('trace.json'), WindowsPath('trace.pftrace'))
        >>> derive_combined_paths(Path("trace.json"))
        (WindowsPath('trace.json'), WindowsPath('trace.pftrace'))
        >>> derive_combined_paths(Path("out/gcmon"))
        (WindowsPath('out/gcmon.json'), WindowsPath('out/gcmon.pftrace'))
    """
    return (
        base_path.parent / (base_path.stem + ".json"),
        base_path.parent / (base_path.stem + ".pftrace"),
    )


class CombinedTraceExporter(EventsExporter):
    """An :class:`EventsExporter` that forwards every call to two sub-exporters.

    Used for ``--format chrome+perfetto`` to write a single event stream to
    both a Chrome Trace Event JSON file and a Perfetto binary protobuf file
    in one monitoring session. Each sub-exporter owns its own buffer, lock,
    and per-``(pid, iid)`` meta-dedup state.
    """

    def __init__(self, chrome: EventsExporter, perfetto: EventsExporter) -> None:
        self._chrome = chrome
        self._perfetto = perfetto

    @property
    def chrome_path(self) -> Path:
        return self._chrome._output_path  # type: ignore[attr-defined, no-any-return]

    @property
    def perfetto_path(self) -> Path:
        return self._perfetto._output_path  # type: ignore[attr-defined, no-any-return]

    @override
    def add_event(self, pid: int, item: TGCStatsInfo) -> None:
        self._chrome.add_event(pid, item)
        self._perfetto.add_event(pid, item)

    @override
    def add_instant_event(self, pid: int, item: TInstantMsg) -> None:
        self._chrome.add_instant_event(pid, item)
        self._perfetto.add_instant_event(pid, item)

    @override
    def add_rss_sample(self, pid: int, rss_bytes: int, ts_ns: int) -> None:
        self._chrome.add_rss_sample(pid, rss_bytes, ts_ns)
        self._perfetto.add_rss_sample(pid, rss_bytes, ts_ns)

    @override
    def add_loss_event(self, pid: int, item: TLossMsg) -> None:
        self._chrome.add_loss_event(pid, item)
        self._perfetto.add_loss_event(pid, item)

    @override
    def add_process_liveness(self, pids: Set[int], ts_ns: int) -> None:
        self._chrome.add_process_liveness(pids, ts_ns)
        self._perfetto.add_process_liveness(pids, ts_ns)

    @override
    def close(self) -> None:
        try:
            self._chrome.close()
        finally:
            self._perfetto.close()
