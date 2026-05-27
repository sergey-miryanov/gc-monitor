"""Control plane for parent-child IPC via multiprocessing.connection."""

import json
import logging
import threading
from multiprocessing.connection import Connection, Listener, wait

from gc_monitor.exporters.exporter import EventsExporter
from gc_monitor.data import instant_msg

logger = logging.getLogger("gc_monitor.control")

CONTROL_ADDRESS_ENV = "GC_MONITOR_CONTROL_ADDRESS"
CONTROL_FAMILY_ENV = "GC_MONITOR_CONTROL_FAMILY"

READER_POLL_INTERVAL = 0.1


class ControlServer:
    """Parent-side control plane.

    Accepts connections from child processes and tracks which PIDs have
    monitoring enabled via start/stop messages.
    """

    def __init__(self) -> None:
        self._listener: Listener|None = Listener(("localhost", 0), family="AF_INET")
        self._connections: set[Connection] = set()
        self._enabled: dict[int, bool] = {}
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._has_msg_event = threading.Event()
        self._running = False
        self._exporter: EventsExporter | None = None
        self._accept_thread: threading.Thread = threading.Thread(
            target=self._accept_loop, daemon=True
        )
        self._reader_thread: threading.Thread = threading.Thread(
            target=self._reader_loop, daemon=True
        )

    @property
    def address(self) -> str|tuple[str, int]:
        assert self._listener is not None
        return self._listener.address

    def start(self) -> None:
        """Start background accept and reader daemon threads."""
        self._has_msg_event.clear()
        self._stop_event.clear()
        self._accept_thread.start()
        self._reader_thread.start()

    def _accept_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                assert self._listener is not None
                conn = self._listener.accept()
                with self._lock:
                    self._connections.add(conn)
                self._has_msg_event.set()
            except Exception as e:
                logger.debug("Error while connecting child: %s", e)

    def _reader_loop(self) -> None:
        while not self._stop_event.is_set():
            with self._lock:
                conns = list(self._connections)

            to_remove: list[Connection] = []
            if conns:
                ready: list[Connection] = wait(conns, timeout=READER_POLL_INTERVAL)
                for conn in ready:
                    if self._stop_event.is_set():
                        break

                    try:
                        msg = conn.recv()
                        m = msg["msg"]
                        if m in ("start", "stop"):
                            pid = msg["pid"]
                            with self._lock:
                                self._enabled[pid] = m == "start"

                                if self._exporter is not None:
                                    self._exporter.add_instant_event(
                                        pid,
                                        instant_msg(f"{m} GC monitor")
                                    )

                    except EOFError:
                        to_remove.append(conn)
                    except Exception as e:
                        logger.debug("Error while receving data from child: %s", e)

            if to_remove:
                with self._lock:
                    for conn in to_remove:
                        if conn in self._connections:
                            self._connections.remove(conn)

            self._has_msg_event.wait(READER_POLL_INTERVAL)

    def set_exporter(self, exporter: EventsExporter) -> None:
        """Set the exporter to record control plane events."""
        with self._lock:
            self._exporter = exporter

    def is_enabled(self, pid: int) -> bool:
        """Check if monitoring is enabled for the given PID.

        Returns True for unknown PIDs (safe default).
        """
        with self._lock:
            return self._enabled.get(pid, True)

    def close(self) -> None:
        """Shut down all connections and the listener."""
        self._stop_event.set()
        self._has_msg_event.set()

        with self._lock:
            conns = list(self._connections)
            self._connections.clear()
            self._enabled.clear()

        for conn in conns:
            try:
                conn.close()
            except OSError:
                pass

        if self._listener is not None:
            try:
                self._listener.close()
            except OSError:
                pass
            self._listener = None


def set_control_env(env: dict[str, str], address: str|tuple[str, int]) -> None:
    """Populate env dict with control plane connection info."""
    env[CONTROL_ADDRESS_ENV] = json.dumps(address)
    env[CONTROL_FAMILY_ENV] = "AF_INET"
