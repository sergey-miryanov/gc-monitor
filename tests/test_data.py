import msgspec
import pytest

from gcmon.data import GCStatsInfo, InstantMsg, from_mapping, instant_msg
from gcmon.protocol import has_deduce_unreachable, has_incremental, has_mark_alive


class TestGCStatsInfo:
    def test_struct_creation(self, simple_item):
        assert simple_item.gen == 0
        assert simple_item.iid == 1
        assert simple_item.ts_start == 1_000_000
        assert simple_item.ts_stop == 2_000_000
        assert simple_item.heap_size == 1024
        assert simple_item.collections == 5
        assert simple_item.collected == 50
        assert simple_item.uncollectable == 0
        assert simple_item.candidates == 10
        assert simple_item.duration == 0.005


class TestInstantMsg:
    def test_instant_msg_creation(self, instant_item):
        assert instant_item.type == "i"
        assert instant_item.name == "start GC monitor"
        assert instant_item.ts == 5_000_000

    def test_instant_msg_with_explicit_ts(self):
        msg = instant_msg("test event", 12345)
        assert isinstance(msg, InstantMsg)
        assert msg.type == "i"
        assert msg.name == "test event"
        assert msg.ts == 12345


class TestFromMapping:
    def test_regular_item(self, gc_stats_dict):
        result = from_mapping(gc_stats_dict)
        assert isinstance(result, GCStatsInfo)
        assert not has_incremental(result)
        assert result.gen == 0
        assert result.iid == 1
        assert result.ts_start == 1_000_000
        assert result.ts_stop == 2_000_000
        assert result.heap_size == 1024
        assert result.collections == 5
        assert result.collected == 50
        assert result.uncollectable == 0
        assert result.candidates == 10
        assert result.duration == 0.005

    def test_incremental_item(self, incremental_dict):
        result = from_mapping(incremental_dict)
        assert isinstance(result, GCStatsInfo)
        assert has_incremental(result)
        assert has_mark_alive(result)
        assert has_deduce_unreachable(result)
        assert result.gen == 1
        assert result.iid == 2
        assert result.ts_start == 3_000_000
        assert result.ts_stop == 4_000_000
        assert result.heap_size == 2048
        assert result.collections == 10
        assert result.collected == 100
        assert result.uncollectable == 1
        assert result.candidates == 20
        assert result.duration == 0.01
        assert result.increment_size == 500
        assert result.alive_size == 300
        assert result.ts_mark_alive_start == 3_000_500
        assert result.ts_mark_alive_stop == 3_001_000
        assert result.ts_fill_increment_start == 3_001_500
        assert result.ts_fill_increment_stop == 3_002_000
        assert result.ts_deduce_unreachable_start == 3_002_500
        assert result.ts_deduce_unreachable_stop == 3_003_000
        assert result.finalized_garbage_count == 42
        assert result.clear_weakrefs_count == 7
        assert result.deleted_garbage_count == 13

    def test_from_mapping_returns_instant_msg(self, instant_dict):
        result = from_mapping(instant_dict)
        assert isinstance(result, InstantMsg)
        assert result.type == "i"
        assert result.name == "start GC monitor"
        assert result.ts == 5_000_000

    def test_from_mapping_empty_raises(self):
        with pytest.raises(msgspec.ValidationError):
            from_mapping({})
