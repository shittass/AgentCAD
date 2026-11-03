"""Repair rate: how many calls that would have failed become the call the caller meant.

The corpus is fixed and deterministic. Each case is one call an agent plausibly writes that the
runtime would reject, plus the call it meant; the canonical call is run first so the expected
document is the runtime's own output, never a hand-written expectation.

Three outcomes per case:

``auto``
    the broken call is rewritten in place, executes, and the drawing matches the intended one —
    the round-trip disappears.
``suggested``
    the call is rejected, the error carries ``repair.suggested_call``, and replaying that call
    produces the intended drawing — the caller still spends the resend, not the guessing turns.
``miss``
    neither.

``repair_rate`` is ``(auto + suggested) / eligible``; ``zero_turn_rate`` is ``auto / eligible``.
Control cases are canonical calls that must run untouched, so ``false_repair`` counts a correct
call the layer rewrote.

The corpus is a regression gate, not a sample of what models do: a case that stops being repaired
means a table entry or a wiring path broke.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: a benchmark run fails when the fixed corpus drops below this repair rate
REPAIR_FLOOR = 0.9
#: a single rewritten correct call fails the run
FALSE_REPAIR_LIMIT = 0

SETUP = [
    {"op": "add_rectangle", "corner": [0, 0], "width": 40, "height": 20, "layer": "OUTLINE", "tag": "outline"},
    {"op": "add_circle", "center": [10, 10], "radius": 2, "layer": "HOLES", "tag": "hole.a"},
    {"op": "add_circle", "center": [30, 10], "radius": 2, "layer": "HOLES", "tag": "hole.b"},
]

#: both sides of a case start from the same blank document, created in the same transaction
CREATE = {"units": "mm", "template": "mechanical"}


@dataclass
class Case:
    name: str
    namespace: str  # op | param | enum | selector
    broken: list[dict[str, Any]]
    expected: list[dict[str, Any]]
    control: bool = False


CASES: list[Case] = [
    # --- operation-name aliases: no round-trip at all -----------------------------------------
    Case("op draw_rectangle", "op",
         [{"op": "draw_rectangle", "corner": [0, 0], "width": 20, "height": 10}],
         [{"op": "add_rectangle", "corner": [0, 0], "width": 20, "height": 10}]),
    Case("op draw_line", "op",
         [{"op": "draw_line", "start": [0, 0], "end": [10, 0]}],
         [{"op": "add_line", "start": [0, 0], "end": [10, 0]}]),
    Case("op make_layer", "op",
         [{"op": "make_layer", "name": "STEEL", "color": 3}],
         [{"op": "create_layer", "name": "STEEL", "color": 3}]),
    # --- parameter aliases ---------------------------------------------------------------------
    Case("param position", "param",
         [{"op": "add_text", "text": "PLATE", "position": [0, 0]}],
         [{"op": "add_text", "text": "PLATE", "insert": [0, 0]}]),
    Case("param r", "param",
         [{"op": "add_circle", "center": [5, 5], "r": 3}],
         [{"op": "add_circle", "center": [5, 5], "radius": 3}]),
    Case("param colour", "param",
         [{"op": "add_circle", "center": [5, 5], "radius": 3, "colour": 3}],
         [{"op": "add_circle", "center": [5, 5], "radius": 3, "color": 3}]),
    # --- enum spellings ------------------------------------------------------------------------
    Case("enum MIDDLE_CENTER", "enum",
         [{"op": "add_text", "text": "PLATE", "insert": [0, 0], "align": "MIDDLE_CENTER"}],
         [{"op": "add_text", "text": "PLATE", "insert": [0, 0], "align": "middle_center"}]),
    Case("enum SKIP", "enum",
         [{"op": "add_text", "text": "PLATE", "insert": [0, 0], "if_tag_exists": "SKIP"}],
         [{"op": "add_text", "text": "PLATE", "insert": [0, 0], "if_tag_exists": "skip"}]),
    # --- selector keys -------------------------------------------------------------------------
    Case("selector layr", "selector",
         SETUP + [{"op": "move", "target": {"layr": "HOLES"}, "delta": [0, 5]}],
         SETUP + [{"op": "move", "target": {"layer": "HOLES"}, "delta": [0, 5]}]),
    # --- rejected calls that only get a suggestion ---------------------------------------------
    Case("op add_circl", "op",
         [{"op": "add_circl", "center": [0, 0], "radius": 4}],
         [{"op": "add_circle", "center": [0, 0], "radius": 4}]),
    Case("param heigth", "param",
         [{"op": "add_text", "text": "PLATE", "insert": [0, 0], "heigth": 5}],
         [{"op": "add_text", "text": "PLATE", "insert": [0, 0], "height": 5}]),
    # --- controls: correct calls must not be rewritten ------------------------------------------
    Case("control rectangle", "op",
         [{"op": "add_rectangle", "corner": [0, 0], "width": 20, "height": 10}],
         [{"op": "add_rectangle", "corner": [0, 0], "width": 20, "height": 10}], control=True),
    Case("control text", "op",
         [{"op": "add_text", "text": "PLATE", "insert": [0, 0], "align": "middle_center", "height": 5}],
         [{"op": "add_text", "text": "PLATE", "insert": [0, 0], "align": "middle_center", "height": 5}],
         control=True),
    Case("control selector", "selector",
         SETUP + [{"op": "move", "target": {"layer": "HOLES"}, "delta": [0, 5]}],
         SETUP + [{"op": "move", "target": {"layer": "HOLES"}, "delta": [0, 5]}], control=True),
]


def _runtime(root: Path) -> Any:
    os.environ.setdefault("AGENTCAD_VIEWER", "0")
    from agentcad.policy import Policy
    from agentcad.service import Runtime
    return Runtime(Policy(root=root))


def signature(rt: Any, path: str) -> list[tuple]:
    """What the drawing holds, independent of handles and of how it was produced."""
    r = rt.inspect_entities(path, detail=True)
    return sorted((e["type"], e["layer"], e.get("tag") or "", e["fingerprint"]) for e in r["entities"])


def _replay(rt: Any, call: dict[str, Any]) -> dict[str, Any]:
    arguments = dict(call.get("arguments") or {})
    tool = call.get("tool") or "apply_operations"
    return getattr(rt, tool)(**arguments)


def measure(root: Path) -> dict[str, Any]:
    """Run the fixed corpus in ``root`` and return the per-case outcomes and the rates."""
    root.mkdir(parents=True, exist_ok=True)
    rt = _runtime(root)
    outcomes: list[dict[str, Any]] = []
    auto = suggested = miss = 0
    false_repair = 0
    controls = control_ok = 0
    for i, case in enumerate(CASES):
        expected_path = f"expect{i}.dxf"
        got_path = f"got{i}.dxf"
        # create=True makes the file in the same call: half the transactions of create+apply
        expected = rt.apply_operations(expected_path, case.expected, create=CREATE)
        if not expected.get("ok"):
            raise RuntimeError(f"benchmark case {case.name!r}: the intended call failed: {expected.get('error')}")
        expected_sig = signature(rt, expected_path)
        got = rt.apply_operations(got_path, case.broken, create=CREATE)
        record: dict[str, Any] = {"name": case.name, "namespace": case.namespace, "control": case.control}
        if case.control:
            controls += 1
            changed = bool(got.get("repaired")) or (not got.get("ok"))
            if changed:
                false_repair += 1
            else:
                control_ok += 1
            record["outcome"] = "false_repair" if changed else "control_ok"
        elif got.get("ok") and got.get("repaired"):
            if signature(rt, got_path) == expected_sig:
                auto += 1
                record["outcome"] = "auto"
            else:
                record["outcome"] = "wrong_rewrite"
        elif not got.get("ok"):
            call = (got.get("error") or {}).get("repair", {}).get("suggested_call")
            replayed = _replay(rt, call) if call else None
            if call and replayed and replayed.get("ok") and signature(rt, got_path) == expected_sig:
                suggested += 1
                record["outcome"] = "suggested"
            else:
                miss += 1
                record["outcome"] = "miss"
        else:  # accepted without a rewrite: the call was not actually broken
            miss += 1
            record["outcome"] = "accepted_unrepaired"
        outcomes.append(record)
    eligible = auto + suggested + miss
    repaired = auto + suggested
    return {
        "eligible": eligible,
        "auto": auto,
        "suggested": suggested,
        "miss": miss,
        "repair_rate": round(repaired / eligible, 3) if eligible else 0.0,
        "zero_turn_rate": round(auto / eligible, 3) if eligible else 0.0,
        "controls": controls,
        "control_ok": control_ok,
        "false_repair": false_repair,
        "cases": outcomes,
    }


def candidate_top1(operations: Any = None) -> dict[str, Any]:
    """Top-1 accuracy of the pure matcher on deterministic misspellings of the registry operations."""
    from agentcad import repair
    from agentcad.ops import REGISTRY
    names = sorted(REGISTRY)
    if operations is not None:
        names = [n for n in names if n in operations]
    variants: list[tuple[str, str]] = []
    for name in names:
        if len(name) > 5:
            variants.append((name, name[1:]))                      # dropped first character
            variants.append((name, name[:2] + name[3:]))           # dropped interior character
        if len(name) > 4:
            mid = len(name) // 2
            variants.append((name, name[:mid] + name[mid + 1] + name[mid] + name[mid + 2:]))  # transposed pair
            variants.append((name, name[:mid] + name[mid] + name[mid:]))                      # doubled character
    right = 0
    for target, variant in variants:
        if variant == target or variant in REGISTRY:
            continue
        top = repair.suggest_ops(variant, names, limit=1)
        if top and top[0]["op"] == target:
            right += 1
    judged = sum(1 for target, variant in variants if variant != target and variant not in REGISTRY)
    return {"variants": judged, "top1": round(right / judged, 3) if judged else 0.0}
