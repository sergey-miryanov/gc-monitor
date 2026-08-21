"""The layering, which nothing else in the suite can observe.

Every arrangement of these modules produces the same behaviour, so a
dependency inversion cannot fail a behavioural test. It fails here instead.
The walk reads the imports without running them; the check answers whether an
import crosses a layer the wrong way. See spec 0041.
"""

from __future__ import annotations

import ast
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import pytest

pytestmark = pytest.mark.layering

PACKAGE = "gcmon"
SRC = Path(__file__).resolve().parent.parent / "src" / PACKAGE

ALLOWED: dict[str, frozenset[str]] = {
    "support": frozenset(),
    "model": frozenset({"support"}),
    "exporters": frozenset({"model", "support"}),
    "stats": frozenset({"model", "support"}),
    "control": frozenset({"model", "exporters", "support"}),
    "monitoring": frozenset({"model", "exporters", "stats", "control", "support"}),
    "pyperf": frozenset({"model", "exporters", "stats", "control", "monitoring", "support"}),
    "cli": frozenset({"model", "exporters", "stats", "control", "monitoring", "pyperf", "support"}),
}
"""What each layer may import. The table lives here because it is a statement
about the architecture, and this is where such a statement can fail."""

STILL_FLAT: dict[str, str] = {
    "monitor": "monitoring",
    "monitor_loop": "monitoring",
    "events_reader": "monitoring",
    "target_process": "monitoring",
    "wait_policy": "monitoring",
    "run_policy": "monitoring",
    "rss_sampler": "monitoring",
    "child_process_runner": "monitoring",
}
"""The modules still waiting for the directory that will answer for them.
This table shrinks with each move and is gone once the tree is layered."""


@dataclass(frozen=True)
class Import:
    """One import from the package into itself, named as the walk sees it.

    ``module`` and ``imported`` are dotted paths relative to ``gcmon``, so
    ``monitor`` and ``exporters.exporter`` rather than ``gcmon.monitor``.
    """

    module: str
    imported: str
    lineno: int


def layer_of(module: str) -> str | None:
    """The layer *module* belongs to.

    The directory answers: a module under `stats/` is `stats`. Two rules make
    that true without exceptions in the tree. `commands` is part of `cli`,
    which is otherwise the package root, where `__init__.py` and `__main__.py`
    have to live. A module still sitting at the root on its way to a layer is
    named in `STILL_FLAT` until it moves.
    """
    head = module.split(".")[0]
    if head in ALLOWED:
        return head
    if head == "commands":
        return "cli"
    return STILL_FLAT.get(head, "cli")


def import_graph(root: Path) -> list[Import]:
    """Every intra-package import under *root*, read with ``ast``.

    Parsing rather than importing keeps this fast, free of import side
    effects, and independent of whether an optional dependency is installed.
    """
    graph: list[Import] = []
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        module = ".".join(path.relative_to(root).with_suffix("").parts)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Import | ast.ImportFrom):
                continue
            for imported in _targets_of(node, module, root):
                graph.append(Import(module, imported, node.lineno))
    return graph


def _targets_of(node: ast.Import | ast.ImportFrom, module: str, root: Path) -> list[str]:
    """What *node* imports from the package, relative to it, if anything."""
    if isinstance(node, ast.Import):
        return [name.name[len(PACKAGE) + 1 :] for name in node.names if name.name.startswith(f"{PACKAGE}.")]
    if node.level == 0:
        if node.module == PACKAGE:
            return _named(node, [], root)
        if node.module is not None and node.module.startswith(f"{PACKAGE}."):
            return [node.module[len(PACKAGE) + 1 :]]
        return []
    base = module.split(".")[:-1]
    if node.level > 1:
        base = base[: len(base) - (node.level - 1)]
    if node.module is not None:
        return [".".join([*base, node.module])]
    return _named(node, base, root)


