from collections.abc import Mapping
from types import SimpleNamespace
from typing import ClassVar

from tests.data_helpers import (  # noqa: F401
    incremental_item,
    instant_item,
    simple_item,
)

from gc_monitor.protocol import (
    has_clear_weakrefs,
    has_deduce_unreachable,
    has_delete_garbage,
    has_finalize_garbage,
    has_gen,
    has_handle_resurrected,
    has_handle_weakrefs,
    has_incremental,
    has_mark_alive,
    has_pause_ts,
    is_gc_stats,
    is_instant,
    to_mapping,
)


class TestIsGC:
    def test_regular_returns_true(self, simple_item):
        assert is_gc_stats(simple_item) is True

    def test_incremental_returns_true(self, incremental_item):
        assert is_gc_stats(incremental_item) is True

    def test_instant_returns_false(self, instant_item):
        assert is_gc_stats(instant_item) is False


class TestIsInstant:
    def test_instant_returns_true(self, instant_item):
        assert is_instant(instant_item) is True

    def test_gc_stats_returns_false(self, simple_item):
        assert is_instant(simple_item) is False

    def test_incremental_returns_false(self, incremental_item):
        assert is_instant(incremental_item) is False


class TestHasGuards:
    def test_has_pause_ts_true(self):
        item = SimpleNamespace(ts_start=100)
        assert has_pause_ts(item)

    def test_has_pause_ts_false(self):
        item = SimpleNamespace(gen=0)
        assert not has_pause_ts(item)

    def test_has_incremental_true(self):
        item = SimpleNamespace(increment_size=500)
        assert has_incremental(item)

    def test_has_incremental_false(self):
        item = SimpleNamespace(gen=0)
        assert not has_incremental(item)

    def test_has_mark_alive_true(self):
        item = SimpleNamespace(alive_size=300)
        assert has_mark_alive(item)

    def test_has_mark_alive_false(self):
        item = SimpleNamespace(gen=0)
        assert not has_mark_alive(item)

    def test_has_deduce_unreachable_true(self):
        item = SimpleNamespace(ts_deduce_unreachable_start=100)
        assert has_deduce_unreachable(item)

    def test_has_deduce_unreachable_false(self):
        item = SimpleNamespace(gen=0)
        assert not has_deduce_unreachable(item)

    def test_has_handle_weakrefs_true(self):
        item = SimpleNamespace(ts_handle_weakref_callbacks_start=100)
        assert has_handle_weakrefs(item)

    def test_has_handle_weakrefs_false(self):
        item = SimpleNamespace(gen=0)
        assert not has_handle_weakrefs(item)

    def test_has_finalize_garbage_true(self):
        item = SimpleNamespace(ts_finalize_garbage_stop=100)
        assert has_finalize_garbage(item)

    def test_has_finalize_garbage_false(self):
        item = SimpleNamespace(gen=0)
        assert not has_finalize_garbage(item)

    def test_has_handle_resurrected_true(self):
        item = SimpleNamespace(ts_handle_resurected_stop=100)
        assert has_handle_resurrected(item)

    def test_has_handle_resurrected_false(self):
        item = SimpleNamespace(gen=0)
        assert not has_handle_resurrected(item)

    def test_has_clear_weakrefs_true(self):
        item = SimpleNamespace(ts_clear_weakrefs_stop=100)
        assert has_clear_weakrefs(item)

    def test_has_clear_weakrefs_false(self):
        item = SimpleNamespace(gen=0)
        assert not has_clear_weakrefs(item)

    def test_has_delete_garbage_true(self):
        item = SimpleNamespace(ts_delete_garbage_start=100)
        assert has_delete_garbage(item)

    def test_has_delete_garbage_false(self):
        item = SimpleNamespace(gen=0)
        assert not has_delete_garbage(item)

    def test_has_gen_true(self):
        item = SimpleNamespace(gen=1)
        assert has_gen(item)

    def test_has_gen_false(self):
        item = SimpleNamespace(other=42)
        assert not has_gen(item)


