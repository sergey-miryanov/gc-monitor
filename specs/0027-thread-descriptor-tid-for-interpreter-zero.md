# 0027 — Report the interpreter id as the thread tid for every interpreter

- **Status:** Not started
- **Kind:** bug — reporting
- **Effort:** XS
- **Origin:** post-v0.2.0 code review (old spec 18, REQ-15)
- **Respects:** [ADR-0002](../docs/adr/0002-perfetto-track-uuid-and-hierarchy.md) (every track is explicitly parented), [ADR-0013](../docs/adr/0013-rss-sampling.md) (`tid = -1` sentinel), [ADR-0015](../docs/adr/0015-gc-loss-spans-on-their-own-track.md) (`tid = -2 - iid` sentinel)

## 1. Problem

Someone writing a PerfettoSQL query to attribute GC activity to an interpreter cannot read the
interpreter id out of the `thread` table uniformly. For every interpreter but the first,
`thread.tid` is the interpreter id. For the main interpreter it is the **process id** instead,
so a query joining GC slices back to interpreters needs a special case that says "if `tid`
equals `pid`, this means interpreter 0" — and that special case is wrong for anyone whose
subinterpreter id happens to equal the pid.

## 2. Evidence

`perfetto_format._emit_thread_descriptor` builds the descriptor with

```python
tid=pid if iid == 0 else iid,
```

Every other tid gcmon publishes is derived from `iid` alone and carries no pid: `RSS_TID` is
`-1` (ADR-0013), the loss track uses `loss_tid(iid) == -2 - iid` (ADR-0015), and
`BufferedTraceExporter._build_meta` names the thread `f"Thread {iid}"` — the very descriptor
whose `tid` disagrees with its own name. The `iid == 0` branch is the only place in the
codebase where a tid is a pid.

The likely intent is to mimic Linux, where the main thread's tid equals the pid. But gcmon's
tids are not OS thread ids at all: they are interpreter ids in a synthetic namespace, and two
of the three sentinel values are negative numbers no OS would issue.

## 3. Scope

**Affected:** the Perfetto `ThreadDescriptor` for `iid == 0` — that is, every trace, since the
main interpreter is always present. Visible as `thread.tid` in the trace processor and in the
Perfetto UI's thread details.

**Not affected:** the Chrome JSON path, which carries `tid` from the `TraceEvent` and already
uses `iid` unconditionally. Track UUIDs, parenting and slice content are untouched — only the
`tid` field inside the descriptor changes. The loss and RSS tracks emit their own descriptors
and never reach this branch.

**Why the suite didn't catch it:** nothing asserts on `thread.tid` through SQL.
`test_dump_thread_table` prints the table without asserting, and the builder test constructs a
descriptor directly with an explicit `tid=0`, bypassing `_emit_thread_descriptor` entirely. So
the branch has no coverage in either direction.

## 4. Proposed change

1. `tid=pid if iid == 0 else iid` → `tid=iid`.
2. Add the SQL assertion described in §5 so the convention is stated somewhere executable.
3. If the trace processor turns out to *depend* on `tid == pid` to identify a main thread —
   see §5 for how this is settled — keep the current expression and replace this spec with a
   comment on the branch saying why, plus a note in
   [docs/perfetto-sql.md](../docs/perfetto-sql.md) telling query authors about the special
   case. Silence is the only outcome this spec rules out.

## 5. Seams and testing decisions

- **Seam:** the trace processor, via `tests/exporters/test_perfetto_exporter_integration.py`.
  `thread.tid` is a real column there, so this is the highest seam that can see the defect —
  and the only one that can settle §4.3, since what matters is how the trace processor
  interprets the field, not what bytes we wrote.
- **New seam needed:** none. `test_dump_thread_table` already queries the table; it needs
  assertions rather than prints.
- **What makes a good test here:** emit two interpreters for one pid and assert the `thread`
  rows' `tid` values are exactly `{0, 1}`. A test that only checks the descriptor bytes would
  confirm we wrote what we meant to write and tell us nothing about whether the trace
  processor still associates the thread with its process.
- **Prior art:** the `JOIN thread th ON tt.utid = th.utid` queries already in
  `test_perfetto_exporter_integration.py` and `test_perfetto_loss_track.py`.
- **Cases:**
  1. Two interpreters, one pid: `thread.tid` is `{0, 1}`, and both rows still join to the
     right `process.pid` through `upid`.
  2. Regression guard: the thread name stays `Thread {iid}`, the track hierarchy is unchanged,
     and the loss/RSS descriptors keep their sentinel tids.

## 6. Out of scope

- Renaming the concept. `tid` is Perfetto's field name; gcmon's own vocabulary already calls
  the value an **iid**, and the mapping between them is deliberate.
- The `RSS_TID` and `loss_tid` sentinels. They are settled by ADR-0013 and ADR-0015 and are
  what makes the `iid == 0` case look out of place.
- Documenting the tid convention in [docs/perfetto-sql.md](../docs/perfetto-sql.md). Worth
  doing either way, but it belongs with whichever outcome §4 reaches, not ahead of it.
