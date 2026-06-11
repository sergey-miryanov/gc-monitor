"""Thread-safety stress tests for ControlClient.

These tests exercise concurrent send() / close() / failure-recovery paths on
a single ControlClient connection.
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable
from typing import Any
from unittest.mock import MagicMock

import pytest

from gcmon.control.control_client import ControlClient
from gcmon.control.control_server import ControlServer
from tests.helpers import MockExporter


def _run_threads(threads: list[threading.Thread], timeout: float = 5.0) -> None:
    """Start all threads, join all with bounded timeout, report survivors."""
    for t in threads:
        t.start()
    deadline = time.monotonic() + timeout
    for t in threads:
        remaining = max(0.0, deadline - time.monotonic())
        t.join(timeout=remaining)
    survivors = [t.name for t in threads if t.is_alive()]
    assert not survivors, f"threads hung: {survivors}"


def _wait_server_drain(server: ControlServer, timeout: float = 0.7) -> None:
    """Wait for the server's reader thread to observe an EOF on a closed conn.

    The server's _drain_connections runs for up to 0.5s after stop_event; the
    reader poll loop is 100ms. 700ms covers both windows on slow CI.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if len(server._connections) == 0:
            return
        time.sleep(0.01)


def _wait_for_event_count(
    exporter: MockExporter, count: int, timeout: float = 2.0
) -> None:
    """Wait until the exporter has received at least `count` events.

    The server's reader poll loop is 100ms (READER_POLL_INTERVAL). 2s
    covers Windows CI jitter.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if len(exporter.instant_events) >= count:
            return
        time.sleep(0.01)


def _make_server_with_exporter() -> tuple[ControlServer, MockExporter]:
    """Spin up a real ControlServer with a real MockExporter (collects events)."""
    exporter = MockExporter()
    server = ControlServer(exporter)
    server.start()
    return server, exporter


# Iteration counts for concurrent-send stress tests. Both TC-1 and TC-2
# use the same N_SENDERS x N_PER_SENDER matrix: 4 senders each pushing
# 10 messages through a single ControlClient, released simultaneously
# by a Barrier. The total (40 messages) is enough to exercise contention
# at the OS scheduler level while keeping local test runs under 15s
# (the production ControlServer drains at ~10 messages/second through
# a single pipe; see READER_POLL_INTERVAL in control_server.py).
N_SENDERS = 4
N_PER_SENDER = 10
N_RACE_ITERATIONS = 10


def _sender_sends_n(
    i: int,
    client: ControlClient,
    barrier: threading.Barrier,
    errors: list[BaseException],
) -> None:
    """Worker: send N_PER_SENDER messages on `client`, tagged with index `i`."""
    try:
        barrier.wait(timeout=2)
        for n in range(N_PER_SENDER):
            client.instant_msg(f"t{i}-m{n}")
    except BaseException as e:
        errors.append(e)


def _run_senders(
    senders: list[tuple[int, ControlClient]],
    barrier: threading.Barrier,
    *,
    timeout: float = 10.0,
) -> list[BaseException]:
    """Launch one thread per (index, client) pair; return the errors list.

    All threads are joined before this returns (see _run_threads for the
    bounded-wait contract). The returned list is the per-thread error
    sink: empty on success, populated with any captured exception.
    """
    errors: list[BaseException] = []
    threads = [
        threading.Thread(target=_sender_sends_n, args=(i, client, barrier, errors))
        for i, client in senders
    ]
    _run_threads(threads, timeout=timeout)
    return errors


def _assert_no_messages_loss(
    exporter: MockExporter,
    expected_total: int,
    *,
    timeout: float = 15.0,
) -> None:
    _wait_for_event_count(exporter, expected_total, timeout=timeout)
    assert len(exporter.instant_events) == expected_total


def _assert_no_messages_garbling(exporter: MockExporter) -> None:
    """Assert every sent name appears exactly once in the received set."""
    expected_names = {
        f"t{i}-m{n}" for i in range(N_SENDERS) for n in range(N_PER_SENDER)
    }
    actual_names = {msg.name for _, msg in exporter.instant_events}
    missing = expected_names - actual_names
    extra = actual_names - expected_names
    assert not (missing or extra), f"missing={missing}, extra={extra}"


def _assert_send_order_preserved(exporter: MockExporter) -> None:
    """Assert that each sender's messages arrived in send order."""
    for i in range(N_SENDERS):
        sender_events = [
            msg.name for _, msg in exporter.instant_events
            if msg.name.startswith(f"t{i}-")
        ]
        expected = [f"t{i}-m{n}" for n in range(N_PER_SENDER)]
        assert sender_events == expected, (
            f"sender {i}: order broken. got {sender_events[:5]}... "
            f"expected {expected[:5]}..."
        )


