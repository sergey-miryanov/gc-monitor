import time
from collections.abc import Callable, Generator
from typing import Any, Protocol, override, runtime_checkable


@runtime_checkable
class Runner(Protocol):
    def run(self, stop: Callable[[], bool]) -> Generator[None, Any]: ...


class InfinityRunner(Runner):
    @override
    def run(self, stop: Callable[[], bool]) -> Generator[None, Any]:
        while not stop():
            yield


class DurationRunner(Runner):
    def __init__(self, duration: float):
        self._duration = duration

    @override
    def run(self, stop: Callable[[], bool]) -> Generator[None, Any]:
        ts_start = time.monotonic()
        while not stop():
            yield
            ts = time.monotonic()
            if (ts - ts_start) > self._duration:
                break


def RunnerFactory(duration: float | None) -> Runner:
    if duration is not None:
        return DurationRunner(duration)

    return InfinityRunner()
