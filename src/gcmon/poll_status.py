from enum import IntEnum, auto, unique


@unique
class PollStatus(IntEnum):
    OK = auto()
    FAIL = auto()
    INVALID_PROCESS = auto()


@unique
class ProcessLifecycle(IntEnum):
    """Process lifecycle transitions reported by the monitor.

    ``STARTED`` is emitted the first time a successful poll (``PollStatus.OK``)
    is observed for a pid. ``DIED`` is emitted the first time an
    ``INVALID_PROCESS`` poll is observed for a pid that was previously
    ``STARTED`` (including on graceful ``EventsMonitor.stop()``).
    """

    STARTED = auto()
    DIED = auto()
