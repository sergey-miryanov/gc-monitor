from collections.abc import Mapping

from gc_monitor.protocol import is_gc_stats, is_incremental, is_instant, to_mapping

from tests.data_helpers import simple_item, incremental_item, instant_item


class TestIsGC:
    def test_regular_returns_true(self, simple_item):
        assert is_gc_stats(simple_item) is True

    def test_incremental_returns_true(self, incremental_item):
        assert is_gc_stats(incremental_item) is True

    def test_instant_returns_false(self, instant_item):
        assert is_gc_stats(instant_item) is False


class TestIsIncremental:
    def test_regular_returns_false(self, simple_item):
        assert is_incremental(simple_item) is False

    def test_incremental_returns_true(self, incremental_item):
        assert is_incremental(incremental_item) is True

    def test_instant_returns_false(self, instant_item):
        assert is_incremental(instant_item) is False

    def test_incremental_type_guard(self, incremental_item):
        result = is_incremental(incremental_item)
        if result:
            assert incremental_item.increment_size == 500
            assert incremental_item.alive_size == 300


class TestIsInstant:
    def test_instant_returns_true(self, instant_item):
        assert is_instant(instant_item) is True

    def test_gc_stats_returns_false(self, simple_item):
        assert is_instant(simple_item) is False

    def test_incremental_returns_false(self, incremental_item):
        assert is_instant(incremental_item) is False


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
        assert result["ts_deduce_uncreachable_start"] == 3_002_500
        assert result["ts_deduce_uncreachable_stop"] == 3_003_000

    def test_instant_item(self, instant_item):
        result = to_mapping(instant_item)

        assert isinstance(result, Mapping)
        assert result["type"] == "i"
        assert result["name"] == "start GC monitor"
        assert result["ts"] == 5_000_000
