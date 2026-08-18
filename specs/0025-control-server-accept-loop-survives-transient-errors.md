# 0025: Keep accepting control connections after a transient accept error

- **Status:** **Pinned** (`tests/control/test_control_server.py::TestControlServerAcceptLoop::test_accept_loop_accept_exception_breaks`)
- **Kind:** bug (availability)
- **Effort:** XS
- **Origin:** post-v0.2.0 code review (old spec 18, REQ-8)
- **Respects:** [ADR-0014](../docs/adr/0014-perfetto-integration-test-strategy.md) (stress tests are the only probabilistic suite; do not add a `sleep`)

## 1. Problem

An application drives monitoring from the inside: `ControlClient` to start and stop
collection, and to drop named marks into the trace. One accept error on the server's
listening socket, and every later connection from that application is refused for the rest of
the run. Nothing says so: the server logs a single line at `ERROR`, `stop()` still works,
`is_enabled` still answers, and the operator only finds out afterwards, when the marks they
put in their code are missing from the trace. `ControlClient` reconnects silently on the next
send, which turns one transient failure into a permanent, invisible outage.

## 2. Evidence

`ControlServer._accept_loop` treats "no connection this time" and "stop" as the same
condition:

```python
conn = self._safe_accept(listener)
if conn is None:
    break
```

`_safe_accept` returns `None` for exactly one reason: `_accept` raised, it logged
`"Error accepting connection on control server"`, and it swallowed the exception. That is the
transient case by construction; the terminating cases are handled above it (`_stop_event`
in the `while` condition, `self._listener is None` in its own branch). The one path that
means "try again" is the one that exits.

The connection-add failure two lines below already does the right thing: it closes the
orphan, logs, and `continue`s.

## 3. Scope

**Affected:** every run with a control plane: `gcmon run`, the pyperf hook, and any process
using `ControlClient` directly. Both platform transports (`AF_UNIX` socket and the Windows
named pipe) go through the same loop.

**Not affected:** the polling path and the exporters. A run that never opens a control
connection cannot reach `_safe_accept`'s failure branch, so no trace content changes for
anyone not using the control plane.

**Why the suite didn't catch it:** it *asserts* it. `test_accept_loop_accept_exception_breaks`
patches `_accept` to raise unconditionally and asserts the loop returns, written to cover the
branch, not to state what should happen. With the fix the loop would spin until the stop
event, so the test does not merely change its expectation; it needs a bounded `_accept` side
effect. Change it deliberately, in the same commit.

## 4. Proposed change

1. `break` → `continue` in the `_safe_accept` branch of `_accept_loop`. The `while not
   self._stop_event.is_set()` condition is re-checked immediately, so shutdown still exits on
   the next iteration.
2. Rewrite `test_accept_loop_accept_exception_breaks` as
   `test_accept_loop_continues_after_transient_error`: `_accept` fails once, then returns a
   connection. Assert the connection lands in `_connections` and the error was logged.
3. Leave `_safe_accept` alone. An exception it does not catch still propagates out of the
   accept thread, and `self._listener is None` still terminates the loop; those remain the
   only two ways out besides the stop event.

A failure mode this opens: a listener that is broken but not closed makes `_accept` raise on
every call, and the loop becomes a hot spin logging at `ERROR` per iteration. Judged
acceptable and better than the current silence: it is loud, and `stop()` still ends it. If a
run is ever observed doing this, the answer is a backoff or a consecutive-failure ceiling, not
a return to `break`.

## 5. Seams and testing decisions

- **Seam:** the real server/client pair in `tests/control/test_control_client_thread_safety.py`,
  which already constructs a live `ControlServer` and connects `ControlClient(server.address)`
  over the real transport. That is the highest seam that can observe this: it asserts what the
  operator cares about (a client can still connect), not what the loop did internally.
- **New seam needed:** none. The lower `_accept_loop`-with-patched-`_accept` seam in
  `test_control_server.py` also stays, because it is the only way to inject the transient
  failure deterministically.
- **What makes a good test here:** connect a *second* client after the induced failure and
  assert its message arrives at the exporter. Asserting only that `_accept_loop` returned, or
  that `_connections` is non-empty, would pass on a loop that accepted one connection and then
  died, which is the bug.
- **Prior art:** `TestConcurrentSend` in `tests/control/test_control_client_thread_safety.py`
  for the live pair; `TestControlServerAcceptLoop` for the injected failure.
- **Cases:**
  1. `_accept` raises once, then yields a connection: the connection is accepted, the error is
     logged once.
  2. Regression guard: `_stop_event.set()` and `self._listener = None` each still terminate
     the loop, and a genuinely raising `_safe_accept` internal (an exception it does not catch)
     still propagates.

## 6. Out of scope

- Backoff, jitter, or a consecutive-failure ceiling. Not warranted until a real run is seen
  spinning; adding it now would be speculative policy in a loop whose failure modes we have
  observed exactly once.
- The reader loop (`_reader_loop`) and `_drain_connections`. Their error handling was not part
  of this finding and reads correctly.
- Surfacing control-plane health to the operator (a warning at shutdown that N accepts failed).
  Worth doing, but it is a reporting feature and not what makes this a bug.
