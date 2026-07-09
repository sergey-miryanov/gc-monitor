# TODO: remove this code when 3.15 will be supported by mypy and pyrefly.
# This code will be replaced by:
# https://github.com/python/typeshed/blob/main/stdlib/_remote_debugging.pyi
from typing import Protocol

class GCStatsInfo(Protocol):
    gen: int
    iid: int
    ts_start: int
    ts_stop: int
    heap_size: int
    collections: int
    collected: int
    uncollectable: int
    candidates: int
    duration: float

def get_child_pids(pid: int, *, recursive: bool) -> list[int]: ...
def get_gc_stats(pid: int, *, all_interpreters: bool) -> list[GCStatsInfo]: ...
