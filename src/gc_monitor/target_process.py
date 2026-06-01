from typing import Protocol, override, runtime_checkable


@runtime_checkable
class TargetProcess(Protocol):
    @property
    def pid(self) -> int: ...


class ExternalProcess(TargetProcess):
    def __init__(self, pid: int):
        self._pid = pid

    @property
    @override
    def pid(self) -> int:
        return self._pid
