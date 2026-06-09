from collections.abc import Callable, Generator
from contextlib import contextmanager
from typing import Any


@contextmanager
def replace_signals(handler: Callable[[int, object], None]) -> Generator[None, Any]:
    import signal

    prev_sigint = signal.getsignal(signal.SIGINT)
    prev_sigterm = signal.getsignal(signal.SIGTERM)

    try:
        signal.signal(signal.SIGINT, handler)
        signal.signal(signal.SIGTERM, handler)

        yield None
    finally:
        signal.signal(signal.SIGINT, prev_sigint)
        signal.signal(signal.SIGTERM, prev_sigterm)
