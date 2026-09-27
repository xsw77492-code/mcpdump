"""Automated checks on the layering dependency direction.

AST analysis turns the layering rules into executable assertions. Runtime imports and
``TYPE_CHECKING`` imports are distinguished, and constraints are an allowlist, not a blocklist.
"""

from __future__ import annotations

import ast
import pathlib
from collections import defaultdict
from dataclasses import dataclass

from conftest import ROOT

SRC = ROOT / "src"

#: Zero-dependency "vocabulary" modules: any layer may reference them without crossing a boundary.
#: The question is "could it depend back", not "is it important": both define only constants and
#: pure lookups with zero internal imports, which ``TestHardRules`` pins separately.
VOCABULARY = frozenset({"mcpdump.i18n", "mcpdump.exits"})

#: Top-level packages each package may depend on at runtime; ``VOCABULARY`` and the root are
#: allowed separately, so only layer-specific edges appear here. Layer order (bottom → top):
#: ``core`` → ``ui`` → ``services`` ≈ ``runtime`` → ``commands`` → ``cli``.
ALLOWED: dict[str, frozenset[str]] = {
    # Text source and exit-code contract must sit lowest: both are leaves, so the empty sets
    # here are a declaration, not a placeholder — add nothing.
    "mcpdump.i18n": frozenset(),
    "mcpdump.exits": frozenset(),
    "mcpdump": frozenset(),
    # The bundled example MCP server. Empty on purpose: it must depend on nothing to serve as
    # ``check``'s golden server. If it ever needs core, re-think whether that still holds.
    "mcpdump.demo": frozenset(),
    "mcpdump.core": frozenset({"mcpdump.core"}),
    "mcpdump.ui": frozenset({"mcpdump.ui", "mcpdump.core"}),
    "mcpdump.services": frozenset({"mcpdump.services", "mcpdump.core", "mcpdump.ui"}),
    "mcpdump.runtime": frozenset({"mcpdump.runtime", "mcpdump.core", "mcpdump.ui"}),
    "mcpdump.commands": frozenset(
        {"mcpdump.commands", "mcpdump.core", "mcpdump.services", "mcpdump.ui", "mcpdump.runtime"}
    ),
    "mcpdump.cli": frozenset({"mcpdump.commands", "mcpdump.runtime", "mcpdump.ui"}),
    "mcpdump.__main__": frozenset({"mcpdump.cli"}),
}

#: The package root holds only ``__version__`` and the default protocol version, so it is a leaf
#: referenceable from any layer; stated once here rather than per layer.
ROOT_PACKAGE = "mcpdump"


@dataclass(frozen=True)
class Edge:
    """One internal import edge. A false ``runtime`` means it lives only in the type checker."""

    source: str
    target: str
    runtime: bool

    def __str__(self) -> str:
        kind = "imports" if self.runtime else "annotates with"
        return f"{self.source} {kind} {self.target}"


def _module_name(path: pathlib.Path) -> str:
    """``src/mcpdump/services/discovery.py`` → ``mcpdump.services.discovery``。"""
    rel = path.relative_to(SRC).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _top(module: str) -> str:
    """``mcpdump.services.checks.base`` → ``mcpdump.services``。"""
    return ".".join(module.split(".")[:2])


#: ``mcpdump/__init__.py`` holds only constants; any layer may read it, and it references no
#: internal module itself (see ``test_mcpdump_init_has_no_internal_dependencies``).
CORE_CONSTANTS = "mcpdump"


def _allowed_for(source_top: str) -> frozenset[str]:
    return ALLOWED.get(source_top, frozenset()) | VOCABULARY | {CORE_CONSTANTS}


def _is_type_checking(test: ast.expr) -> bool:
    """Both ``if TYPE_CHECKING:`` and ``if typing.TYPE_CHECKING:`` are recognised."""
    if isinstance(test, ast.Name):
        return test.id == "TYPE_CHECKING"
    return isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"


def _annotation_only(tree: ast.Module) -> set[int]:
    """Collect the node ``id``s of everything inside ``if TYPE_CHECKING:`` blocks."""
    inside: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and _is_type_checking(node.test):
            for child in node.body:
                inside.update(id(sub) for sub in ast.walk(child))
    return inside


