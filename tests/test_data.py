import msgspec
import pytest

from gcmon.data import GCStatsInfo, InstantMsg, LossMsg, duration_text, from_mapping, instant_msg
from gcmon.protocol import TMapping, has_deduce_unreachable, has_incremental, has_mark_alive, to_mapping


class TestGCStatsInfo:
    def test_struct_creation(self, simple_item: GCStatsInfo) -> None:
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
    def test_instant_msg_creation(self, instant_item: InstantMsg) -> None:
        assert instant_item.type == "i"
        assert instant_item.name == "start GC monitor"
        assert instant_item.ts == 5_000_000

    def test_instant_msg_with_explicit_ts(self) -> None:
        msg = instant_msg("test event", 12345)
        assert isinstance(msg, InstantMsg)
        assert msg.type == "i"
        assert msg.name == "test event"
        assert msg.ts == 12345


class TestFromMapping:
    def test_regular_item(self, gc_stats_dict: TMapping) -> None:
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

    def test_incremental_item(self, incremental_dict: TMapping) -> None:
        result = from_mapping(incremental_dict)
        assert isinstance(result, GCStatsInfo)
        # Common fields (always present)
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
        assert result.finalized_garbage_count == 42
        assert result.clear_weakrefs_count == 7
        assert result.deleted_garbage_count == 13
        # Incremental fields
        assert has_incremental(result)
        assert result.increment_size == 500
        assert result.ts_fill_increment_start == 3_001_500
        assert result.ts_fill_increment_stop == 3_002_000
        # Mark-alive fields
        assert has_mark_alive(result)
        assert result.alive_size == 300
        assert result.ts_mark_alive_start == 3_000_500
        assert result.ts_mark_alive_stop == 3_001_000
        # Deduce-unreachable fields
        assert has_deduce_unreachable(result)
        assert result.ts_deduce_unreachable_start == 3_002_500
        assert result.ts_deduce_unreachable_stop == 3_003_000

    def test_from_mapping_returns_instant_msg(self, instant_dict: TMapping) -> None:
        result = from_mapping(instant_dict)
        assert isinstance(result, InstantMsg)
        assert result.type == "i"
        assert result.name == "start GC monitor"
        assert result.ts == 5_000_000

    def test_from_mapping_empty_raises(self) -> None:
        with pytest.raises(msgspec.ValidationError):
            from_mapping({})


class TestLossMsg:
    def test_it_carries_the_one_generation_it_lost(self) -> None:
        msg = LossMsg(iid=0, gen=1, ts_start=1_000, ts_stop=2_000, lost_count=76)

        assert msg.gen == 1
        assert msg.lost_pause_ns == 0

    def test_neither_a_gc_record_nor_an_instant(self) -> None:
        """What keeps the existing branches in ``to_mapping``,
        ``convert_to_trace_format`` and the normalizers from claiming it. It
        carries a ``gen`` like a GC record does, so ``collections`` is what
        tells the two apart."""
        msg = LossMsg(iid=0, gen=0, ts_start=1_000, ts_stop=2_000)

        assert not hasattr(msg, "collections")
        assert not hasattr(msg, "type")

    def test_from_mapping_returns_loss_msg(self) -> None:
        result = from_mapping(
            {"pid": 42, "tid": -2, "iid": 1, "gen": 2, "ts_start": 1_000, "ts_stop": 2_000, "lost_count": 76}
        )

        assert isinstance(result, LossMsg)
        assert (result.iid, result.gen) == (1, 2)
        assert result.lost_count == 76

    def test_round_trips_through_a_mapping(self) -> None:
        msg = LossMsg(iid=1, gen=0, ts_start=1_000, ts_stop=2_000, lost_count=76, lost_pause_ns=8_100_000)

        assert from_mapping(to_mapping(msg)) == msg

    def test_a_zeroed_record_still_decodes_as_loss(self) -> None:
        """``from_mapping`` discriminates on ``lost_count`` being present, not
        truthy, so a record reporting nothing must still round-trip rather
        than come back as a GC record missing every field."""
        msg = LossMsg(iid=0, gen=1, ts_start=1_000, ts_stop=2_000)

        assert from_mapping(to_mapping(msg)) == msg


class TestDurationText:
    """The readable half of a pause total.

    Exactness lives in the `_ns` arg beside it; this one only has to be read
    correctly at a glance, which the digits are not.
    """

    @pytest.mark.parametrize(
        ("ns", "text"),
        [
            (3_316_458_100, "3s 316ms 458µs 100ns"),
            (5_000_000, "5ms"),
            (200, "200ns"),
            (1_000_000_100, "1s 100ns"),
            (90_000_000_000, "1m 30s"),
            (3_600_000_000_000, "1h"),
            (0, "0ns"),
        ],
    )
    def test_it_reads_as_a_duration(self, ns: int, text: str) -> None:
        assert duration_text(ns) == text

    def test_the_units_multiply_back_to_the_nanoseconds(self) -> None:
        """Every unit a component carries, against the number it came from.
        A wrong divisor produces text that still looks like a duration."""
        sizes = {"h": 3_600_000_000_000, "m": 60_000_000_000, "s": 1_000_000_000, "ms": 1_000_000, "µs": 1_000}

        for ns in (1, 999, 1_000, 3_316_458_100, 86_400_000_000_123):
            total = 0
            for part in duration_text(ns).split():
                digits = part.rstrip("hmsnµ")
                total += int(digits) * sizes.get(part.removeprefix(digits), 1)
            assert total == ns
