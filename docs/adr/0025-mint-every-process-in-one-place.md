# ADR-0025: Mint every process in one place, and carry it instead of a pid

- **Status:** Accepted
- **Date:** 2026-08-31

## Context

A pid belongs to the operating system, which hands the same one out again, and
gcmon monitors a *tree* of short-lived workers. So a record naming a pid does
not say which process produced it.

It already said so in one place.
[ADR-0016](0016-the-ring-is-the-statistics-unit.md) gave everything a run
keeps an epoch counting the processes that have held the pid, and the
`--stats` table prints `12345:0#2` for the second. Nothing else could: the
exporter protocol, the `Track` an event names
([ADR-0024](0024-an-event-names-the-track-it-is-drawn-on.md)) and the trace
itself all carried a number two processes shared.

Closing that gap by counting a second epoch, at the encoder, off the liveness
reports it already receives, is what [spec 0059](../../specs/RETIRED.md)
specified first. Two counters over one set of evidence can drift, and the spec
priced a cross-check in to catch it. Two things it could not do at all: the
control server takes an operating-system pid off the wire and has to draw an
instant on a process, and a pid the control server suppresses is absent from
one liveness report and present in a later one, which is the shape a
derivation reads as a new process.

## Decision

**A `Process` is `(pid, pid_epoch)`, and that pair alone is its identity.**
The epoch counts from 1. What gcmon learned about the process, its discovery
timestamp and its command line, rides along and stays out of equality, hashing
and ordering: a caller holding a pid and an epoch then reaches the rings and
tracks filed under it without reproducing what gcmon read. Ordering is by pid
then epoch, which is the order the `--stats` table prints.

**One registry mints them, and the monitor is the only caller that may.**
`create` is the only thing that makes a `Process`, and the monitor calls it as
it is about to poll a pid rather than when a listing names one. A pid gcmon
has never polled is one it knows nothing about, so evidence naming it belongs
to no process and is dropped rather than opening a process that was never
monitored.

**Below the registry a pid is an `int`; above it, a `Process`.** The reader,
the child listing, the attachment
([ADR-0020](0020-attach-to-a-process-once.md)) and the `psutil` calls take the
number the operating system gave. `Track`, the exporter protocol, the
statistics keys and the trace take the process. The registry is the one place
the two meet, so the boundary is checkable by reading a signature.

**Evidence is filed under `at(pid, ts)`, not under whatever holds the pid
now.** Evidence outlives the process it describes. A control-plane instant is
stamped when its sender named no time and can reach gcmon after the pid was
retired; a poll returns collections that already happened, and a pid pruned
from the tree loses its read cursor
([ADR-0017](0017-monitor-owns-the-pid-lifecycle.md)), so whatever claims it
next re-reads records its predecessor produced. A timestamp inside a closed
life belongs to the process that lived it. One later than every retirement is
the process running now, or the last to leave. One earlier than the first
process's discovery is that first process, since a poll can return a
collection older than gcmon's first sight of it.

**The control plane holds a read-only view, not the registry.** A
`ProcessLookup` protocol carrying `at` and nothing else lives in `model`,
which every layer may import. Minting stays the monitor's, and `control` does
not import `monitoring`, which the layer table in
`tests/architecture/test_layering.py` forbids.

**One lock, taken on every access.** The monitor writes and the control server
reads from its own thread. One write per process against one read per control
message leaves nothing to contend over. The command-line read runs outside it:
it reads another process, and a control message about a third would otherwise
wait behind it.

## Consequences

- The table and the trace cannot disagree about which process a record
  belonged to. There is one number, not two counted from the same evidence,
  and no test has to compare them.
- **The epoch still depends on gcmon seeing the departure.** A pid recycled
  between two ticks, with the listing never showing it gone, reads as one
  process throughout. ADR-0016 accepted that and the registry inherits it;
  [spec 0052](../../specs/0052-a-recycled-pid-can-be-read-through-a-stale-attachment.md)
  is the related hazard on the read path.
- **Evidence for a pid nobody minted is dropped without a warning.** The
  ordinary cause is a client naming a pid gcmon never monitored, which is the
  client's error and not gcmon's, so it is a debug log.
- **A caller cannot reconstruct a command line.** Two `Process` values that
  disagree about it are the same key, so a caller that wants one has to hold
  the value the registry minted.
- **The registry keeps every departure for the life of the run**, one entry
  per process that has left, because that is what `at` answers from.

## Alternatives considered

- **Derive the epoch at the encoder from the liveness reports**, which cost
  nothing to plumb because the reports were already arriving. Rejected on the
  two blind spots in the Context, plus a third: a command line read once per
  pid, at the first flush, names the first process's program on every later
  span of that pid
  ([ADR-0010](0010-process-identity-cmdline-and-start-marker.md)).
- **Stamp every record with its epoch at the monitor.** Rejected: it puts the
  number on the one path where it is never needed, since a record's epoch is
  implied by which process produced it, and it widens the exporter protocol
  for every format including the ones that ignore it. Naming the process on
  the `Track` an event already carries costs one field on a value that exists.
- **A run-wide process counter instead of a per-pid epoch.** Unique with no
  registry lookup, and it reads as an opaque number. `#2` means "the second
  process to hold this pid", which is what a reader of a recycled pid is
  asking, and what the table has printed since ADR-0016.
- **Hand the control server the registry itself.** It is one object and the
  control server only reads from it. Rejected: the layer table puts `control`
  below `monitoring`, so the import runs the wrong way, and a handle that can
  mint is one a later change will mint from.
- **Move the registry below both layers, into `model` or `support`.** It would
  make the import legal in either direction. Rejected: the layer a thing
  belongs to is the one that writes it, and only the monitor writes here.
- **A module-level registry.** Rejected: two runs in one process would share
  epochs, and every test would have to reset it.
- **Key the statistics on `(pid, iid, epoch)` and leave the exporters on
  pids.** What ADR-0016 shipped. Rejected: three fields where one value does,
  and the trace still could not say which process a slice belonged to, which
  is the gap spec 0059 opened with.

## Implementation

- `src/gcmon/model/process.py` holds `Process` and the `ProcessLookup`
  protocol. It is in `model` because every layer above `support` names one.
- `src/gcmon/monitoring/process_registry.py` holds the registry, the lock, the
  departure history and the `psutil` read behind an injected provider. It is
  in `monitoring` because that is the layer that writes to it, and the
  provider is injected rather than defaulted so a test naming a pid the
  machine does not have reads nothing instead of whatever holds that number.
- `src/gcmon/monitoring/monitor.py` mints as it polls and retires where it
  drops a pid's state, both halves of
  [ADR-0017](0017-monitor-owns-the-pid-lifecycle.md)'s prune.
- `src/gcmon/control/control_server.py` resolves the pid on the wire through
  the protocol and drops what resolves to nothing.
- `src/gcmon/cli/commands/monitoring_base.py` builds the one registry a run
  has, before the target starts, because the control server has to be
  listening by then.
- `tests/test_process.py` pins the identity, the ordering and the suffix;
  `tests/monitoring/test_process_registry.py` pins minting, the prune and what
  `at` answers on each side of a departure;
  `tests/architecture/test_layering.py` is where the `control`-to-`monitoring`
  edge fails, and it is deselected by default.
