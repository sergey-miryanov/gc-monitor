# ADR-0010: Carry process cmdline in two places, and force the process track to render

- **Status:** Accepted
- **Date:** 2026-06-08 (`Start Process` marker added 2026-06-27)

## Context

gcmon discovers child PIDs at runtime and monitors them alongside the main process, so a
trace routinely contains several processes. `Process 4821` is not enough to tell them
apart, so you need the command line.

Perfetto's `ProcessDescriptor` has a `cmdline` field (field 2, repeated string) for this.
Two problems stood in the way.

**The trace processor does not surface it.** `ProcessDescriptor.cmdline` is not exposed via
the SQL `process.cmdline` column, which always returns `None`. Writing it correctly makes
the data visible in the UI but unqueryable from SQL, which is how the trace-processor tests
and any user analysis read a trace.

**The process track was often invisible.** The Perfetto UI renders a track's
`description` only when the track has at least one event on it. The process track is
OS-scoped and receives events only from `InstantEvent`s, since Begin, End and Counter
events live on child tracks. A trace with no instant events for a pid therefore had an
empty process track, and the UI hid its description.

## Decision

**Write the cmdline twice, on purpose.**

- `ProcessDescriptor.cmdline` (field 2, repeated string), protobuf-correct and visible in
  the UI.
- `TrackDescriptor.description` (field 14, string), the space-joined cmdline, on the
  process track. This one *is* surfaced in the `args` table and joins to `process` via
  `track`, so it is queryable:

  ```sql
  SELECT p.pid, a.string_value AS description
  FROM process p
  JOIN process_track pt ON pt.upid = p.upid
  JOIN track t ON t.id = pt.id
  LEFT JOIN args a ON a.arg_set_id = t.source_arg_set_id AND a.flat_key = 'description'
  ```

**Emit a synthetic zero-duration `TYPE_INSTANT` event named `Start Process`** on the
process track itself, at the timestamp of the first non-meta event for that pid, at most
once per pid. This guarantees the track has an event, so the track and its description
always render. It is the smallest change that fixes the visibility problem.

**Collection is the exporter's job and degrades silently.** The provider imports `psutil`
lazily. If it is not installed, or the process is gone or inaccessible
(`psutil.Error`, `OSError`, `PermissionError`), the provider returns `None` and drops the
cmdline. No exception escapes, and the trace stays valid.

The exporter collects cmdline for the main pid and each child, and emits it on the first
`TrackDescriptor` for each. [ADR-0008](0008-buffered-exporter-and-encoder-protocol.md)'s
atomic meta building guarantees that happens exactly once.

## Consequences

- You can identify processes in the UI and query them from SQL.
- The cmdline is stored twice. Accepted: the two consumers differ (UI rendering versus the
  SQL `args` table), and neither can read the other's copy.
- A trace carries one extra `Start Process` instant event per pid. Consumers that
  enumerate slices must filter it, as the chrome↔perfetto equivalence test does, since the
  marker is Perfetto-only.
- `psutil` stays an optional dependency (the `cmdline` extra). gcmon works without it,
  minus the cmdline.
- In a `combine` run the pids are historical and the processes are usually gone, so
  `psutil.NoSuchProcess` is the normal case: the encoder logs a warning and emits the
  descriptor without a cmdline. Same for a monitored process that exits before the first
  flush, and for cross-PID-namespace environments such as containers.
- `description` joins the arguments with spaces and no shell quoting, favouring readability
  over round-trippability. The structured form is in `ProcessDescriptor.cmdline`.

## Alternatives considered

- **`ProcessDescriptor.cmdline` alone.** Rejected: not queryable from SQL, so the
  trace-processor tests could not assert on it and you could not analyse by it.
- **`TrackDescriptor.description` alone.** Rejected: it abandons the protobuf-correct
  field, and a future trace-processor version that does surface `cmdline` would find it
  empty.
- **Collect cmdline in `monitor_loop.py` / `monitor.py` or the CLI.** Rejected: cmdline is
  trace metadata that only the Perfetto format needs. Keeping collection in the exporter
  layer means the JSONL, Chrome and stdout paths carry no `psutil` cost. Unchanged by
  monitor-reported liveness ([ADR-0011](0011-process-lifetime-and-ordering.md)), which reports
  observations from the loop but still asks the exporter for no metadata. It does mean a pid
  seen only through liveness gets a slice with no cmdline, since the fetch hangs off the event
  path.
- **Make `psutil` a hard dependency.** Rejected: gcmon is installed next to the process it
  monitors, and graceful degradation costs one `try`/`except`.

## Implementation

- `src/gcmon/exporters/perfetto_proto.py` carries the `ProcessDescriptor` field numbers
  (`PID = 1`, `CMDLINE = 2`, `PROCESS_NAME = 6`) and `TrackDescriptor.description` at
  field 14.
- `src/gcmon/exporters/perfetto_format.py` names the `Start Process` marker and emits it at
  most once per pid.
- `src/gcmon/exporters/perfetto_process_lifetime.py` puts the cmdline on the `Processes`
  slice's BEGIN alongside the `real_start_ts` / `real_end_ts` annotations
  ([ADR-0011](0011-process-lifetime-and-ordering.md)).
- `src/gcmon/exporters/encoder.py` holds the default provider, with its lazy
  `import psutil`, and registers each pid's cmdline once.
- `src/gcmon/exporters/perfetto_track_state.py` stores a pid's cmdline and hands it back.