def _targets(node: ast.Import | ast.ImportFrom, package: str) -> list[str]:
    """Resolve an import statement into fully-qualified target modules (``mcpdump``-internal)."""
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if node.level == 0:
        return [node.module] if node.module else []
    # Relative import: ``level`` is how many levels up. ``__package__`` has already been
    # computed at the call site depending on ``__init__.py``, so this only subtracts.
    parts = package.split(".")
    base = parts[: len(parts) - (node.level - 1)]
    if node.module:
        base = [*base, *node.module.split(".")]
    return [".".join(base)]


def _sources() -> list[pathlib.Path]:
    return sorted(SRC.rglob("*.py"))


def _edges() -> list[Edge]:
    out: list[Edge] = []
    for path in _sources():
        name = _module_name(path)
        package = name if path.name == "__init__.py" else name.rsplit(".", 1)[0]
        tree = ast.parse(path.read_text(encoding="utf-8"))
        inside = _annotation_only(tree)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            runtime = id(node) not in inside
            for target in _targets(node, package):
                if target == "mcpdump" or target.startswith("mcpdump."):
                    out.append(Edge(name, target, runtime))
    return out


def _runtime_edges() -> list[Edge]:
    return [edge for edge in _edges() if edge.runtime]


def _cross_package_edges(*, runtime_only: bool = True) -> list[Edge]:
    """Only cross-package edges are considered.

    References within a package collapse to a self-loop once aggregated to the top-level
    package, not a cycle; the layering rule cares about "between layers".
    """
    return [
        edge
        for edge in (_runtime_edges() if runtime_only else _edges())
        if _top(edge.source) != _top(edge.target)
    ]


def _edges_between(
    source_top: str,
    target_top: str | None = None,
    *,
    runtime_only: bool = True,
) -> list[Edge]:
    """Select edges between two top-level packages; omitting ``target_top`` means "anywhere".

    The six ``TestHardRules`` tests ask the same question, so the filter lives here once.
    """
    edges = _runtime_edges() if runtime_only else _edges()
    return [
        edge
        for edge in edges
        if _top(edge.source) == source_top
        and (target_top is None or _top(edge.target) == target_top)
    ]


# ---------------------------------------------------------------- layering


class TestLayering:
    def test_no_package_depends_on_something_outside_its_allowlist(self) -> None:
        violations = [
            edge
            for edge in _cross_package_edges()
            if _top(edge.target) not in _allowed_for(_top(edge.source))
        ]
        assert not violations, "out-of-bounds dependency:\n" + "\n".join(str(e) for e in violations)

    def test_every_package_is_covered_by_the_allowlist(self) -> None:
        """A new package with no rule is unconstrained — a silent failure is worse than an error."""
        tops = {_top(_module_name(path)) for path in _sources()}
        assert tops <= set(ALLOWED), f"unregistered rule: {sorted(tops - set(ALLOWED))}"

    def test_the_runtime_graph_is_acyclic(self) -> None:
        """A cycle makes "import order" an implicit contract — a bug seen from one entry point."""
        graph: dict[str, set[str]] = defaultdict(set)
        for edge in _cross_package_edges():
            graph[_top(edge.source)].add(_top(edge.target))
        assert _find_cycle(graph) is None, f"runtime dependency cycle: {_find_cycle(graph)}"


class TestHardRules:
    """The four most important edges are pinned individually; a wrong table makes them fire."""

    def test_core_never_imports_ui(self) -> None:
        """``core`` understands only the protocol; once it knows about a terminal it needs a UI."""
        assert not _edges_between("mcpdump.core", "mcpdump.ui")

    def test_ui_never_imports_services(self) -> None:
        """Once ``runtime → ui → services → runtime`` cycles, import order is a hidden hazard."""
        assert not _edges_between("mcpdump.ui", "mcpdump.services")

    def test_i18n_has_no_internal_dependencies(self) -> None:
        """The text table is a leaf; whatever it depends on suffers "editing copy edits code"."""
        assert not _edges_between("mcpdump.i18n", runtime_only=False)

    def test_exits_has_no_internal_dependencies(self) -> None:
        """The exit-code contract must be referenceable from any layer, so it references none."""
        assert not _edges_between("mcpdump.exits", runtime_only=False)

    def test_services_touches_runtime_only_for_annotations(self) -> None:
        """``runtime`` sits above ``services``. An annotation is fine; a runtime import is not."""
        offenders = _edges_between("mcpdump.services", "mcpdump.runtime")
        assert not offenders, "\n".join(str(e) for e in offenders)

    def test_only_runtime_imports_are_constrained(self) -> None:
        """Reverse check: ``services`` really does hold an annotation edge to ``runtime``.

        Without it, the test above might pass only because that edge does not exist at all.
        """
        annotation_edges = [
            edge
            for edge in _edges_between("mcpdump.services", "mcpdump.runtime", runtime_only=False)
            if not edge.runtime
        ]
        assert annotation_edges, (
            "the expected annotation edge is missing: either it became a runtime import, "
            "or the rule needs a rewrite"
        )


