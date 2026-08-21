import threading
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any


@contextmanager
def set_on_exit(event: threading.Event) -> Generator[threading.Event, Any]:
    """Set event on exit"""
    try:
        yield event
    finally:
        event.set()
