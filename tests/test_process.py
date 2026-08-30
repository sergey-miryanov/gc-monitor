"""What identifies a `Process`, and what it merely carries."""

from gcmon.model.process import Process


class TestTwoProcessesAreTheSameOne:
    """Identity is the pid and the epoch. `start_ts` and `cmdline` are what
    gcmon learned about the process, so a caller holding only the pair reaches
    what was filed under it."""

    LEARNED = Process(12345, 1, 900, ("python", "-m", "worker"))

    def test_what_gcmon_learned_is_not_part_of_it(self) -> None:
        assert Process(12345, 1, 0) == self.LEARNED

    def test_it_keys_a_dict_the_same_way(self) -> None:
        assert {self.LEARNED: "rings"}[Process(12345, 1, 0)] == "rings"

    def test_unequal_is_the_negation_of_equal(self) -> None:
        """msgspec keeps a `__ne__` of its own on the class, and it does not
        read `__eq__`. Unwritten, the two answer yes at once and every scan
        comparing processes silently matches nothing."""
        assert (Process(12345, 1, 0) != self.LEARNED) is False

    def test_a_successor_on_the_pid_is_another_process(self) -> None:
        assert Process(12345, 2, 0) != Process(12345, 1, 0)

    def test_a_process_is_not_a_pid(self) -> None:
        assert Process(12345, 1, 0) != 12345


class TestProcessesSort:
    def test_by_pid_then_epoch(self) -> None:
        """The order the `--stats` table prints its blocks in."""
        out_of_order = [Process(2, 1, 0), Process(1, 2, 0), Process(1, 1, 0)]

        assert sorted(out_of_order) == [Process(1, 1, 0), Process(1, 2, 0), Process(2, 1, 0)]

    def test_what_gcmon_learned_does_not_decide_it(self) -> None:
        assert not (Process(1, 1, 900) < Process(1, 1, 0))


class TestProcessReadsAsItsPid:
    def test_the_first_to_hold_a_pid_is_unmarked(self) -> None:
        assert str(Process(12345, 1, 0)) == "12345"

    def test_a_later_one_carries_the_epoch(self) -> None:
        assert str(Process(12345, 2, 0)) == "12345#2"

    def test_the_suffix_is_available_on_its_own(self) -> None:
        """The table writes `12345:0#2`, so it needs the piece rather than
        the whole label."""
        assert Process(12345, 2, 0).epoch_suffix == "#2"
