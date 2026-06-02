"""Control plane for parent-child IPC via multiprocessing.connection."""

import contextlib
import logging
import sys
import threading
import time
from multiprocessing.connection import Client, Connection, Listener, wait

if sys.platform == "win32":
    from multiprocessing.connection import PipeConnection

from typing import Self

import msgspec

from gc_monitor.data import instant_msg
from gc_monitor.exporters.exporter import EventsExporter

logger = logging.getLogger("gc_monitor")

CONTROL_ADDRESS_ENV = "GC_MONITOR_CONTROL_ADDRESS"
_PREFIX = "gc-monitor-"

READER_POLL_INTERVAL = 0.1

if sys.platform == "win32":
    TConnection = PipeConnection

    def _make_address(name: str) -> str:
        return rf"\\.\pipe\{_PREFIX}{name}"

    def _accept(listener: Listener) -> PipeConnection:
        return listener.accept() # type: ignore

    def _wait(conns: list[PipeConnection]) -> list[PipeConnection]:
        return wait(conns, timeout=READER_POLL_INTERVAL) # type: ignore
else:
    TConnection = Connection

    def _make_address(name: str) -> str:
        return f"/tmp/{_PREFIX}{name}"

    def _accept(listener: Listener) -> Connection:
        return listener.accept()

    def _wait(conns: list[Connection]) -> list[Connection]:
        return wait(conns, timeout=READER_POLL_INTERVAL) # type: ignore


class ControlMsg(msgspec.Struct):
    msg: str
    pid: int


class ControlServer:
    """Parent-side control plane.

    Accepts connections from child processes and tracks which PIDs have
    monitoring enabled via start/stop messages.
    """

    def __init__(self, exporter: EventsExporter, address: str | None = None) -> None:
        full_address = _make_address(address) if address is not None else None
        self._listener: Listener|None = Listener(full_address)
        assert isinstance(self._listener.address, str)
        self._full_address = self._listener.address
        self._connections: set[TConnection] = set()
        self._enabled: dict[int, bool] = {}
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._running = False
        self._exporter: EventsExporter = exporter
        self._accept_thread: threading.Thread = threading.Thread(
            target=self._accept_loop, daemon=True
        )
        self._reader_thread: threading.Thread = threading.Thread(
            target=self._reader_loop, daemon=True
        )

    @property
    def address(self) -> str:
        return self._full_address

    def start(self) -> None:
        assert not self._running
        self._stop_event.clear()
        self._accept_thread.start()
        self._reader_thread.start()
        self._running = True

        logger.info("Running server on %s", self.address)

    def _safe_accept(self, listener: Listener) -> TConnection | None:
        try:
            return _accept(listener)
        except Exception as e:
            logger.error("Error accepting connection on control server: %s", e)
            return None

    def _accept_loop(self) -> None:
        while not self._stop_event.is_set():
            with self._lock:
                listener = self._listener

            if listener is None:
                break

            conn = self._safe_accept(listener)
            if conn is None:
                break

            try:
                with self._lock:
                    self._connections.add(conn)
            except Exception:
                with contextlib.suppress(Exception):
                    conn.close()
                logger.debug("Failed to add connection, continuing")
                continue

        logger.debug("Stopped accept loop")

    def _reader_loop(self) -> None:
        while not self._stop_event.is_set():
            with self._lock:
                conns = list(self._connections)

            to_remove: list[TConnection] = []
            if conns:
                ready = self._safe_wait(conns)
                for conn in ready:
                    if self._stop_event.is_set():
                        break

                    msg = self._recv(conn, to_remove)
                    logger.debug("Got message: %s", msg)
                    if msg is not None:
                        self._handle_msg(msg)

            if to_remove:
                self._remove_connections(to_remove)

            self._stop_event.wait(READER_POLL_INTERVAL)

        self._drain_connections()

    def _recv(self, conn: TConnection, to_remove: list[TConnection]) -> ControlMsg | None:
        try:
            msg = conn.recv()
            return msgspec.convert(msg, ControlMsg)
        except (EOFError, OSError, ConnectionError):
            to_remove.append(conn)
        except Exception as e:
            logger.debug("Error while receiving data from child: %s", e)
            to_remove.append(conn)

        return None

    def _handle_msg(self, msg: ControlMsg) -> None:
        try:
            m = msg.msg
            pid = msg.pid
            if m == "start":
                m = "start GC monitor"
                with self._lock:
                    self._enabled.pop(pid, None)
            elif m == "stop":
                m = "stop GC monitor"
                with self._lock:
                    self._enabled[pid] = False
            self._add_event(m, pid)
        except Exception as e:
            logger.debug("Error while handling message: %s", e)

    def _drain_connections(self, timeout: float = 0.5) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                conns = list(self._connections)
            if not conns:
                return
            to_remove: list[TConnection] = []
            any_data = False
            for conn in conns:
                try:
                    if conn.poll(timeout=0):
                        msg = self._recv(conn, to_remove)
                        if msg is not None:
                            self._handle_msg(msg)
                            any_data = True
                except Exception:
                    to_remove.append(conn)
            if to_remove:
                self._remove_connections(to_remove)
            if not any_data:
                break

    def _add_event(self, m: str, pid: int) -> None:
        msg = instant_msg(m)
        self._exporter.add_instant_event(pid, msg)

    def _safe_wait(self, conns: list[TConnection]) -> list[TConnection]:
        try:
            ready = _wait(conns)
            return ready
        except Exception:
            to_remove: list[TConnection] = []
            for conn in conns:
                try:
                    conn.poll(timeout=0)
                except Exception:
                    to_remove.append(conn)

            if to_remove:
                self._remove_connections(to_remove)

            return []

    def _remove_connections(self, to_remove: list[TConnection]) -> None:
        logger.debug("Remove connections: %s", to_remove)

        with self._lock:
            self._connections -= set(to_remove)

        self._close_connections(to_remove)

    def _clear_connections(self) -> None:
        with self._lock:
            conns = list(self._connections)
            self._connections.clear()

        self._close_connections(conns)

    def _close_connections(self, conns: list[TConnection]) -> None:
        for conn in conns:
            with contextlib.suppress(Exception):
                conn.close()

    def is_running(self) -> bool:
        return self._running

    def is_enabled(self, pid: int) -> bool:
        """Check if monitoring is enabled for the given PID.

        Returns True for unknown PIDs (safe default).
        """
        with self._lock:
            return self._enabled.get(pid, True)

    def close(self) -> None:
        """Shut down all connections and the listener."""
        self._stop_event.set()

        if self._running:
            self._reader_thread.join(2)

            with contextlib.suppress(Exception), Client(self.address):
                pass

            self._accept_thread.join(1)

        self._clear_connections()
        with self._lock:
            self._enabled.clear()

        if self._running:
            with self._lock:
                if self._listener is not None:
                    self._listener.close()
                    self._listener = None

        self._running = False

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


def set_control_env(env: dict[str, str], address: str) -> None:
    """Populate env dict with control plane connection info."""
    env[CONTROL_ADDRESS_ENV] = address
