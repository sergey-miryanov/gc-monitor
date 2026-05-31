"""Child-side control plane API for start/stop/pause monitoring."""

import logging
import os
import threading
from collections.abc import Generator
from contextlib import contextmanager
from multiprocessing.connection import Client, Connection
from typing import Any

from gc_monitor.control.control_server import CONTROL_ADDRESS_ENV

logger = logging.getLogger("gc_monitor.control")

_conn: Connection | None = None
_lock = threading.Lock()


def _create_connection(verbose:bool=False) -> Connection | None:
    address = os.environ.get(CONTROL_ADDRESS_ENV)
    if not address:
        return None
    try:
        return Client(address)
    except Exception as e:
        logger.warning("Failed to connect to control plane: %s", e)
        if verbose:
            print(f"Failed to connect to control plane: {e}")
        return None


def _ensure_connected(verbose:bool=False) -> Connection | None:
    global _conn

    if _conn is not None:
        return _conn

    with _lock:
        if _conn is None:
            _conn = _create_connection(verbose)

    return _conn


def _send(msg: dict[str,str|int], *, verbose:bool=False) -> None:
    conn = _ensure_connected(verbose)
    if conn is not None:
        try:
            msg.update({"pid": os.getpid()})
            conn.send(msg)
        except Exception as e:
            logger.debug("Failed to send control message: %s", e)
    elif verbose:
        # logger.debug("No connection")
        print("No connection")


def start_monitoring(verbose:bool = False) -> None:
    """Resume/enable GC monitoring for this process."""
    _send({"msg": "start"}, verbose=verbose)


def stop_monitoring(verbose:bool = False) -> None:
    """Pause/disable GC monitoring for this process."""
    _send({"msg": "stop"}, verbose=verbose)


@contextmanager
def pause_monitoring(verbose:bool = False) -> Generator[None, Any]:
    """Context manager that pauses monitoring and resumes on exit."""
    stop_monitoring(verbose)
    try:
        yield
    finally:
        start_monitoring(verbose)
