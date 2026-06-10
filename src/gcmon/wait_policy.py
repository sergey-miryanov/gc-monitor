import time
from typing import Protocol, override, runtime_checkable

from .poll_status import PollStatus


@runtime_checkable
class WaitPolicy(Protocol):
    def wait(self, status: PollStatus) -> bool: ...


class WaitPolicyFactory(Protocol):
    def __call__(self) -> WaitPolicy: ...


class NoWaitPolicy(WaitPolicy):
    @override
    def wait(self, status: PollStatus) -> bool:
        return status == PollStatus.OK


class StartupTimeoutPolicy(WaitPolicy):
    """
    Wait for process to become valid during startup.

    If we see INVALID_PROCESS before any OK status, the process might
    still be initializing — wait up to `timeout` seconds.

    If we see INVALID_PROCESS after seeing OK at least once, the process
    has died — stop immediately (it won't come back).
    """

    def __init__(self, timeout: int) -> None:
        self._start_time = time.monotonic()
        self._timeout = timeout
        self._has_seen_alive = False

    @override
    def wait(self, status: PollStatus) -> bool:
        match status:
            case PollStatus.OK:
                self._has_seen_alive = True
                return True
            case PollStatus.FAIL:
                return False
            case PollStatus.INVALID_PROCESS:
                if not self._has_seen_alive:
                    # Process hasn't started yet — wait with timeout
                    return (time.monotonic() - self._start_time) < self._timeout
                # Process was alive but is now dead — stop immediately
                return False
            case _:
                raise ValueError(f"Unknown status: {status}")
