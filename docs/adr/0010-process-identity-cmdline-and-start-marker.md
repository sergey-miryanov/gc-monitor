# ADR-0010: Carry process cmdline in two places, and force the process track to render

- **Status:** Accepted
- **Date:** 2026-06-08 (`Start Process` marker added 2026-06-27; collection
  moved to the monitor and became once per process 2026-08-31, see
  [ADR-0025](0025-create-every-process-in-one-place.md))

## Context

gcmon discovers child PIDs at runtime and monitors them alongside the main
process, so a trace routinely contains several processes. `Process 4821` is
not enough to tell them apart, so you need the command line.

Perfetto's `ProcessDescriptor` has a `cmdline` field (field 2, repeated
string) for this. Two problems stood in the way.

**The trace processor does not surface it.** `ProcessDescriptor.cmdline` is
not exposed via the SQL `process.cmdline` column, which always returns `None`.
Writing it correctly makes the data visible in the UI but unqueryable from
SQL, which is how the trace-processor tests and any user analysis read a
trace.

**The process track was often invisible.** The Perfetto UI renders a track's
`description` only when the track has at least one event on it. The process
track is OS-scoped and receives events only from `InstantEvent`s, since Begin,
End and Counter events live on child tracks. A trace with no instant events
for a pid therefore had an empty process track, and the UI hid its
description.

## Decision

**Write the cmdline twice, on purpose.**

- `ProcessDescriptor.cmdline` (field 2, repeated string), protobuf-correct and
  visible in the UI.
- `TrackDescriptor.description` (field 14, string), the space-joined cmdline,
  on the process track. This one *is* surfaced in the `args` table and joins
  to `process` via `track`, so it is queryable:

  ```sql
  SELECT p.pid, a.string_value AS description
  FROM process p
  JOIN process_track pt ON pt.upid = p.upid
  JOIN track t ON t.id = pt.id
  LEFT JOIN args a ON a.arg_set_id = t.source_arg_set_id AND a.flat_key = 'description'
  ```

**Emit a synthetic zero-duration `TYPE_INSTANT` event named `Start Process`**
on the process track itself, at the timestamp of the first non-meta event for
that pid, at most once per pid. This guarantees the track has an event, so the
track and its description always render. It is the smallest change that fixes
the visibility problem.

**A command line is read once per process, where the monitor creates it.**
Reading it at the first flush instead cost two things: a process that exited
between the poll and the flush had none left to read, and a read filed under
the pid put the first process's program on every later process that held it.
The monitor discovers a process while it is running. `create` hands the read
back with the process and the monitor forwards it to the exporter, because a
`Process` holds its identity and nothing else
([ADR-0025](0025-create-every-process-in-one-place.md)).

**The read degrades silently.** It imports `psutil` lazily. If it is not
installed, or the process is gone or inaccessible, nothing is read, a warning
says so and the trace stays valid. A process gcmon never polled has no command
line, and that is the only way to have none.

## Consequences

- You can identify processes in the UI and query them from SQL.
- The cmdline is stored twice. Accepted: the two consumers differ (UI
  rendering versus the SQL `args` table), and neither can read the other's
  copy.
- A trace carries one extra `Start Process` instant event per pid. Consumers
  that enumerate slices must filter it, as the chrome↔perfetto equivalence
  test does, since the marker is Perfetto-only.
- `psutil` stays an optional dependency (the `cmdline` extra). gcmon works
  without it, minus the cmdline.
- **A `combine` run writes no command line.** Offline conversion creates no
  process, so nothing is read.
- `description` joins the arguments with spaces and no shell quoting,
  favouring readability over round-trippability. The structured form is in
  `ProcessDescriptor.cmdline`.

## Alternatives considered

- **`ProcessDescriptor.cmdline` alone.** Rejected: not queryable from SQL, so
  the trace-processor tests could not assert on it and you could not analyse
  by it.
- **`TrackDescriptor.description` alone.** Rejected: it abandons the
  protobuf-correct field, and a future trace-processor version that does
  surface `cmdline` would find it empty.
- **Collect the cmdline in the exporter**, on the grounds that it is trace
  metadata only the Perfetto format needs, so the JSONL and stdout paths carry
  no `psutil` cost. The original decision, and **reversed**: the exporter
  learns of a process on the first flush that mentions it, which is the wrong
  moment on both counts above; offline it asked the local machine about a
  historical pid, which answers about an unrelated process once the pid has
  been reissued; and the saving was one `psutil` call per process on a path
  that already reads every process once a tick. The Perfetto-only part that
  survives is the emission, not the collection.
- **Make `psutil` a hard dependency.** Rejected: gcmon is installed next to
  the process it monitors, and graceful degradation costs one `try`/`except`.

## Implementation

- `src/gcmon/exporters/perfetto_proto.py` carries the `ProcessDescriptor`
  field numbers (`PID = 1`, `CMDLINE = 2`, `PROCESS_NAME = 6`) and
  `TrackDescriptor.description` at field 14.
- `src/gcmon/exporters/perfetto_format.py` names the `Start Process` marker
  and emits it at most once per pid.
- `src/gcmon/exporters/perfetto_process_lifetime.py` puts the cmdline on the
  `Processes` slice's BEGIN alongside the `real_start_ts` / `real_end_ts`
  annotations ([ADR-0011](0011-process-lifetime-and-ordering.md)).
- `src/gcmon/monitoring/process_registry.py` holds the read, with its lazy
  `import psutil`, behind a provider the CLI wires in.
- `src/gcmon/monitoring/monitor.py` sends the result to the exporter as it
  creates the process, and `src/gcmon/exporters/perfetto_track_state.py` keeps
  it for both emission sites.
