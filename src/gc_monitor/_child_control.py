"""Child-side control plane API for start/stop/pause monitoring."""

import json
import logging
import os
import threading
from contextlib import contextmanager
from multiprocessing.connection import Client, Connection

from gc_monitor._control import CONTROL_ADDRESS_ENV, CONTROL_FAMILY_ENV

logger = logging.getLogger("gc_monitor.child_control")

_conn: Connection | None = None
_lock = threading.Lock()


def _ensure_connected() -> Connection | None:
    global _conn

    if _conn is not None:
        return _conn

    with _lock:
        if _conn is not None:
            return _conn

        address_str = os.environ.get(CONTROL_ADDRESS_ENV)
        if not address_str:
            return None
        try:
            address = tuple(json.loads(address_str))
        except (json.JSONDecodeError, ValueError):
            logger.warning("Invalid control address: %s", address_str)
            return None

        family_str = os.environ.get(CONTROL_FAMILY_ENV)
        family = family_str or "AF_INET"

        try:
            _conn = Client(address, family=family)
        except Exception as e:
            logger.warning("Failed to connect to control plane: %s", e)

    return _conn


def _send(msg: dict[str,str|int]) -> None:
    conn = _ensure_connected()
    if conn is not None:
        try:
            msg.update({"pid": os.getpid()})
            conn.send(msg)
        except Exception as e:
            logger.debug("Failed to send control message: %s", e)


def start_monitoring() -> None:
    """Resume/enable GC monitoring for this process."""
    _send({"msg": "start"})


def stop_monitoring() -> None:
    """Pause/disable GC monitoring for this process."""
    _send({"msg": "stop"})


@contextmanager
def pause_monitoring():
    """Context manager that pauses monitoring and resumes on exit."""
    stop_monitoring()
    try:
        yield
    finally:
        start_monitoring()
