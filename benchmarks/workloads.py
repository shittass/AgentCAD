"""What the local benchmark measures: the runtime's own calls, without an LLM and without AutoCAD.

Every workload group returns :class:`Sample` rows timed in a fresh workspace. ``quick`` keeps the
whole run near ten seconds; ``full`` adds the slow paths (large drawings, exports, MCP process).

The point is not absolute speed (that belongs to the machine) but the relative numbers a change
moves: per-call latency, the cost of the transaction layer, payload sizes an agent pays for, and
the wall-clock of one realistic drafting session.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .harness import Sample, Timing, measure, measure_process

# The example spec shipped with the repository: a 100x60 plate with four R8 corner holes.
SPEC = {
    "kind": "plate", "units": "mm", "name": "benchmark plate", "width": 100, "height": 60,
    "holes": [{"pattern": "corners", "diameter": 8, "edge_offset": 10}], "material": "Q235", "thickness": 3,
}

DIMS = [
    {"op": "add_linear_dim", "p1": [0, 0], "p2": [100, 0], "offset": -10, "tag": "d_width"},
    {"op": "add_linear_dim", "p1": [0, 0], "p2": [0, 60], "orientation": "vertical", "offset": 10, "tag": "d_height"},
    {"op": "add_diameter_dim", "target": "tag:hole0", "tag": "d_hole"},
]


def rect_ops(width: float, height: float) -> list[dict]:
    """A closed outline as four lines, the way an agent draws a plate without a spec."""
    return [
        {"op": "add_line", "start": [0, 0], "end": [width, 0], "layer": "OUTLINE", "tag": "edge_s"},
        {"op": "add_line", "start": [width, 0], "end": [width, height], "layer": "OUTLINE", "tag": "edge_e"},
        {"op": "add_line", "start": [width, height], "end": [0, height], "layer": "OUTLINE", "tag": "edge_n"},
        {"op": "add_line", "start": [0, height], "end": [0, 0], "layer": "OUTLINE", "tag": "edge_w"},
    ]


def hole_ops(count: int, diameter: float = 8.0, offset: float = 10.0, width: float = 100.0,
             height: float = 60.0, tag_prefix: str = "hole") -> list[dict]:
    """``count`` holes spaced along the mid-line, replaceable by tag so repeated calls stay one steady state."""
    ops = []
    for i in range(count):
        x = offset + (width - 2 * offset) * (i + 0.5) / count
        ops.append({"op": "add_circle", "center": [round(x, 3), height / 2], "diameter": f"{diameter}mm",
                    "layer": "HOLES", "tag": f"{tag_prefix}{i}", "if_tag_exists": "replace"})
    return ops


@dataclass
class Workload:
    """One group of timed calls; ``only`` selects groups by this name."""

    name: str
    description: str
    profiles: frozenset[str]
    fn: Callable[["Context"], list[Sample]]


WORKLOADS: list[Workload] = []


def workload(name: str, *, description: str, profiles: tuple[str, ...] = ("quick", "full")):
    def register(fn: Callable[["Context"], list[Sample]]) -> Callable[["Context"], list[Sample]]:
        WORKLOADS.append(Workload(name, description, frozenset(profiles), fn))
        return fn
    return register


@dataclass
class Context:
    """One benchmark run: a workspace, its runtime, and the repeat policy."""

    root: Path
    runtime: Any
    profile: str
    repeat_override: int | None = None
    process_env: dict[str, str] = field(default_factory=dict)

    def repeat(self, base: int) -> int:
        if self.repeat_override is not None:
            return max(1, self.repeat_override)
        return max(1, base * (2 if self.profile == "full" else 1))

    def path(self, group: str, name: str) -> str:
        """A workspace-relative drawing path, with its directory created."""
        (self.root / group).mkdir(parents=True, exist_ok=True)
        return f"{group}/{name}"

    def plate(self, group: str, name: str = "plate.dxf") -> str:
        target = self.path(group, name)
        result = self.runtime.draw_from_spec(SPEC, target)
        if not result["ok"]:
            raise RuntimeError(f"could not draw the benchmark plate: {result.get('error')}")
        return target

    def python(self, *args: str) -> list[str]:
        return [sys.executable, *args]

    def measure_argv(self, argv: list[str], repeat: int) -> Any:
        return measure_process(argv, repeat=repeat, env=self.process_env, cwd=self.root)


@workload("startup", description="cold interpreter and CLI startup an agent pays once per session")
def startup(ctx: Context) -> list[Sample]:
    out = [Sample("cli_help", "startup", ctx.measure_argv(ctx.python("-m", "agentcad", "--help"),
                                                          repeat=ctx.repeat(3)))]
    if ctx.profile == "full":
        out.append(Sample("import_service", "startup",
                          ctx.measure_argv(ctx.python("-c", "import agentcad.service"), repeat=ctx.repeat(2))))
    return out


@workload("drawing", description="create and generate drawings from a spec")
def drawing(ctx: Context) -> list[Sample]:
    counter = iter(range(10_000))

    def fresh_create() -> dict:
        return ctx.runtime.create_drawing(ctx.path("drawing", f"empty{next(counter)}.dxf"))

    def fresh_spec() -> dict:
        return ctx.runtime.draw_from_spec(SPEC, ctx.path("drawing", f"spec{next(counter)}.dxf"))

    out = [Sample("create_drawing", "drawing", measure(fresh_create, repeat=ctx.repeat(5), payload=True),
                  extra={"note": "mechanical template, new file each call"}),
           Sample("draw_from_spec", "drawing", measure(fresh_spec, repeat=ctx.repeat(5), payload=True),
                  extra={"plan_size": 9})]
    return out


@workload("ops", description="the transaction layer: apply_operations with one call per batch size")
def ops(ctx: Context) -> list[Sample]:
    plate = ctx.plate("ops")
    state = {"i": 0}

    def one_op() -> dict:
        state["i"] += 1
        op = {"op": "add_circle", "center": [10 + state["i"] % 80, 30], "diameter": "4mm",
              "layer": "HOLES", "tag": "probe", "if_tag_exists": "replace"}
        return ctx.runtime.apply_operations(plate, [op])

    batch_50 = hole_ops(50, diameter=2.5)
    batch_500 = hole_ops(500, diameter=1.0)
    strict_ops = [{"op": "add_circle", "center": [30, 30], "diameter": "10mm", "tag": "strict_a",
                   "if_tag_exists": "replace"},
                  {"op": "add_circle", "center": [70, 30], "diameter": "10mm", "tag": "strict_b",
                   "if_tag_exists": "replace"}]
    out = [
        Sample("op_single_call", "ops", measure(one_op, repeat=ctx.repeat(8), payload=True),
               extra={"note": "one op per call: lock, execute, save, independent validate"}),
        Sample("op_batch_50", "ops", measure(lambda: ctx.runtime.apply_operations(plate, batch_50),
                                             repeat=ctx.repeat(5), payload=True)),
        Sample("op_dry_run", "ops", measure(lambda: ctx.runtime.apply_operations(plate, batch_50, dry_run=True),
                                            repeat=ctx.repeat(5), payload=True),
               extra={"note": "preview only: nothing is written"}),
        Sample("op_strict", "ops", measure(lambda: ctx.runtime.apply_operations(plate, strict_ops, strict=True),
                                           repeat=ctx.repeat(4), payload=True),
               extra={"note": "validation inside the transaction; a failure saves nothing"}),
    ]
    if ctx.profile == "full":
        out.append(Sample("op_batch_500", "ops",
                          measure(lambda: ctx.runtime.apply_operations(plate, batch_500),
                                  repeat=ctx.repeat(2), payload=True)))
    return out


@workload("read", description="reads and checks an agent runs before deciding what to draw")
def read(ctx: Context) -> list[Sample]:
    plate = ctx.plate("read")
    for batch in (hole_ops(8), DIMS):
        result = ctx.runtime.apply_operations(plate, batch)
        if not result["ok"]:
            raise RuntimeError(result["error"])
    return [
        Sample("read_drawing", "read", measure(lambda: ctx.runtime.read_drawing(plate),
                                               repeat=ctx.repeat(8), payload=True)),
        Sample("inspect_entities", "read", measure(lambda: ctx.runtime.inspect_entities(plate),
                                                   repeat=ctx.repeat(6), payload=True),
               extra={"note": "default page: detail=true, limit=200"}),
        Sample("validate_drawing", "read", measure(lambda: ctx.runtime.validate_drawing(plate),
                                                   repeat=ctx.repeat(5), payload=True)),
        Sample("get_diff", "read", measure(lambda: ctx.runtime.get_diff(plate),
                                           repeat=ctx.repeat(5), payload=True)),
    ]


@workload("revision", description="history, checkpoints and undo")
def revision(ctx: Context) -> list[Sample]:
    plate = ctx.plate("revision")
    for _ in range(3):
        result = ctx.runtime.apply_operations(plate, [{"op": "add_circle", "center": [50, 30],
                                                       "diameter": "12mm", "tag": "c",
                                                       "if_tag_exists": "replace"}])
        if not result["ok"]:
            raise RuntimeError(result["error"])
    return [
        Sample("history", "revision", measure(lambda: ctx.runtime.history(plate, limit=50),
                                              repeat=ctx.repeat(8), payload=True)),
        Sample("list_checkpoints", "revision", measure(lambda: ctx.runtime.list_checkpoints(plate),
                                                       repeat=ctx.repeat(8), payload=True)),
        Sample("rollback", "revision", measure(lambda: ctx.runtime.rollback(plate),
                                               repeat=ctx.repeat(3), payload=True),
               extra={"note": "restores the latest differing checkpoint"}),
        Sample("recover", "revision", measure(lambda: ctx.runtime.recover(plate),
                                              repeat=ctx.repeat(3), payload=True)),
    ]


@workload("discovery", description="what the agent reads before its first edit: tool and op catalogs")
def discovery(ctx: Context) -> list[Sample]:
    return [
        Sample("list_operations", "discovery", measure(lambda: ctx.runtime.list_operations(),
                                                       repeat=ctx.repeat(5), payload=True),
               extra={"note": "compact index; payload is context the model must read"}),
        Sample("list_operations_one", "discovery",
               measure(lambda: ctx.runtime.list_operations(ops=["add_line"]), repeat=ctx.repeat(5), payload=True)),
        Sample("search_tools", "discovery", measure(lambda: ctx.runtime.search_tools("hole"),
                                                    repeat=ctx.repeat(8), payload=True)),
    ]


@workload("export", description="finished-drawing exports", profiles=("full",))
def export(ctx: Context) -> list[Sample]:
    plate = ctx.plate("export")  # the spec plan already dimensions itself
    counter = iter(range(10_000))
    return [
        Sample("export_svg", "export", measure(lambda: ctx.runtime.export_drawing(
            plate, ctx.path("export", f"out{next(counter)}.svg"), format="svg"),
            repeat=ctx.repeat(3), payload=True)),
        Sample("export_pdf", "export", measure(lambda: ctx.runtime.export_drawing(
            plate, ctx.path("export", f"out{next(counter)}.pdf"), format="pdf"),
            repeat=ctx.repeat(3), payload=True)),
        Sample("export_json", "export", measure(lambda: ctx.runtime.export_drawing(
            plate, ctx.path("export", f"out{next(counter)}.json"), format="json"),
            repeat=ctx.repeat(3), payload=True)),
    ]


@workload("scale", description="a drawing large enough for per-entity costs to show", profiles=("full",))
def scale(ctx: Context) -> list[Sample]:
    big = ctx.path("scale", "big.dxf")
    lines = [{"op": "add_line", "start": [i % 100, i // 100 * 2], "end": [i % 100, i // 100 * 2 + 1],
              "layer": "OUTLINE"} for i in range(2000)]
    result = ctx.runtime.apply_operations(big, lines, create={"units": "mm"})
    if not result["ok"]:
        raise RuntimeError(result["error"])
    return [
        Sample("read_drawing_2k", "scale", measure(lambda: ctx.runtime.read_drawing(big),
                                                  repeat=ctx.repeat(3), payload=True)),
        Sample("inspect_entities_2k", "scale", measure(lambda: ctx.runtime.inspect_entities(big, limit=200),
                                                       repeat=ctx.repeat(3), payload=True)),
        Sample("validate_2k", "scale", measure(lambda: ctx.runtime.validate_drawing(big),
                                               repeat=ctx.repeat(3), payload=True)),
    ]


class McpClient:
    """A stdio MCP client just for the timing: initialize, list tools, call one tool."""

    def __init__(self, root: Path) -> None:
        env = {**os.environ, "AGENTCAD_ROOT": str(root), "AGENTCAD_VIEWER": "0", "PYTHONUTF8": "1"}
        self.proc = subprocess.Popen([sys.executable, "-m", "agentcad.mcp_server"], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, env=env,
                                     cwd=str(Path(__file__).resolve().parents[1]))

    def send(self, payload: dict) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps(payload) + "\n")
        self.proc.stdin.flush()

    def timed(self, payload: dict) -> tuple[dict, float]:
        assert self.proc.stdout is not None
        t0 = time.perf_counter()
        self.send(payload)
        line = self.proc.stdout.readline()
        if not line:
            raise RuntimeError("the MCP server closed stdout before answering")
        return json.loads(line), (time.perf_counter() - t0) * 1000.0

    def notify(self, payload: dict) -> None:
        self.send(payload)

    def close(self) -> None:
        try:
            if self.proc.stdin is not None:
                self.proc.stdin.close()
        except OSError:
            pass  # the server already exited; nothing to flush
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=5)


@workload("mcp", description="the real agent path: MCP process start and JSON-RPC calls", profiles=("full",))
def mcp(ctx: Context) -> list[Sample]:
    plate = ctx.plate("mcp")
    startup_ms, list_ms, call_ms = [], [], []
    list_kb, call_kb = [], []
    for _ in range(ctx.repeat(3)):
        t0 = time.perf_counter()
        client = McpClient(ctx.root)
        try:
            client.timed({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                "protocolVersion": "2025-06-18", "capabilities": {},
                "clientInfo": {"name": "agentcad-benchmark", "version": "1"}}})
            startup_ms.append((time.perf_counter() - t0) * 1000.0)
            client.notify({"jsonrpc": "2.0", "method": "notifications/initialized"})
            listed, ms = client.timed({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
            list_ms.append(ms)
            list_kb.append(len(json.dumps(listed)) / 1024.0)
            called, ms = client.timed({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                                       "params": {"name": "read_drawing", "arguments": {"path": plate}}})
            call_ms.append(ms)
            call_kb.append(len(json.dumps(called)) / 1024.0)
        finally:
            client.close()
    return [
        Sample("mcp_startup", "mcp", Timing.of(startup_ms), extra={"note": "process start through initialize"}),
        Sample("mcp_tools_list", "mcp", Timing.of(list_ms, list_kb),
               extra={"note": "tools/list round trip; payload is the whole tool catalog"}),
        Sample("mcp_tool_call", "mcp", Timing.of(call_ms, call_kb),
               extra={"note": "tools/call read_drawing round trip"}),
    ]


def _session_once(ctx: Context, path: str, svg: str) -> tuple[float, list[dict[str, Any]]]:
    """One drafting session as an agent would run it, timed step by step; returns (total ms, steps)."""
    steps: list[dict[str, Any]] = []
    total0 = time.perf_counter()

    def step(name: str, fn: Callable[[], dict]) -> dict:
        t0 = time.perf_counter()
        result = fn()
        ms = (time.perf_counter() - t0) * 1000.0
        if not result.get("ok"):
            raise RuntimeError(f"session step {name} failed: {result.get('error')}")
        steps.append({"step": name, "ms": round(ms, 2),
                      "payload_kb": round(len(json.dumps(result, default=str)) / 1024, 2)})
        return result

    step("draw_plate", lambda: ctx.runtime.apply_operations(
        path, [*rect_ops(100, 60), *hole_ops(4, diameter=8, offset=10)],
        create={"units": "mm", "template": "mechanical"}))
    step("dimension", lambda: ctx.runtime.apply_operations(path, DIMS))
    step("note", lambda: ctx.runtime.apply_operations(
        path, [{"op": "add_text", "text": "DEBURR ALL EDGES", "insert": [0, -25], "height": 3.5, "layer": "TEXT"}]))
    step("validate", lambda: ctx.runtime.validate_drawing(path))
    step("export_svg", lambda: ctx.runtime.export_drawing(path, svg, format="svg"))
    step("revise", lambda: ctx.runtime.apply_operations(
        path, [{"op": "add_circle", "center": [50, 30], "diameter": "20mm", "tag": "bore",
                "if_tag_exists": "replace"}]))
    step("read", lambda: ctx.runtime.read_drawing(path))
    step("rollback", lambda: ctx.runtime.rollback(path))
    return (time.perf_counter() - total0) * 1000.0, steps


@workload("session", description="one realistic drafting session end to end")
def session(ctx: Context) -> list[Sample]:
    """Two sessions in quick and three in full; the fastest one is reported (single-shot work is noisy)."""
    attempts = [(_session_once(ctx, ctx.path("session", f"plate{i}.dxf"), ctx.path("session", f"plate{i}.svg")))
                for i in range(3 if ctx.profile == "full" else 2)]
    total_ms, steps = min(attempts, key=lambda pair: pair[0])
    return [Sample("drafting_session", "session", Timing.of([total_ms]), extra={"steps": steps})]


def select(profile: str, only: list[str] | None = None) -> list[Workload]:
    """Workloads for a profile, optionally restricted to groups named in ``only``."""
    chosen = [w for w in WORKLOADS if profile in w.profiles]
    if only:
        wanted = {name.strip() for name in only if name.strip()}
        unknown = sorted(wanted - {w.name for w in WORKLOADS})
        if unknown:
            raise ValueError(f"unknown workload group(s): {', '.join(unknown)}; "
                             f"available: {', '.join(w.name for w in WORKLOADS)}")
        chosen = [w for w in chosen if w.name in wanted]
    return chosen
