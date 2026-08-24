"""The marks the hook writes: where each benchmark ran, on the worker's timeline.

Driven through a real ``ControlClient`` into a real ``ControlServer``, which is
the highest seam that sees a mark end to end without a monitor, a target or
pyperf.
"""

import os
import time
from collections.abc import Generator, Sequence
from pathlib import Path
from typing import NamedTuple, override

import pytest

from gcmon.control.control_server import CONTROL_ADDRESS_ENV, ControlServer
from gcmon.model.marks import BEGIN, END, Mark, parse_mark
from gcmon.model.protocol import TInstantMsg
from gcmon.pyperf.hook import GCMonitorHook, gcmon_hook
from tests.helpers import MockExporter


class Marked(NamedTuple):
    """One mark as the exporter saw it, with the pid it landed on."""

    pid: int
    mark: Mark
    ts: int


class Sink(NamedTuple):
    server: ControlServer
    exporter: MockExporter

    def marks(self) -> list[Marked]:
        found = []
        for pid, msg in list(self.exporter.instant_events):
            mark = parse_mark(msg.name)
            if mark is not None:
                found.append(Marked(pid, mark, msg.ts))
        return found

    def wait_for(self, count: int, timeout: float = 5.0) -> list[Marked]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            found = self.marks()
            if len(found) >= count:
                return found
            time.sleep(0.01)
        return self.marks()


@pytest.fixture
def sink(monkeypatch: pytest.MonkeyPatch) -> Generator[Sink]:
    """A listening control plane, reached the way a worker reaches one."""
    exporter = MockExporter()
    server = ControlServer(exporter)
    server.start()
    monkeypatch.setenv(CONTROL_ADDRESS_ENV, server.address)
    try:
        yield Sink(server, exporter)
    finally:
        server.close()


def _sides(marks: Sequence[Marked]) -> list[str]:
    return [m.mark.side for m in marks]


class TestAccumulateAndLand:
    def test_two_regions_land_as_four_instants_at_teardown(self, sink: Sink) -> None:
        hook = gcmon_hook()

        with hook:
            pass
        with hook:
            pass

        assert sink.marks() == [], "a mark crossed a process boundary while the benchmark was running"

        hook.teardown({"name": "bm_base64"})

        marks = sink.wait_for(4)
        assert _sides(marks) == [BEGIN, END, BEGIN, END]
        assert [m.mark.bench for m in marks] == ["bm_base64"] * 4
        first, second = marks[0].mark.region, marks[2].mark.region
        assert [m.mark.region for m in marks] == [first, first, second, second]
        assert second == first + 1

    def test_the_marks_carry_the_benchmark_s_own_instants(self, sink: Sink) -> None:
        hook = gcmon_hook()

        before = time.monotonic_ns()
        with hook:
            time.sleep(0.01)
        after = time.monotonic_ns()

        time.sleep(0.05)
        sent_no_earlier_than = time.monotonic_ns()
        hook.teardown({"name": "bm_base64"})

        begin, end = sink.wait_for(2)
        assert before <= begin.ts < end.ts <= after
        assert end.ts < sent_no_earlier_than, "the mark was stamped at send time, not at the benchmark"

    def test_the_marks_land_on_the_worker_s_pid(self, sink: Sink) -> None:
        hook = gcmon_hook()
        with hook:
            pass
        hook.teardown({"name": "bm_base64"})

        assert {m.pid for m in sink.wait_for(2)} == {os.getpid()}

    def test_a_name_that_is_not_a_field_is_sanitized(self, sink: Sink) -> None:
        hook = gcmon_hook()
        with hook:
            pass
        hook.teardown({"name": "bm:odd name"})

        assert {m.mark.bench for m in sink.wait_for(2)} == {"bm_odd_name"}


class TestRegionNumbering:
    def test_a_second_hook_instance_continues_the_numbering(self, sink: Sink) -> None:
        first = gcmon_hook()
        with first:
            pass
        first.teardown({"name": "bm_base64"})
        assert sink.wait_for(2)

        second = gcmon_hook()
        with second:
            pass
        second.teardown({"name": "bm_base64"})

        regions = [m.mark.region for m in sink.wait_for(4)]
        assert regions[2] == regions[0] + 1, "the second instance restarted the count and reused a mark name"

    def test_regions_of_one_instance_are_numbered_in_order(self, sink: Sink) -> None:
        hook = gcmon_hook()
        for _ in range(3):
            with hook:
                pass
        hook.teardown({"name": "bm_base64"})

        regions = [m.mark.region for m in sink.wait_for(6)]
        assert regions == sorted(regions)
        assert regions[0] == regions[1] < regions[2] == regions[3] < regions[4] == regions[5]


