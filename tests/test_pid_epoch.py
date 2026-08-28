"""Tests for the shared `#N` suffix naming which process held a pid."""

from gcmon.support.pid_epoch import epoch_suffix


class TestEpochSuffix:
    def test_first_process_on_a_pid_is_unmarked(self) -> None:
        assert epoch_suffix(1) == ""

    def test_second_process_on_a_pid_is_marked(self) -> None:
        assert epoch_suffix(2) == "#2"

    def test_later_processes_keep_counting(self) -> None:
        assert epoch_suffix(17) == "#17"