class TestToMappingPartial:
    BASE: ClassVar[dict[str, int | float]] = {
        "gen": 0, "iid": 1, "ts_start": 1_000_000, "ts_stop": 2_000_000,
        "heap_size": 1024, "collections": 5, "collected": 50,
        "uncollectable": 0, "candidates": 10, "duration": 0.005,
    }

    def _make_item(self, **extra: int | float) -> SimpleNamespace:
        return SimpleNamespace(**self.BASE, **extra)

    def test_fill_increment_only(self):
        item = self._make_item(
            increment_size=500,
            ts_fill_increment_start=1_000_500,
            ts_fill_increment_stop=1_001_000,
        )
        result = to_mapping(item)
        assert result["increment_size"] == 500
        assert result["ts_fill_increment_start"] == 1_000_500
        assert result["ts_fill_increment_stop"] == 1_001_000
        assert "alive_size" not in result
        assert "ts_mark_alive_start" not in result
        assert "ts_deduce_unreachable_start" not in result
        assert "ts_handle_weakref_callbacks_start" not in result
        assert "ts_finalize_garbage_stop" not in result
        assert "ts_handle_resurected_stop" not in result
        assert "ts_clear_weakrefs_stop" not in result
        assert "ts_delete_garbage_start" not in result

    def test_mark_alive_only(self):
        item = self._make_item(
            alive_size=300,
            ts_mark_alive_start=1_000_500,
            ts_mark_alive_stop=1_001_000,
        )
        result = to_mapping(item)
        assert result["alive_size"] == 300
        assert result["ts_mark_alive_start"] == 1_000_500
        assert result["ts_mark_alive_stop"] == 1_001_000
        assert "increment_size" not in result
        assert "ts_fill_increment_start" not in result
        assert "ts_deduce_unreachable_start" not in result
        assert "ts_handle_weakref_callbacks_start" not in result
        assert "ts_finalize_garbage_stop" not in result
        assert "ts_handle_resurected_stop" not in result
        assert "ts_clear_weakrefs_stop" not in result
        assert "ts_delete_garbage_start" not in result

    def test_deduce_unreachable_only(self):
        item = self._make_item(
            ts_deduce_unreachable_start=1_000_500,
            ts_deduce_unreachable_stop=1_001_000,
        )
        result = to_mapping(item)
        assert result["ts_deduce_unreachable_start"] == 1_000_500
        assert result["ts_deduce_unreachable_stop"] == 1_001_000
        assert "increment_size" not in result
        assert "alive_size" not in result
        assert "ts_mark_alive_start" not in result
        assert "ts_handle_weakref_callbacks_start" not in result
        assert "ts_finalize_garbage_stop" not in result
        assert "ts_handle_resurected_stop" not in result
        assert "ts_clear_weakrefs_stop" not in result
        assert "ts_delete_garbage_start" not in result

    def test_all_partial_phases(self):
        item = self._make_item(
            increment_size=500, alive_size=300,
            ts_mark_alive_start=1_000_500, ts_mark_alive_stop=1_001_000,
            ts_fill_increment_start=1_001_500, ts_fill_increment_stop=1_002_000,
            ts_deduce_unreachable_start=1_002_500, ts_deduce_unreachable_stop=1_003_000,
            ts_handle_weakref_callbacks_start=1_003_000,
            ts_handle_weakref_callbacks_stop=1_004_000,
            ts_finalize_garbage_stop=1_005_000,
            ts_handle_resurected_stop=1_006_000,
            ts_clear_weakrefs_stop=1_007_000,
            ts_delete_garbage_start=1_008_000,
            ts_delete_garbage_stop=1_009_000,
        )
        result = to_mapping(item)
        assert result["increment_size"] == 500
        assert result["alive_size"] == 300
        assert result["ts_mark_alive_start"] == 1_000_500
        assert result["ts_mark_alive_stop"] == 1_001_000
        assert result["ts_fill_increment_start"] == 1_001_500
        assert result["ts_fill_increment_stop"] == 1_002_000
        assert result["ts_deduce_unreachable_start"] == 1_002_500
        assert result["ts_deduce_unreachable_stop"] == 1_003_000
        assert result["ts_handle_weakref_callbacks_start"] == 1_003_000
        assert result["ts_handle_weakref_callbacks_stop"] == 1_004_000
        assert result["ts_finalize_garbage_stop"] == 1_005_000
        assert result["ts_handle_resurected_stop"] == 1_006_000
        assert result["ts_clear_weakrefs_stop"] == 1_007_000
        assert result["ts_delete_garbage_start"] == 1_008_000
        assert result["ts_delete_garbage_stop"] == 1_009_000


class TestToMapping:
    def test_regular_item(self, simple_item):
        result = to_mapping(simple_item)

        assert isinstance(result, Mapping)
        assert result["gen"] == 0
        assert result["iid"] == 1
        assert result["ts_start"] == 1_000_000
        assert result["ts_stop"] == 2_000_000
        assert result["heap_size"] == 1024
        assert result["collections"] == 5
        assert result["collected"] == 50
        assert result["uncollectable"] == 0
        assert result["candidates"] == 10
        assert result["duration"] == 0.005
        assert "increment_size" not in result

    def test_incremental_item(self, incremental_item):
        result = to_mapping(incremental_item)

        assert isinstance(result, Mapping)
        assert result["gen"] == 1
        assert result["iid"] == 2
        assert result["ts_start"] == 3_000_000
        assert result["ts_stop"] == 4_000_000
        assert result["heap_size"] == 2048
        assert result["collections"] == 10
        assert result["collected"] == 100
        assert result["uncollectable"] == 1
        assert result["candidates"] == 20
        assert result["duration"] == 0.01
        assert result["increment_size"] == 500
        assert result["alive_size"] == 300
        assert result["ts_mark_alive_start"] == 3_000_500
        assert result["ts_mark_alive_stop"] == 3_001_000
        assert result["ts_fill_increment_start"] == 3_001_500
        assert result["ts_fill_increment_stop"] == 3_002_000
        assert result["ts_deduce_unreachable_start"] == 3_002_500
        assert result["ts_deduce_unreachable_stop"] == 3_003_000
        assert result["ts_handle_weakref_callbacks_start"] == 3_003_000
        assert result["ts_handle_weakref_callbacks_stop"] == 3_004_000
        assert result["ts_finalize_garbage_stop"] == 3_005_000
        assert result["ts_handle_resurected_stop"] == 3_006_000
        assert result["ts_clear_weakrefs_stop"] == 3_007_000
        assert result["ts_delete_garbage_start"] == 3_008_000
        assert result["ts_delete_garbage_stop"] == 3_009_000

    def test_instant_item(self, instant_item):
        result = to_mapping(instant_item)

        assert isinstance(result, Mapping)
        assert result["type"] == "i"
        assert result["name"] == "start GC monitor"
        assert result["ts"] == 5_000_000

    def test_to_mapping_unknown_type_raises(self):
        import pytest
        with pytest.raises(NotImplementedError, match="Unknown item type"):
            to_mapping("not a valid item")  # type: ignore[arg-type]
