"""Child-side control plane API for start/stop/pause monitoring."""

import logging
import os
import threading
import time
from collections.abc import Callable, Generator
from contextlib import contextmanager, suppress
from multiprocessing.connection import Client, Connection
from typing import Any, Self

from gc_monitor.control.control_server import CONTROL_ADDRESS_ENV

logger = logging.getLogger("gc_monitor")


def connect_with_retry(
    address: str,
    timeout: float = 5.0,
    retry_interval: float = 0.05,
) -> Connection | None:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            return Client(address)
        except Exception as e:
            last_error = e
            time.sleep(retry_interval)
    logger.warning("Failed to connect to control plane: %s", last_error)
    return None


def _default_connect(address: str) -> Connection | None:
    return connect_with_retry(address)


class ControlClient:
    def __init__(
        self,
        control_address: str = "",
        *,
        connection_factory: Callable[[str], Connection | None] | None = None,
    ) -> None:
        self._control_address = control_address
        self._conn: Connection | None = None
        self._lock = threading.Lock()
        self._connect = connection_factory or _default_connect

    def _ensure_connected(self) -> Connection | None:
        if self._conn is not None:
            return self._conn
        with self._lock:
            if self._conn is None:
                address = self._control_address or os.environ.get(CONTROL_ADDRESS_ENV)
                if address:
                    self._conn = self._connect(address)
        return self._conn

    def _send(self, msg: dict[str, str | int]) -> None:
        conn = self._ensure_connected()
        if conn is not None:
            try:
                msg.update({"pid": os.getpid()})
                conn.send(msg)
                logger.debug("Sent control message=%s", msg)
            except Exception as e:
                logger.debug("Failed to send control message=%s: %s", msg, e)
                with self._lock:
                    self._conn = None
                with suppress(Exception):
                    conn.close()
        else:
            logger.debug("No connection: msg=%s, address=%s", msg, self._control_address)

    def start_monitoring(self) -> None:
        """Resume/enable GC monitoring for this process."""
        self._send({"msg": "start"})

    def stop_monitoring(self) -> None:
        """Pause/disable GC monitoring for this process."""
        self._send({"msg": "stop"})

    @contextmanager
    def pause_monitoring(self) -> Generator[None, Any]:
        """Context manager that pauses monitoring and resumes on exit."""
        self.stop_monitoring()
        try:
            yield
        finally:
            self.start_monitoring()

    def close(self) -> None:
        """Close the control connection."""
        with self._lock:
            if self._conn is not None:
                with suppress(Exception):
                    self._conn.close()
                self._conn = None

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()
