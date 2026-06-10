"""Thread-safety stress tests for exporters.

Topology: two threads access a single exporter concurrently.

  * Main thread          - writes GC events via add_event()
  * Control-server thread - writes instant events via add_instant_event()

This mirrors the real production access pattern in
``src/gcmon/monitor.py:54`` and ``src/gcmon/control/control_server.py:209``.
``close()`` may come from either thread (or a watchdog).

A single parametrized test class exercises all four exporter types via
an ``ExporterFactory`` abstraction. The factories live in this file and
expose:

  * ``build(tmp_path, threshold)`` -> ``(exporter, capture, teardown)``
  * ``id`` -- human-readable name shown by pytest

``capture`` is an exporter-specific output reader implementing
``count_completes()`` and ``count_instants()``.

All tests are decorated with ``@pytest.mark.stress`` and skipped by
default via ``addopts = "-m 'not stress'"`` in pyproject.toml. Run them
locally with:

    pytest -m stress tests/exporters/test_exporter_thread_safety.py

Optionally combine with the project's existing ``--count`` option
(see ``tests/conftest.py``):

    pytest -m stress --count=10 tests/exporters/test_exporter_thread_safety.py
"""

from __future__ import annotations

import io
import json
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import NamedTuple
from unittest.mock import MagicMock

import pytest

from gcmon.exporters import (
    EventsExporter,
    JsonlExporter,
    PerfettoExporter,
    StdoutExporter,
    TraceExporter,
)
from gcmon.exporters.perfetto_format import (
    TYPE_INSTANT,
    TYPE_SLICE_BEGIN,
    TracePacketField,
    TrackEventField,
)
from gcmon.protocol import TGCStatsInfo, TInstantMsg
from tests.data_helpers import create_instant_msg
from tests.helpers import create_mock_stats_item
from tests.proto_decoder import ProtoField, decode_message, get_fields, get_varint

# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------

N_GC = 100
N_INSTANT = 100
PRE_FILL = 50
THREAD_JOIN_TIMEOUT = 30.0

MAIN_PID = 12345
CTRL_PID = 67890


# ---------------------------------------------------------------------------
# Generic helpers
# ---------------------------------------------------------------------------


def _run_two_threads(workers: list[Callable[[], None]]) -> list[BaseException]:
    """Run all workers behind a Barrier, return any captured exceptions.

    Each worker is launched on its own thread. They block on a Barrier
    sized to the number of workers; once the last thread arrives all
    workers proceed simultaneously. No ``time.sleep`` is used for
    synchronization.
    """
    barrier = threading.Barrier(len(workers))
    captured: list[BaseException] = []
    captured_lock = threading.Lock()

    def _wrap(fn: Callable[[], None]) -> None:
        try:
            barrier.wait(timeout=THREAD_JOIN_TIMEOUT)
            fn()
        except BaseException as exc:  # surface everything
            with captured_lock:
                captured.append(exc)

    threads = [threading.Thread(target=_wrap, args=(w,), daemon=True) for w in workers]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=THREAD_JOIN_TIMEOUT)
        assert not t.is_alive(), f"thread {t.name} did not finish within {THREAD_JOIN_TIMEOUT}s"

    return captured


def _make_gc_events(n: int, pid: int, ts_base: int) -> list[TGCStatsInfo]:
    """N GC events with unique ts_start / iid so we can later assert no overwrites."""
    return [
        create_mock_stats_item(
            gen=0,
            iid=1000 + i,
            ts_start=ts_base + i * 1_000_000,
            ts_stop=ts_base + i * 1_000_000 + 500_000,
            collections=1,
            collected=1,
            uncollectable=0,
            candidates=1,
            heap_size=1024,
            duration=0.001,
        )
        for i in range(n)
    ]


def _make_instant_events(n: int, pid: int, ts_base: int) -> list[TInstantMsg]:
    """N instant events with unique ts / name."""
    return [create_instant_msg(name=f"inst-{i}", ts=ts_base + i) for i in range(n)]