def _named(node: ast.ImportFrom, base: list[str], root: Path) -> list[str]:
    """Resolve ``from <package> import name``, where *name* may be either.

    A name is a submodule if the file is there, and otherwise something the
    package's ``__init__`` exports, such as ``__version__``.
    """
    targets = []
    for name in node.names:
        dotted = [*base, name.name]
        is_module = root.joinpath(*dotted).with_suffix(".py").exists() or root.joinpath(*dotted, "__init__.py").exists()
        targets.append(".".join(dotted) if is_module else ".".join([*base, "__init__"]))
    return targets


def violations(
    graph: Sequence[Import],
    layer: Callable[[str], str | None],
    allowed: dict[str, frozenset[str]],
) -> list[str]:
    """The imports in *graph* that cross a layer the wrong way.

    One message per crossing, naming the importing module, the imported
    module and the edge that is not allowed. A bare "layering violation"
    costs more to diagnose than the rule saves.
    """
    found: list[str] = []
    for edge in graph:
        source, target = layer(edge.module), layer(edge.imported)
        if source is None or target is None or source == target:
            continue
        if target not in allowed[source]:
            found.append(f"{edge.module}:{edge.lineno} imports {edge.imported}: {source} may not import {target}")
    return found


class TestThePackageAsItStandsToday:
    """Case 1: the day-one state, which the test's first job is to record."""

    def test_no_import_crosses_a_layer_the_wrong_way(self) -> None:
        assert violations(import_graph(SRC), layer_of, ALLOWED) == []

    def test_the_walk_finds_the_imports_that_are_there(self) -> None:
        """A walk that found nothing would also report no violations."""
        graph = import_graph(SRC)
        assert len(graph) > 50
        assert any(edge.module == "monitor" and edge.imported.startswith("model.data") for edge in graph)

    def test_every_module_the_walk_sees_has_a_layer(self) -> None:
        """A module no rule places is skipped by ``violations``, so an
        unplaced one would be guarded by nothing."""
        graph = import_graph(SRC)
        seen = {edge.module for edge in graph} | {edge.imported for edge in graph}
        assert [module for module in sorted(seen) if layer_of(module) is None] == []


class TestTheLayerOfAModule:
    """The directory answers, with the package root standing for `cli`."""

    def test_a_module_in_a_layer_directory_belongs_to_that_layer(self) -> None:
        assert layer_of("support.set_on_exit") == "support"
        assert layer_of("exporters.exporter") == "exporters"

    def test_the_subcommands_are_part_of_the_cli(self) -> None:
        assert layer_of("commands.run_cmd") == "cli"

    def test_a_module_at_the_package_root_is_cli(self) -> None:
        assert layer_of("cli") == "cli"
        assert layer_of("_env") == "cli"
        assert layer_of("__init__") == "cli"

    def test_a_module_still_at_the_root_keeps_its_layer_until_it_moves(self) -> None:
        assert layer_of("monitor") == "monitoring"
        assert layer_of("rss_sampler") == "monitoring"

    def test_a_shim_left_behind_by_a_move_is_cli_like_any_root_module(self) -> None:
        """It re-exports from the layer it came from, which is downward."""
        assert layer_of("data") == "cli"


class TestAnImportThatCrossesTheWrongWay:
    """Cases 2 and 3, which the real tree cannot supply: it holds no violation."""

    def test_the_record_model_may_not_import_the_cli(self) -> None:
        graph = [Import("model.data", "cli", 3)]
        assert violations(graph, layer_of, ALLOWED) == ["model.data:3 imports cli: model may not import cli"]

    def test_the_exporters_and_stats_are_siblings_in_both_directions(self) -> None:
        assert violations([Import("exporters.exporter", "stats", 7)], layer_of, ALLOWED) == [
            "exporters.exporter:7 imports stats: exporters may not import stats"
        ]
        assert violations([Import("stats", "exporters.exporter", 7)], layer_of, ALLOWED) == [
            "stats:7 imports exporters.exporter: stats may not import exporters"
        ]

    def test_an_import_that_goes_down_is_allowed(self) -> None:
        assert violations([Import("monitor", "model.data", 11)], layer_of, ALLOWED) == []

    def test_an_import_inside_one_layer_is_allowed(self) -> None:
        assert violations([Import("exporters.exporter", "exporters.encoder", 4)], layer_of, ALLOWED) == []