class TestTheMarksInATrace:
    """What the operator opens: marks as instants on the worker's own process."""

    def test_the_marks_reach_a_perfetto_trace_on_the_worker_s_process(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from perfetto.trace_processor import TraceProcessor, TraceProcessorConfig

        from gcmon.exporters.perfetto_exporter import PerfettoExporter

        class CountingExporter(PerfettoExporter):
            """The trace the operator opens, plus a way to wait for it."""

            instants = 0

            @override
            def add_instant_event(self, pid: int, item: TInstantMsg) -> None:
                super().add_instant_event(pid, item)
                self.instants += 1

        path = tmp_path / "marks.pftrace"
        exporter = CountingExporter(output_path=path, flush_threshold=1000)
        server = ControlServer(exporter)
        server.start()
        try:
            monkeypatch.setenv(CONTROL_ADDRESS_ENV, server.address)
            hook = gcmon_hook()
            with hook:
                pass
            hook.teardown({"name": "bm_base64"})
            deadline = time.monotonic() + 5
            while exporter.instants < 2 and time.monotonic() < deadline:
                time.sleep(0.01)
        finally:
            server.close()
            exporter.close()

        tp = TraceProcessor(trace=str(path), config=TraceProcessorConfig(load_timeout=300))
        try:
            rows = list(
                tp.query(
                    "SELECT s.name AS name FROM slice s "
                    "JOIN process_track pt ON s.track_id = pt.id "
                    "JOIN process p ON pt.upid = p.upid "
                    f"WHERE p.pid = {os.getpid()} AND s.dur = 0 AND s.name LIKE 'gcmon:%' "
                    "ORDER BY s.ts"
                )
            )
        finally:
            tp.close()

        marks = [parse_mark(row.name) for row in rows]
        assert len(marks) == 2, f"expected one region's pair of marks, got {[row.name for row in rows]}"
        assert marks[0] is not None and marks[1] is not None
        assert marks[0].side == BEGIN
        assert marks[1].side == END
        assert marks[0].bench == marks[1].bench == "bm_base64"
        assert marks[0].region == marks[1].region


class TestAnUnfinishedRegion:
    def test_a_region_whose_exit_never_ran_lands_nothing(self, sink: Sink) -> None:
        hook = gcmon_hook()

        hook.__enter__()
        hook.teardown({"name": "bm_base64"})

        assert sink.wait_for(1, timeout=0.5) == [], "half a region reached the trace"

    def test_a_finished_region_before_an_unfinished_one_still_lands(self, sink: Sink) -> None:
        hook = gcmon_hook()

        with hook:
            pass
        hook.__enter__()
        hook.teardown({"name": "bm_base64"})

        assert _sides(sink.wait_for(2)) == [BEGIN, END]


class TestTheHookDoesNothingElse:
    def test_the_hook_spawns_no_process(self, sink: Sink, monkeypatch: pytest.MonkeyPatch) -> None:
        """The monitor is the operator's, started once over the whole suite."""
        import subprocess

        def refuse(*args: object, **kwargs: object) -> None:
            raise AssertionError("the hook spawned a process")

        monkeypatch.setattr(subprocess, "Popen", refuse)

        hook = gcmon_hook()
        with hook:
            pass
        hook.teardown({"name": "bm_base64"})

        assert len(sink.wait_for(2)) == 2

    def test_the_hook_writes_no_file(self, sink: Sink, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)

        hook = gcmon_hook()
        with hook:
            pass
        hook.teardown({"name": "bm_base64"})
        assert len(sink.wait_for(2)) == 2

        assert list(tmp_path.iterdir()) == []

    def test_teardown_adds_no_key_to_the_metadata(self, sink: Sink) -> None:
        hook = gcmon_hook()
        with hook:
            pass

        metadata: dict[str, object] = {"name": "bm_base64", "loops": 4}
        hook.teardown(metadata)

        assert metadata == {"name": "bm_base64", "loops": 4}

    def test_a_hook_is_built_without_touching_the_control_plane(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Nothing connects until there are marks to send."""
        monkeypatch.delenv(CONTROL_ADDRESS_ENV, raising=False)

        hook = gcmon_hook()

        assert isinstance(hook, GCMonitorHook)