def _patch_psutil(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace psutil so PerfettoExporter doesn't touch real processes."""
    mock_process = MagicMock()
    mock_process.cmdline.return_value = ["python", "-u", "script.py"]
    mock_psutil = MagicMock()
    mock_psutil.Process.return_value = mock_process
    mock_psutil.Error = Exception
    monkeypatch.setitem(sys.modules, "psutil", mock_psutil)


# ---------------------------------------------------------------------------
# OutputCapture: per-exporter output reader
# ---------------------------------------------------------------------------


class OutputCapture:
    """Per-exporter output reader.

    Subclasses implement ``count_completes()`` and ``count_instants()``,
    counting "complete" (= one GC event) and "instant" (= one instant
    event) records in the exporter's output. The counting strategy is
    exporter-specific because the formats differ:

    * JSONL/Stdout  -- one record per line
    * Chrome trace  -- "ph": "X" / "ph": "I" markers; we count by raw
                       text scan to stay correct when the writer
                       appends after ``close()`` (which leaves data
                       outside the JSON array)
    * Perfetto      -- protobuf packets with TYPE_SLICE_BEGIN /
                       TYPE_INSTANT track events
    """

    def count_completes(self) -> int:
        return 0

    def count_instants(self) -> int:
        return 0


class JsonlFileCapture(OutputCapture):
    """Captures JSONL output from a file exporter."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def _lines(self) -> list[dict[str, object]]:
        if not self._path.exists():
            return []
        out: list[dict[str, object]] = []
        for ln in self._path.read_text(encoding="utf-8").splitlines():
            if not ln:
                continue
            out.append(json.loads(ln))
        return out

    def count_completes(self) -> int:
        # GC events: no 'type' field. Instant events: type == 'i'.
        return sum(1 for e in self._lines() if e.get("type") != "i")

    def count_instants(self) -> int:
        return sum(1 for e in self._lines() if e.get("type") == "i")


class ChromeTraceFileCapture(OutputCapture):
    """Captures Chrome Trace output. Uses raw-text counting for resilience."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def _text(self) -> str:
        if not self._path.exists():
            return ""
        return self._path.read_text(encoding="utf-8")

    def count_completes(self) -> int:
        return self._text().count('"ph": "X"')

    def count_instants(self) -> int:
        return self._text().count('"ph": "I"')


class PerfettoFileCapture(OutputCapture):
    """Captures Perfetto protobuf output."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def _packets(self) -> list[list[ProtoField]]:
        if not self._path.exists():
            return []
        data = self._path.read_bytes()
        if not data:
            return []
        top = decode_message(data)
        return [decode_message(f.value) for f in get_fields(top, 1)]  # type: ignore[arg-type]  # PACKET field

    def _get_bytes_at(self, fields: list[ProtoField], field_number: int) -> bytes | None:
        for f in fields:
            if f.field_number == field_number and f.wire_type == 2:
                return f.value  # type: ignore[return-value]
        return None

    def _count_event_type(self, event_type: int) -> int:
        n = 0
        for pf in self._packets():
            te_bytes = self._get_bytes_at(pf, TracePacketField.TRACK_EVENT)
            if not te_bytes:
                continue
            te = decode_message(te_bytes)
            if get_varint(te, TrackEventField.TYPE) == event_type:
                n += 1
        return n

    def count_completes(self) -> int:
        return self._count_event_type(TYPE_SLICE_BEGIN)

    def count_instants(self) -> int:
        return self._count_event_type(TYPE_INSTANT)


# ---------------------------------------------------------------------------
# Stdout capture: StdoutExporter extends JsonlExporter; same line format.
# ---------------------------------------------------------------------------


class _LockingStringIO(io.StringIO):
    """StringIO subclass that locks every write() so we can detect mid-line interleaving."""

    def __init__(self) -> None:
        super().__init__()
        self._lock = threading.Lock()

    def write(self, s: str) -> int:
        with self._lock:
            return super().write(s)


class StdoutCapture(OutputCapture):
    """Captures StdoutExporter output via a locked StringIO buffer."""

    def __init__(self, buffer: _LockingStringIO) -> None:
        self._buffer = buffer

    def _lines(self) -> list[dict[str, object]]:
        out: list[dict[str, object]] = []
        for ln in self._buffer.getvalue().splitlines():
            if not ln:
                continue
            out.append(json.loads(ln))
        return out

    def count_completes(self) -> int:
        return sum(1 for e in self._lines() if e.get("type") != "i")

    def count_instants(self) -> int:
        return sum(1 for e in self._lines() if e.get("type") == "i")


class _StdoutRedirector:
    """Context manager that swaps ``sys.stdout`` for a locked StringIO buffer."""

    def __init__(self) -> None:
        self._buffer = _LockingStringIO()
        self._original: object = None

    def __enter__(self) -> _LockingStringIO:
        self._original = sys.stdout
        sys.stdout = self._buffer
        return self._buffer

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        sys.stdout = self._original  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# ExporterFactory
# ---------------------------------------------------------------------------


class Bundle(NamedTuple):
    """Return value of ``ExporterFactory.build``."""

    exporter: EventsExporter
    capture: OutputCapture
    teardown: Callable[[], None]


class ExporterFactory:
    """Build an exporter, capture its output, and clean up after the test.

    Each subclass is responsible for exporter-specific concerns:
    ``PerfettoExporter`` needs psutil mocked; ``StdoutExporter`` needs
    ``sys.stdout`` redirected to a buffer. The ``build`` method returns
    a :class:`Bundle` whose ``teardown`` callable releases whatever
    resources ``build`` acquired.
    """

    id: str

    def build(self, tmp_path: Path, threshold: int) -> Bundle:  # pragma: no cover - abstract
        raise NotImplementedError


@dataclass
class _FileExporterFactory(ExporterFactory):
    """Base for file-based exporters."""

    _id: str
    _builder: Callable[[Path, int], EventsExporter]
    _capture_cls: type[OutputCapture]

    @property
    def id(self) -> str:  # type: ignore[override]
        return self._id

    def build(self, tmp_path: Path, threshold: int) -> Bundle:
        path = tmp_path / f"out_{self._id}.dat"
        exporter = self._builder(path, threshold)
        return Bundle(exporter=exporter, capture=self._capture_cls(path), teardown=lambda: None)


def _build_jsonl(path: Path, threshold: int) -> JsonlExporter:
    return JsonlExporter(output_path=path, flush_threshold=threshold)


def _build_trace(path: Path, threshold: int) -> TraceExporter:
    return TraceExporter(output_path=path, flush_threshold=threshold)


def _build_perfetto(path: Path, threshold: int) -> PerfettoExporter:
    exporter = PerfettoExporter(output_path=path, flush_threshold=threshold)
    return exporter


# Per-test psutil patch state: the factory stores a monkeypatch handle
# and the teardown stops it.
class _PerfettoFactory(ExporterFactory):
    id = "perfetto"

    def __init__(self) -> None:
        self._mp: pytest.MonkeyPatch | None = None

    def build(self, tmp_path: Path, threshold: int) -> Bundle:
        mp = pytest.MonkeyPatch()
        _patch_psutil(mp)
        self._mp = mp
        path = tmp_path / "out_perfetto.pb"
        exporter = PerfettoExporter(output_path=path, flush_threshold=threshold)

        def teardown() -> None:
            mp.undo()
            self._mp = None

        return Bundle(exporter=exporter, capture=PerfettoFileCapture(path), teardown=teardown)


class _StdoutFactory(ExporterFactory):
    id = "stdout"

    def build(self, tmp_path: Path, threshold: int) -> Bundle:  # tmp_path unused but kept for symmetry
        redirector = _StdoutRedirector()
        buffer = redirector.__enter__()
        exporter = StdoutExporter(flush_threshold=threshold)

        def teardown() -> None:
            redirector.__exit__(None, None, None)

        return Bundle(exporter=exporter, capture=StdoutCapture(buffer), teardown=teardown)


def _all_factories() -> list[ExporterFactory]:
    return [
        _FileExporterFactory("jsonl", _build_jsonl, JsonlFileCapture),
        _FileExporterFactory("trace", _build_trace, ChromeTraceFileCapture),
        _PerfettoFactory(),
        _StdoutFactory(),
    ]


@pytest.fixture(params=_all_factories(), ids=lambda f: f.id)
def exporter_factory(request: pytest.FixtureRequest) -> ExporterFactory:
    """Parametrized fixture: one ExporterFactory per supported exporter type."""
    return request.param  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Unified test class
# ---------------------------------------------------------------------------


@pytest.mark.stress
class TestExporterThreadSafety:
    def test_concurrent_add_event_and_add_instant_event_arrive(
        self, exporter_factory: ExporterFactory, tmp_path: Path
    ) -> None:
        bundle = exporter_factory.build(tmp_path, threshold=10)
        try:
            exporter, capture = bundle.exporter, bundle.capture
            gc_events = _make_gc_events(N_GC, MAIN_PID, 1_500_000_000)
            inst_events = _make_instant_events(N_INSTANT, CTRL_PID, 2_000_000_000)

            def writer_gc() -> None:
                for ev in gc_events:
                    exporter.add_event(MAIN_PID, ev)

            def writer_inst() -> None:
                for ev in inst_events:
                    exporter.add_instant_event(CTRL_PID, ev)

            captured = _run_two_threads([writer_gc, writer_inst])
            exporter.close()
            for exc in captured:
                raise exc

            assert capture.count_completes() == N_GC, (
                f"[{exporter_factory.id}] expected {N_GC} complete events, "
                f"got {capture.count_completes()}"
            )
            assert capture.count_instants() == N_INSTANT, (
                f"[{exporter_factory.id}] expected {N_INSTANT} instant events, "
                f"got {capture.count_instants()}"
            )
        finally:
            bundle.teardown()

    def test_concurrent_add_event_and_close_no_data_loss(
        self, exporter_factory: ExporterFactory, tmp_path: Path
    ) -> None:
        bundle = exporter_factory.build(tmp_path, threshold=5)
        try:
            exporter, capture = bundle.exporter, bundle.capture
            for ev in _make_gc_events(PRE_FILL, MAIN_PID, 1_500_000_000):
                exporter.add_event(MAIN_PID, ev)
            more = _make_gc_events(N_GC, MAIN_PID, 1_500_000_000 + 100_000_000)

            def writer() -> None:
                for ev in more:
                    exporter.add_event(MAIN_PID, ev)

            def closer() -> None:
                exporter.close()

            captured = _run_two_threads([writer, closer])
            for exc in captured:
                raise exc

            completes = capture.count_completes()
            # Pre-fill must always arrive. The remaining events may or may
            # not depending on whether the close beat the writer or vice
            # versa, but the total must be in [PRE_FILL, PRE_FILL + N_GC].
            assert PRE_FILL <= completes <= PRE_FILL + N_GC, (
                f"[{exporter_factory.id}] expected between {PRE_FILL} and "
                f"{PRE_FILL + N_GC} complete events, got {completes}"
            )
        finally:
            bundle.teardown()

    def test_double_close_safe(
        self, exporter_factory: ExporterFactory, tmp_path: Path
    ) -> None:
        bundle = exporter_factory.build(tmp_path, threshold=1)
        try:
            exporter, capture = bundle.exporter, bundle.capture
            for ev in _make_gc_events(5, MAIN_PID, 1_500_000_000):
                exporter.add_event(MAIN_PID, ev)

            def closer_a() -> None:
                exporter.close()

            def closer_b() -> None:
                exporter.close()

            captured = _run_two_threads([closer_a, closer_b])
            for exc in captured:
                raise exc

            assert capture.count_completes() == 5, (
                f"[{exporter_factory.id}] expected exactly 5 complete events, "
                f"got {capture.count_completes()}"
            )
        finally:
            bundle.teardown()
