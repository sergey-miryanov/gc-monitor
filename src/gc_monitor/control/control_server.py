"""Control plane for parent-child IPC via multiprocessing.connection."""

import contextlib
import logging
import sys
import threading
from multiprocessing.connection import Connection, Client, Listener, wait
if sys.platform == "win32":
    from multiprocessing.connection import PipeConnection

from typing import Optional, TypeAlias
import msgspec

from gc_monitor.data import instant_msg
from gc_monitor.exporters.exporter import EventsExporter

logger = logging.getLogger("gc_monitor.control")

CONTROL_ADDRESS_ENV = "GC_MONITOR_CONTROL_ADDRESS"
_PREFIX = "gc-monitor-"

READER_POLL_INTERVAL = 0.1

if sys.platform == "win32":
    TConnection: TypeAlias = PipeConnection

    def _make_address(name: str) -> str:
        return rf"\\.\pipe\{_PREFIX}{name}"

    def _accept(listener: Listener) -> PipeConnection:
        return listener.accept() # type: ignore

    def _wait(conns: list[PipeConnection]) -> list[PipeConnection]:
        return wait(conns, timeout=READER_POLL_INTERVAL) # type: ignore
else:
    TConnection: TypeAlias = Connection

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

    def __init__(self, address: str | None = None) -> None:
        full_address = _make_address(address) if address is not None else None
        self._listener: Listener|None = Listener(full_address)
        assert isinstance(self._listener.address, str)
        self._full_address = self._listener.address
        self._connections: set[TConnection] = set()
        self._enabled: dict[int, bool] = {}
        self._lock = threading.Lock()
        self._exporter_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._running = False
        self._exporter: EventsExporter | None = None
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

    def _accept_loop(self) -> None:
        conn: TConnection | None = None
        while not self._stop_event.is_set():
            try:
                with self._lock:
                    listener = self._listener

                if listener is None:
                    break

                conn = _accept(listener)
                with self._lock:
                    self._connections.add(conn)
                    conn = None
            except Exception as e:
                logger.debug("Error while connecting child: %s", e)
                break

        if conn is not None:
            with contextlib.suppress(Exception):
                conn.close()
                conn = None

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
                    if msg is not None:
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
                            logger.debug("Error while handling data from child: %s", e)

            if to_remove:
                self._remove_connections(to_remove)

            self._stop_event.wait(READER_POLL_INTERVAL)

    def _recv(self, conn: TConnection, to_remove: list[TConnection]) -> Optional[ControlMsg]:
        try:
            msg = conn.recv()
            return msgspec.convert(msg, ControlMsg)
        except (EOFError, OSError, ConnectionError):
            to_remove.append(conn)
        except Exception as e:
            logger.debug("Error while receving data from child: %s", e)
            to_remove.append(conn)

        return None

    def _add_event(self, m:str, pid:int) -> None:
        with self._exporter_lock:
            if self._exporter is not None:
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
        with self._lock:
            self._connections -= set(to_remove)

        self._close_connections(to_remove)

    def _clear_connections(self) -> None:
        with self._lock:
            conns = list(self._connections)
            self._connections.clear()

        self._close_connections(conns)

    def _close_connections(self, conns:list[TConnection])->None:
        for conn in conns:
            with contextlib.suppress(Exception):
                conn.close()

    def set_exporter(self, exporter: EventsExporter) -> None:
        with self._exporter_lock:
            self._exporter = exporter

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

        self._clear_connections()

        if self._running:
            self._reader_thread.join(1)

            with contextlib.suppress(Exception):
                with Client(self.address):
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


def set_control_env(env: dict[str, str], address: str) -> None:
    """Populate env dict with control plane connection info."""
    env[CONTROL_ADDRESS_ENV] = address
