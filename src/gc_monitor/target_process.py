from typing import Protocol, Self, TypedDict, override, runtime_checkable


class TargetProcessMetadata(TypedDict):
    pid: int


@runtime_checkable
class TargetProcess(Protocol):
    @property
    def pid(self) -> int: ...
    def metadata(self) -> TargetProcessMetadata: ...

class ProcessFactory(Protocol):
    def start(self)-> TargetProcess:...

    def __enter__(self) -> Self:...
    def __exit__(self, *args: object) -> None:...

class ExternalProcess(TargetProcess):
    def __init__(self, pid: int):
        self._pid = pid

    @property
    @override
    def pid(self) -> int:
        return self._pid

    @override
    def metadata(self) -> TargetProcessMetadata:
        return {
            "pid": self._pid,
        }

    def start(self) -> ExternalProcess:
        return self

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        pass
