from typing import Protocol, Self, override, runtime_checkable


@runtime_checkable
class TargetProcess(Protocol):
    @property
    def pid(self) -> int: ...


class ProcessRunnerFactory(Protocol):
    def __call__(self, control_address: str) -> ProcessFactory: ...


class ProcessFactory(Protocol):
    def start(self) -> TargetProcess: ...
    def __enter__(self) -> Self: ...
    def __exit__(self, *args: object) -> None: ...

    @property
    def returncode(self) -> int | None: ...

    def wait(self, timeout: float | None = None) -> None: ...


class ExternalProcess(TargetProcess):
    def __init__(self, pid: int):
        self._pid = pid

    @property
    @override
    def pid(self) -> int:
        return self._pid

    def start(self) -> ExternalProcess:
        return self

    @property
    def returncode(self) -> int | None:
        return None

    def wait(self, timeout: float | None = None) -> None:
        """No-op for external processes."""

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        pass
