"""Benchmarks for reaching a monitored process.

ADR-0020 attaches to a process once and reads it many times because finding a
target costs roughly two orders of magnitude more than reading it. That gap is
a cost and nothing else, so this is where a change that rebuilds the attachment
on every read shows up. The two benchmarks are the two halves of the gap, and
they are only worth reading against each other.

Both drive the real ``_remote_debugging`` against a real subprocess. A read
copies the whole fixed-size ring whatever the target is doing, so the target's
allocation rate does not move the measurement.
"""

from __future__ import annotations

import pytest
from pytest_codspeed import BenchmarkFixture

from gcmon.events_reader import RemoteEventsReader
from tests.test_events_reader import running_target


@pytest.mark.benchmark
def test_remote_reader_reads_a_held_attachment(benchmark: BenchmarkFixture) -> None:
    """The steady state: every read after the first one of a pid.

    The first read is paid outside the measurement, so a regression that lets
    go of the attachment lands here as the attach cost below.
    """
    with running_target() as target:
        reader = RemoteEventsReader()
        reader.read(target.pid)

        records = benchmark(lambda: reader.read(target.pid))

    assert {r.gen for r in records} == {0, 1, 2}, "a real read yields a row per generation"


@pytest.mark.benchmark
def test_remote_reader_attaches_and_reads(benchmark: BenchmarkFixture) -> None:
    """The first read of a pid, which is what gcmon used to pay every poll.

    Here to give the benchmark above something to be small against. It tracks
    CPython's attach cost rather than gcmon's, so a move in it is upstream news.
    """
    with running_target() as target:
        records = benchmark(lambda: RemoteEventsReader().read(target.pid))

    assert {r.gen for r in records} == {0, 1, 2}, "a real read yields a row per generation"