def _run_send_close_races(
    client: ControlClient,
    n_iterations: int,
    *,
    post_race: Callable[[], None] | None = None,
) -> tuple[list[BaseException], list[str]]:
    """Run the send/close race `n_iterations` times on `client`.

    Each iteration uses a fresh `threading.Barrier(2)` to release one
    main thread (calls `client.instant_msg("x")`) and one watcher
    thread (calls `client.close()`) simultaneously. Outcomes are
    recorded per iteration: `"clean"` if the send completed,
    `"send-raised"` if the send hit an `OSError` against a half-closed
    conn. Returns `(errors, race_outcomes)` accumulated across all
    iterations.

    `post_race` is called after each iteration's threads have joined.
    Use it to set the conn state for the next iteration — e.g.
    `client.close` for cold-conn tests (defensive, the watcher
    already closed), or a re-warm `client.instant_msg("probe")` for
    hot-conn tests (the watcher's close tore down the conn). If
    `None`, the conn is left in whatever state the watcher left it.
    """
    errors: list[BaseException] = []
    race_outcomes: list[str] = []

    for _ in range(n_iterations):
        start_barrier = threading.Barrier(2)
        iter_errors: list[BaseException] = []

        def watcher(
            barrier: threading.Barrier = start_barrier,
            errs: list[BaseException] = iter_errors,
        ) -> None:
            try:
                barrier.wait(timeout=5)
                client.close()
            except BaseException as e:
                errs.append(e)

        def main(
            barrier: threading.Barrier = start_barrier,
            errs: list[BaseException] = iter_errors,
        ) -> None:
            try:
                barrier.wait(timeout=5)
                try:
                    client.instant_msg("x")
                except OSError:
                    race_outcomes.append("send-raised")
                    return
                race_outcomes.append("clean")
            except BaseException as e:
                errs.append(e)

        threads = [
            threading.Thread(target=main, name="main"),
            threading.Thread(target=watcher, name="watcher"),
        ]
        _run_threads(threads, timeout=5)
        errors.extend(iter_errors)

        if post_race is not None:
            post_race()

    return errors, race_outcomes


def _assert_send_close_serialized(
    race_outcomes: list[str],
    n_iterations: int,
) -> None:
    """Assert the send/close race outcomes match the serialization design.

    `ControlClient._lock` (control_client.py:47, 72, 106) covers both
    `_send` and `close`, so a barrier-released race cannot produce a
    half-closed-conn send. The lock guarantees that the send runs to
    completion before the close can grab the conn. A `send-raised`
    outcome here would mean the lock was lost.
    """
    assert "clean" in race_outcomes, (
        f"never observed close-after-send across "
        f"{n_iterations} iterations: {race_outcomes}"
    )
    assert race_outcomes.count("send-raised") == 0, (
        f"unexpected close-during-send outcomes — _lock no longer "
        f"serializes send/close: {race_outcomes}"
    )


# =============================================================================
# TC-1, TC-2: concurrent senders
# =============================================================================


class TestConcurrentSend:
    @pytest.mark.stress
    def test_concurrent_send_does_not_lose_messages(self) -> None:
        server, exporter = _make_server_with_exporter()
        client = ControlClient(server.address)
        try:
            senders = [(i, client) for i in range(N_SENDERS)]
            errors = _run_senders(senders, threading.Barrier(N_SENDERS))
            assert errors == [], f"thread raised: {errors}"
            _assert_no_messages_loss(exporter, N_SENDERS * N_PER_SENDER)
            _assert_no_messages_garbling(exporter)
        finally:
            client.close()
            server.close()

    @pytest.mark.stress
    def test_per_sender_fifo_preserved(self) -> None:
        server, exporter = _make_server_with_exporter()
        clients = [ControlClient(server.address) for _ in range(N_SENDERS)]
        try:
            senders = [(i, clients[i]) for i in range(N_SENDERS)]
            errors = _run_senders(senders, threading.Barrier(N_SENDERS))
            assert errors == [], f"thread raised: {errors}"
            _assert_no_messages_loss(exporter, N_SENDERS * N_PER_SENDER)
            _assert_send_order_preserved(exporter)
        finally:
            for c in clients:
                c.close()
            server.close()


# =============================================================================
# TC-3, TC-4, TC-5: send/close races
# =============================================================================


