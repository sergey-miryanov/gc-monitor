"""The deep import paths that moved keep answering for one release.

Spec 0041 §4.3 grants every path that tests and downstream code use today one
release on the old name. These are the shims that promise it; the release that
drops them deletes this file with them.
"""

from __future__ import annotations

import importlib

import pytest

MOVED: dict[str, tuple[str, str]] = {
    "gcmon.data": ("gcmon.model.data", "GCStatsInfo"),
    "gcmon.protocol": ("gcmon.model.protocol", "TGCStatsInfo"),
    "gcmon.loss": ("gcmon.model.loss", "RingAccumulator"),
    "gcmon.trace_event": ("gcmon.model.trace_event", "NameInfo"),
    "gcmon.poll_status": ("gcmon.model.poll_status", "PollStatus"),
    "gcmon.schedule": ("gcmon.model.schedule", "position_of"),
    "gcmon.run_report": ("gcmon.model.run_report", "RunReport"),
    "gcmon.stats": ("gcmon.stats.stats", "StreamingStats"),
    "gcmon.stats_output": ("gcmon.stats.stats_output", "StatsView"),
    "gcmon.monitor": ("gcmon.monitoring.monitor", "EventsMonitor"),
    "gcmon.monitor_loop": ("gcmon.monitoring.monitor_loop", "MonitorLoop"),
    "gcmon.events_reader": ("gcmon.monitoring.events_reader", "EventsReader"),
    "gcmon.target_process": ("gcmon.monitoring.target_process", "ExternalProcess"),
    "gcmon.wait_policy": ("gcmon.monitoring.wait_policy", "no_wait_policy"),
    "gcmon.rss_sampler": ("gcmon.monitoring.rss_sampler", "RssSampler"),
    "gcmon.run_policy": ("gcmon.monitoring.run_policy", "Runner"),
    "gcmon.child_process_runner": ("gcmon.monitoring.child_process_runner", "ChildProcessRunner"),
}
"""Each old path, the module it now lives in, and one name to ask both for."""


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
@pytest.mark.parametrize(("old", "new", "name"), [(old, new, name) for old, (new, name) in MOVED.items()])
def test_the_old_path_answers_with_what_the_new_one_holds(old: str, new: str, name: str) -> None:
    """Not merely importable: the same object, so a caller that held on to a
    class across the move still matches an instance made through the new path."""
    assert getattr(importlib.import_module(old), name) is getattr(importlib.import_module(new), name)


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
@pytest.mark.parametrize(("old", "new", "name"), [(old, new, name) for old, (new, name) in MOVED.items()])
def test_the_old_path_still_star_imports(old: str, new: str, name: str) -> None:
    """A module-level ``__getattr__`` is consulted by ``from x import *`` only
    through ``__all__``, so a shim without one imports cleanly and binds
    nothing. The failure would land at the use site, not at the import."""
    namespace: dict[str, object] = {}
    exec(f"from {old} import *", namespace)
    assert name in namespace


def test_reaching_through_a_shim_says_it_is_going() -> None:
    """A caller gets one release of notice, and can turn it into a failure of
    their own with ``-W error::DeprecationWarning``."""
    with pytest.warns(DeprecationWarning, match="one release"):
        _ = importlib.import_module("gcmon.monitor").EventsMonitor


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
def test_a_name_that_was_never_there_still_fails() -> None:
    """The shim forwards; it does not invent."""
    assert not hasattr(importlib.import_module("gcmon.data"), "no_such_name")
