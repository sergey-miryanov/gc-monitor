"""Tests for the warning a run gets when it read too little of its target.

`StreamingStats` answers whether coverage dropped; deciding that is worth
saying, wording it and saying it once are the monitor's, so every test here
reads the log. The stats side reads the answer instead, in
`tests/stats/test_stats.py`.

Batches are hand-built and go through `_ingest`, the in-memory pattern
`test_monitor_cursor.py` uses: no target process, and a `collections` counter
that skips is the whole of what makes a run lossy.
"""

from collections.abc import Sequence
from itertools import count

import pytest

from gcmon.data import GCStatsInfo
from gcmon.monitor import EventsMonitor
from tests.helpers import create_mock_stats_item

PID = 4242
OTHER_PID = 4243

ADVISORY = "of collections observed"

# One collection's pause and the interval between two of them, in nanoseconds.
PAUSE_NS = 1_000_000
PERIOD_NS = 2 * PAUSE_NS

_POLL_CLOCK = count(1_000_000_000, 100_000_000)


def ring(collections: Sequence[int], gen: int = 0) -> list[GCStatsInfo]:
    """A poll's ring for one generation, holding the records at *collections*.

    ``duration`` is the target's own cumulative pause total, which is what the
    loss reconstruction reads, so it counts every run up to the record rather
    than the ones this poll returned.
    """
    return [
        create_mock_stats_item(
            gen=gen,
            collections=n,
            ts_start=n * PERIOD_NS,
            ts_stop=n * PERIOD_NS + PAUSE_NS,
            duration=n * PAUSE_NS / 1e9,
        )
        for n in collections
    ]


def poll(monitor: EventsMonitor, pid: int, collections: Sequence[int], gen: int = 0) -> None:
    """One poll of *pid*, at the next instant on a clock shared by this file.

    `_ingest` bounds a loss record by the two polls around it, so it has to be
    told when this one happened. Nothing here reads those instants, so they
    only have to increase.
    """
    monitor._ingest(pid, ring(collections, gen=gen), next(_POLL_CLOCK))


class TestCoverageWarning:
    def test_it_fires_below_the_threshold(self, monitor: EventsMonitor, caplog: pytest.LogCaptureFixture) -> None:
        """Two records read of ten that ran: 20%, under the 90% floor."""
        poll(monitor, PID, [1])
        poll(monitor, PID, [10])

        assert f"PID {PID} generation 0: only 20% {ADVISORY}" in caplog.text

    def test_it_stays_quiet_above_the_threshold(self, monitor: EventsMonitor, caplog: pytest.LogCaptureFixture) -> None:
        poll(monitor, PID, range(1, 100))
        poll(monitor, PID, [101])

        assert ADVISORY not in caplog.text

    def test_it_fires_once_across_many_polls(self, monitor: EventsMonitor, caplog: pytest.LogCaptureFixture) -> None:
        """A lossy run loses records every tick, so a warning per poll would
        bury the trace it is warning about."""
        for collections in range(1, 200, 20):
            poll(monitor, PID, [collections])

        assert caplog.text.count(ADVISORY) == 1

    def test_one_warning_covers_every_pid(self, monitor: EventsMonitor, caplog: pytest.LogCaptureFixture) -> None:
        """The latch is per run, not per pid: the advice is about the poll
        rate, which no pid owns."""
        poll(monitor, PID, [1])
        poll(monitor, OTHER_PID, [1])
        poll(monitor, PID, [10])
        poll(monitor, OTHER_PID, [10])

        assert caplog.text.count(ADVISORY) == 1

    def test_this_polls_own_records_count_before_it_fires(
        self, monitor: EventsMonitor, caplog: pytest.LogCaptureFixture
    ) -> None:
        """`_ingest` records every ring's gap before it updates any of them, so
        a check made at `record_loss` would divide this poll's gap into the
        sample as it stood before the poll.

        Two polls, 2 records then 100 with 1 lost: measured that early the
        coverage reads 2/3, and the latch would keep that figure for a run that
        ends at 99%.
        """
        poll(monitor, PID, [1, 2])
        poll(monitor, PID, range(4, 104))

        assert ADVISORY not in caplog.text

    def test_it_says_what_survives_and_what_to_try(
        self, monitor: EventsMonitor, caplog: pytest.LogCaptureFixture
    ) -> None:
        poll(monitor, PID, [1])
        poll(monitor, PID, [10])

        assert "reconstructed and exact" in caplog.text
        assert "--rate" in caplog.text

    def test_it_names_no_ring_geometry(self, monitor: EventsMonitor, caplog: pytest.LogCaptureFixture) -> None:
        """How many slots the target's ring holds is not something an operator
        can act on, which is the whole reason the wording moved."""
        poll(monitor, PID, [1])
        poll(monitor, PID, [10])

        assert ADVISORY in caplog.text, "the warning has to fire for this to test anything"
        assert "ring" not in caplog.text
        assert "record" not in caplog.text