class TestSendCloseRace:
    @pytest.mark.stress
    def test_send_and_close_are_serialized_on_cold_conn(self) -> None:
        # Cold-conn variant: each iteration starts from _conn = None,
        # exercising the lazy-reconnect path under send/close contention.
        # See TC-4 for the warm-conn variant of the same property.
        server, _exporter = _make_server_with_exporter()
        client = ControlClient(server.address)
        try:
            errors, race_outcomes = _run_send_close_races(
                client, N_RACE_ITERATIONS,
                post_race=client.close,
            )
            assert errors == [], f"thread raised: {errors}"
            _assert_send_close_serialized(race_outcomes, N_RACE_ITERATIONS)
        finally:
            client.close()
            server.close()

    @pytest.mark.stress
    def test_send_and_close_are_serialized_on_warm_conn(self) -> None:
        # Warm-conn variant: one warmup instant_msg before the loop
        # establishes a live conn, so each iteration races on a hot conn.
        # The re-warm callback restores the hot state after each iter.
        # See TC-3 for the cold-conn variant of the same property.
        server, _exporter = _make_server_with_exporter()
        client = ControlClient(server.address)
        try:
            client.instant_msg("warmup")
            _wait_server_drain(server)

            def re_warm() -> None:
                client.instant_msg("probe")
                _wait_server_drain(server)

            errors, race_outcomes = _run_send_close_races(
                client, N_RACE_ITERATIONS,
                post_race=re_warm,
            )
            assert errors == [], f"thread raised: {errors}"
            _assert_send_close_serialized(race_outcomes, N_RACE_ITERATIONS)
        finally:
            client.close()
            server.close()

    # NOT marked stress (Q4): cheap, catches a real regression, run by default.
    def test_close_idempotent_under_contention(self) -> None:
        server, _exporter = _make_server_with_exporter()
        client = ControlClient(server.address)
        try:
            client._ensure_connected()
            errors: list[BaseException] = []
            barrier = threading.Barrier(8)

            def closer() -> None:
                try:
                    barrier.wait(timeout=2)
                    for _ in range(10):
                        client.close()
                except BaseException as e:
                    errors.append(e)

            threads = [threading.Thread(target=closer) for _ in range(8)]
            _run_threads(threads, timeout=5)

            assert errors == [], f"thread raised: {errors}"
            assert client._conn is None
            _wait_server_drain(server)
            assert len(server._connections) == 0
        finally:
            client.close()
            server.close()


# =============================================================================
# TC-6, TC-7: failure recovery and contract
# =============================================================================


class TestFailureRecovery:
    @pytest.mark.stress
    def test_send_failure_recovery_under_contention(self) -> None:
        """First send raises BrokenPipeError; subsequent sends recover."""
        mock_conn = MagicMock()
        call_count = {"n": 0}

        def send_side_effect(_payload: object) -> None:
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise BrokenPipeError("simulated broken pipe")

        mock_conn.send.side_effect = send_side_effect

        # Factory returns a fresh mock after the first failure, so the
        # recovery path creates a new conn that succeeds.
        reconnect_conn = MagicMock()
        factory_call_count = {"n": 0}

        def factory(_address: str) -> Any:
            factory_call_count["n"] += 1
            if factory_call_count["n"] == 1:
                return mock_conn
            return reconnect_conn

        client = ControlClient("test-address", connection_factory=factory)
        try:
            errors: list[BaseException] = []
            barrier = threading.Barrier(2)

            def sender() -> None:
                try:
                    barrier.wait(timeout=2)
                    client.instant_msg("m")
                except BaseException as e:
                    errors.append(e)

            threads = [threading.Thread(target=sender) for _ in range(2)]
            _run_threads(threads, timeout=5)

            assert errors == [], f"thread raised: {errors}"
            # The first connection's close was called when the failure cleared it.
            mock_conn.close.assert_called_once()
            # At least one reconnect happened (the second-and-later senders
            # raced into _ensure_connected after the first failure cleared
            # self._conn).
            assert factory_call_count["n"] >= 2, (
                f"expected at least 2 factory calls, got {factory_call_count['n']}"
            )
        finally:
            client.close()

    @pytest.mark.stress
    def test_send_after_close_lazily_reconnects(self) -> None:
        """Contract: close() is not sticky; the next send() reconnects.

        This locks in the behavior at control_client.py:67-70 where
        _ensure_connected() re-creates the conn on the next call.
        """
        server, exporter = _make_server_with_exporter()
        client = ControlClient(server.address)
        try:
            # Pre-warm the connection so the first send cannot race with
            # the server's accept loop.
            conn = client._ensure_connected()
            assert conn is not None
            client.instant_msg("before")
            _wait_for_event_count(exporter, 1, timeout=2)
            assert len(exporter.instant_events) == 1

            client.close()
            assert client._conn is None
            client.instant_msg("after")

            client_pid = os.getpid()
            _wait_for_event_count(exporter, 2, timeout=2)
            custom_events = [
                (pid, msg.name) for pid, msg in exporter.instant_events
                if msg.name in ("before", "after")
            ]
            assert custom_events == [
                (client_pid, "before"),
                (client_pid, "after"),
            ], f"unexpected events: {custom_events}"
            # The "after" send re-established a connection; the old one was
            # removed when the server saw EOF. Sample repeatedly until 1
            # is observed, or timeout.
            deadline = time.monotonic() + 1.0
            while time.monotonic() < deadline and len(server._connections) != 1:
                time.sleep(0.01)
            assert len(server._connections) == 1
        finally:
            client.close()
            server.close()
