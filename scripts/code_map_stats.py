"""Structural statistics for docs/code-map.md: module count, line counts, import graph, cycles.

    python scripts/code_map_stats.py

The numbers in docs/code-map.md come from this script; run it after moving modules and update the
document where the output differs. Counting rules (they decide the totals, so they are explicit):

- A module is a ``.py`` file. ``src/agentcad/__init__.py`` is module ``agentcad``;
  ``src/agentcad/ops/__init__.py`` is module ``agentcad.ops``.
- An edge is a unique ``(importer, imported)`` pair of internal modules. ``from . import a, b``
  yields one edge per imported alias; repeated imports collapse into one edge.
- An edge counts as *module-level* if at least one import of that target is not lexically inside a
  function (``if``/``try`` blocks at module level count as module-level). Otherwise it is *lazy*
  (the function-local imports the code uses to break import cycles).
- ``if TYPE_CHECKING:`` imports are not load-time dependencies and are excluded.
- ``ezdxf`` importers are modules with a real ``import ezdxf`` / ``from ezdxf import ...``
  statement. Comments and docstrings do not count.
"""
from __future__ import annotations

import ast
import collections
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
PKG = ROOT / "src" / "agentcad"

GROUPS = {
    "操作层 ops/* + model/units/geom/constraints": (
        "agentcad.ops.", "agentcad.ops", "agentcad.model", "agentcad.units", "agentcad.geom", "agentcad.constraints"),
    "规格与工程域 spec/docspec/engineering/*/stdlib": (
        "agentcad.engineering.", "agentcad.engineering", "agentcad.spec", "agentcad.docspec", "agentcad.drawing_frontend",
        "agentcad.stdlib", "agentcad.tabular", "agentcad.dimension_display", "agentcad.appearance"),
    "接口与进程 cli/mcp_server/http_api/viewer/worker": (
        "agentcad.cli", "agentcad.mcp_server", "agentcad.http_api", "agentcad.worker", "agentcad.sessions",
        "agentcad.progress", "agentcad.viewer", "agentcad.viewer_link", "agentcad.demo", "agentcad.discovery",
        "agentcad.mcp_config"),
    "事务骨架 service/engine/store/locking/policy/guards/errors": (
        "agentcad.service", "agentcad.engine", "agentcad.store", "agentcad.locking", "agentcad.policy",
        "agentcad.guards", "agentcad.errors", "agentcad"),
    "验证与度量 validate/*/quality/verification/measure/fits/tolerances": (
        "agentcad.validate.", "agentcad.validate", "agentcad.quality", "agentcad.verification", "agentcad.measure",
        "agentcad.fits", "agentcad.tolerances"),
    "平台与后端 install/setup_gui/desktop/backends/*": (
        "agentcad.install", "agentcad.setup_gui", "agentcad.desktop", "agentcad.backends.", "agentcad.backends"),
    "输出与媒体 plot/delivery/images/scripting": (
        "agentcad.plot", "agentcad.delivery", "agentcad.images", "agentcad.image_dimensions",
        "agentcad._image_runner", "agentcad._script_runner", "agentcad.scripting", "agentcad.metadata"),
}


def module_name(path: pathlib.Path) -> str:
    rel = path.relative_to(PKG).as_posix()
    if rel.endswith(".py"):
        rel = rel[:-3]
    if rel == "__init__":
        rel = ""
    elif rel.endswith("/__init__"):
        rel = rel[: -len("/__init__")]
    return "agentcad" + ("." + rel.replace("/", ".") if rel else "")


def imports_of(tree: ast.Module, package: str, self_name: str) -> dict[str, bool]:
    """Internal module targets of one module, mapped to True when the import is function-local."""
    found: dict[str, bool] = {}

    def resolve(name: str) -> str | None:
        while name:
            if name in MODULES:
                return name if name != self_name else None
            name = name.rsplit(".", 1)[0] if "." in name else ""
        return None

    def record(name: str, lazy: bool) -> None:
        target = resolve(name)
        if target is not None:
            found[target] = found.get(target, True) and lazy

    def scan(node: ast.AST, lazy: bool) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                scan(child, True)
                continue
            if isinstance(child, ast.If) and _is_type_checking(child.test):
                continue
            targets: list[str] = []
            if isinstance(child, ast.Import):
                targets = [a.name for a in child.names if a.name.startswith("agentcad")]
            elif isinstance(child, ast.ImportFrom):
                if child.level:
                    base = package.split(".")
                    base = base[: len(base) - (child.level - 1)]
                    if child.module:
                        targets = [".".join(base + [child.module])]
                    else:
                        targets = [".".join(base + [a.name]) for a in child.names]
                elif child.module and child.module.startswith("agentcad"):
                    targets = [child.module]
            for name in targets:
                record(name, lazy)
            scan(child, lazy)

    scan(tree, False)
    return found