# ---------------------------------------------------------------- source hygiene


class TestSourceHygiene:
    def test_no_platform_branching_on_sys_platform(self) -> None:
        """Platform detection always goes through injection.

        ``discovery`` promised no ``if sys.platform`` anywhere: that is the reason it can test
        macOS paths on Windows, and breaking it leaves the branch half covered.
        """
        offenders: list[str] = []
        for path in _sources():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.If):
                    continue
                if any(
                    isinstance(sub, ast.Attribute)
                    and sub.attr == "platform"
                    and isinstance(sub.value, ast.Name)
                    and sub.value.id == "sys"
                    for sub in ast.walk(node.test)
                ):
                    offenders.append(f"{path.relative_to(SRC)}:{node.lineno}")
        assert not offenders, (
            "route the platform check through parameter injection:\n" + "\n".join(offenders)
        )

    def test_ui_submodules_are_not_reached_into_from_outside(self) -> None:
        """``ui`` only takes things from the package's exports.

        ``from ..ui.render import X`` bypasses ``ui/__init__``, so "colour via theme, width via
        width" is no longer enforceable.
        """
        offenders = [
            e
            for e in _runtime_edges()
            if _top(e.source) != "mcpdump.ui" and e.target.startswith("mcpdump.ui.")
        ]
        assert not offenders, "\n".join(str(e) for e in offenders)

    def test_all_internal_imports_are_relative(self) -> None:
        """Absolute imports hide which layer the code is in."""
        offenders: list[str] = []
        for path in _sources():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.level == 0:
                    if node.module and node.module.split(".")[0] == "mcpdump":
                        offenders.append(f"{path.relative_to(SRC)}:{node.lineno}")
        assert not offenders, "\n".join(offenders)

    def test_every_module_has_a_docstring(self) -> None:
        """A module docstring is the only on-the-spot record of why the file exists."""
        missing = [
            str(path.relative_to(SRC))
            for path in _sources()
            if ast.get_docstring(ast.parse(path.read_text(encoding="utf-8"))) is None
        ]
        assert not missing, "\n".join(missing)

    def test_every_public_function_has_a_return_annotation(self) -> None:
        """The return annotation is not decoration — it makes "does this return anything" clear."""
        missing: list[str] = []
        for path in _sources():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if node.name.startswith("_") and not node.name.startswith("__"):
                    continue
                if node.returns is None:
                    missing.append(f"{path.relative_to(SRC)}:{node.lineno} {node.name}")
        assert not missing, "\n".join(missing)

    def test_no_type_ignore_comments(self) -> None:
        """``type: ignore`` silences the type checker, and the silenced spot is often exactly
        where the real problem is.
        """
        offenders = [
            f"{path.relative_to(SRC)}:{index}"
            for path in _sources()
            for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
            if "type: ignore" in line
        ]
        assert not offenders, "\n".join(offenders)


def _find_cycle(graph: dict[str, set[str]]) -> list[str] | None:
    """Depth-first search for a cycle; returns the node sequence around it (closing node again)."""
    grey, black = 1, 2
    color: dict[str, int] = {}
    stack: list[str] = []

    def visit(node: str) -> list[str] | None:
        color[node] = grey
        stack.append(node)
        for nxt in sorted(graph.get(node, ())):
            if color.get(nxt) == grey:
                return [*stack[stack.index(nxt) :], nxt]
            if nxt not in color and (found := visit(nxt)):
                return found
        stack.pop()
        color[node] = black
        return None

    for node in sorted(graph):
        if node not in color and (found := visit(node)):
            return found
    return None
