import pytest

from gc_monitor.data import GCStatsInfo, InstantMsg


@pytest.fixture
def simple_item():
    return GCStatsInfo(
        gen=0,
        iid=1,
        ts_start=1_000_000,
        ts_stop=2_000_000,
        heap_size=1024,
        collections=5,
        collected=50,
        uncollectable=0,
        candidates=10,
        duration=0.005,
    )


@pytest.fixture
def incremental_item():
    return GCStatsInfo(
        gen=1,
        iid=2,
        ts_start=3_000_000,
        ts_stop=4_000_000,
        heap_size=2048,
        collections=10,
        collected=100,
        uncollectable=1,
        candidates=20,
        duration=0.01,
        increment_size=500,
        alive_size=300,
        ts_mark_alive_start=3_000_500,
        ts_mark_alive_stop=3_001_000,
        ts_fill_increment_start=3_001_500,
        ts_fill_increment_stop=3_002_000,
        ts_deduce_unreachable_start=3_002_500,
        ts_deduce_unreachable_stop=3_003_000,
        ts_handle_weakref_callbacks_start=3_003_000,
        ts_handle_weakref_callbacks_stop=3_004_000,
        ts_finalize_garbage_stop=3_005_000,
        finalized_garbage_count=42,
        ts_handle_resurected_stop=3_006_000,
        ts_clear_weakrefs_stop=3_007_000,
        clear_weakrefs_count=7,
        ts_delete_garbage_start=3_008_000,
        ts_delete_garbage_stop=3_009_000,
        deleted_garbage_count=13,
    )


def create_instant_msg(name: str="start GC monitor", ts:int = 5_000_000):
    return InstantMsg(type="i", name=name, ts=ts)


@pytest.fixture
def instant_item():
    return create_instant_msg()