def _is_type_checking(test: ast.AST) -> bool:
    if isinstance(test, ast.Name):
        return test.id == "TYPE_CHECKING"
    return isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"


def main() -> int:
    global MODULES
    files = sorted(PKG.rglob("*.py"))
    MODULES = {module_name(f): f for f in files}
    loc = {name: len(path.read_text(encoding="utf-8").splitlines()) for name, path in MODULES.items()}
    sources = {name: path.read_text(encoding="utf-8") for name, path in MODULES.items()}

    edges: set[tuple[str, str]] = set()
    lazy: set[tuple[str, str]] = set()
    ezdxf: list[str] = []
    for name, path in MODULES.items():
        tree = ast.parse(sources[name])
        package = name if path.name == "__init__.py" else name.rsplit(".", 1)[0]
        found = imports_of(tree, package, name)
        for target, is_lazy in found.items():
            edges.add((name, target))
            if is_lazy:
                lazy.add((name, target))
        if any(isinstance(n, ast.Import) and any(a.name == "ezdxf" or a.name.startswith("ezdxf.") for a in n.names)
               or isinstance(n, ast.ImportFrom) and n.level == 0 and (n.module or "").split(".")[0] == "ezdxf"
               for n in ast.walk(tree)):
            ezdxf.append(name)

    out = collections.Counter(a for a, _ in edges)
    inc = collections.Counter(b for _, b in edges)
    leaves = sorted(m for m in MODULES if m not in out)
    top_level = edges - lazy

    print(f"modules: {len(MODULES)}  lines: {sum(loc.values())}")
    print("\ngroups:")
    used: set[str] = set()
    def matches(module: str, prefixes: tuple[str, ...]) -> bool:
        return any(module.startswith(p) if p.endswith(".") else module == p for p in prefixes)

    for label, prefixes in GROUPS.items():
        members = [m for m in MODULES if m not in used and matches(m, prefixes)]
        used.update(members)
        print(f"  {label:<62s} {len(members):2d} files {sum(loc[m] for m in members):6d} lines")
    missing = sorted(set(MODULES) - used)
    print(f"  ungrouped: {missing or 'none'}")

    print(f"\nedges: {len(edges)}  module-level: {len(top_level)}  lazy-only: {len(edges - top_level)}")
    print("in-degree:", ", ".join(f"{m} {n}" for m, n in inc.most_common(8)))
    print("out-degree:", ", ".join(f"{m} {n}" for m, n in out.most_common(8)))
    print(f"leaves ({len(leaves)}):", ", ".join(leaves))
    print(f"ezdxf importers ({len(ezdxf)}):", ", ".join(ezdxf))

    print("\ncycles (all edges):")
    for group in cycles(edges):
        print("  ", sorted(group))
    print("cycles (module-level edges only):", [sorted(g) for g in cycles(top_level)] or "none")

    biggest = sorted(loc, key=lambda m: -loc[m])[:12]
    print(f"\ntop 12 files: {sum(loc[m] for m in biggest)} lines "
          f"= {100 * sum(loc[m] for m in biggest) / sum(loc.values()):.1f}%")
    print("  " + ", ".join(f"{m} {loc[m]}" for m in biggest))
    return 0


def cycles(edges: set[tuple[str, str]]) -> list[list[str]]:
    """Strongly connected components with more than one module (Tarjan)."""
    graph: dict[str, set[str]] = collections.defaultdict(set)
    for a, b in edges:
        graph[a].add(b)
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: dict[str, bool] = {}
    stack: list[str] = []
    components: list[list[str]] = []
    counter = [0]

    def strong(node: str) -> None:
        index[node] = low[node] = counter[0]
        counter[0] += 1
        stack.append(node)
        on_stack[node] = True
        for nxt in graph[node]:
            if nxt not in index:
                strong(nxt)
                low[node] = min(low[node], low[nxt])
            elif on_stack.get(nxt):
                low[node] = min(low[node], index[nxt])
        if low[node] == index[node]:
            component = []
            while True:
                member = stack.pop()
                on_stack[member] = False
                component.append(member)
                if member == node:
                    break
            if len(component) > 1:
                components.append(component)

    for node in list(graph):
        if node not in index:
            strong(node)
    return components


MODULES: dict[str, pathlib.Path] = {}

if __name__ == "__main__":
    sys.exit(main())
