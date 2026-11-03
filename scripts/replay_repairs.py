#!/usr/bin/env python3
"""Replay recorded AgentCAD tool errors through :mod:`agentcad.repair`.

The repository ships two agent-trace corpora (``examples/benchcad/runs/*/v2c``
and ``examples/trials``).  Each trace is one task: every tool call the agent
made, with the JSON envelope the runtime returned.  This script extracts the
calls that failed, asks what the repair layer would have done with the same
arguments, and compares that against the call the agent actually made once it
got the call right.

The comparison needs a "same intent" ground truth, taken from the trace
itself: the next successful call in the same task whose tool matches.  For an
``apply_operations`` error the offending operation is located by
``error.op_index`` and the ground truth is the operation at that index of the
successful call; for a parameter error the ground truth is the key that holds
the same value in the successful operation.

Three outcomes are counted per error:

``auto``
    ``normalize_op`` / ``normalize_params`` rewrite the call in place and the
    rewrite equals the ground truth, so the engine would have accepted it.
``suggest``
    the top-1 candidate of ``suggest_ops`` / ``suggest_params`` /
    ``normalize_enum`` equals the ground truth; the caller still has to resend
    the call, but it is named correctly.
``none``
    the repair layer has nothing to offer.

Turns saved counts assistant round-trips between the failed call and the
successful one, which is how much a working in-place repair removes.

A synthetic sweep over ``REGISTRY`` measures the same primitives on
deterministic misspellings (one deleted character, one transposed pair, one
doubled character, a swapped verb), so the fuzzy matcher has a precision
number that does not depend on which errors a model happened to make.

Usage::

    python scripts/replay_repairs.py --json C:/tmp/repairs.json
    python scripts/replay_repairs.py --corpus trials --limit 20 --json out.json
"""
from __future__ import annotations

import argparse
import ast
import collections
import json
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

# The repair layer and the operation catalog are the two things under
# measurement; everything else in ``agentcad`` is deliberately not imported so
# a concurrent edit elsewhere in the package cannot change these numbers.
_IMPORT_ERROR: BaseException | None = None
for _attempt in range(3):
    try:
        from agentcad import repair
        from agentcad.ops import REGISTRY
        break
    except Exception as exc:  # pragma: no cover - only during a broken checkout
        _IMPORT_ERROR = exc
        time.sleep(0.5)
else:  # pragma: no cover
    raise SystemExit(f"cannot import agentcad.repair / agentcad.ops.REGISTRY: {_IMPORT_ERROR!r}")

SCHEMA = "agentcad.repair_replay/1"

#: trace roots per corpus name, relative to the repository root.
CORPORA: dict[str, str] = {
    "benchcad": "examples/benchcad/runs/*/v2c/*.jsonl",
    "trials": "examples/trials/**/*.jsonl",
}

#: client tools that are not AgentCAD calls; a tool name that never produced an
#: AgentCAD envelope is skipped even without this list, which is kept for clarity.
CLIENT_TOOLS = frozenset({
    "Read", "Write", "Edit", "MultiEdit", "NotebookEdit", "Bash", "BashOutput",
    "KillShell", "Glob", "Grep", "LS", "Task", "TodoWrite", "WebFetch",
    "WebSearch", "EnterPlanMode", "ExitPlanMode", "SlashCommand",
})

#: envelope error code -> repair namespace it belongs to.
ERROR_KIND = {
    "UNKNOWN_OP": "op_name",
    "UNKNOWN_OPERATION": "operation_catalog",
    "UNKNOWN_PARAM": "param",
    "INVALID_ENUM": "enum",
    "BAD_SELECTOR": "selector",
}

_UNKNOWN_PARAMS = re.compile(r"unknown parameter\(s\) \[(.*?)\]")
_INVALID_ENUM = re.compile(r"^(?P<prefix>[\w.]+): (?P<value>'.*') not in (?P<allowed>\[.*\])$")
_BAD_SELECTOR = re.compile(r"^(?P<prefix>[\w.]+): (?P<value>'.*') is not an? [\w ]+selector$")


# --- trace parsing -----------------------------------------------------------

@dataclass(slots=True)
class Call:
    """One tool call from a trace, with the envelope fields that matter here."""

    task: str
    index: int
    turn: int
    tool: str
    args: Any
    ok: bool
    code: str | None = None
    message: str | None = None
    op: str | None = None
    op_index: int | None = None
    hint: str | None = None
    envelope: bool = False


def _bare_tool(name: str) -> str:
    """Strip the MCP server prefix so both agents' traces compare equal."""
    for prefix in ("mcp__agentcad__", "agentcad."):
        if name.startswith(prefix):
            return name[len(prefix):]
    return name


def _result_text(content: Any) -> str:
    """Flatten a Claude ``tool_result`` content (string or text blocks)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                return part.get("text") or ""
    return ""


def _envelope(text: str) -> tuple[bool, dict[str, Any] | None, bool]:
    """Decode one tool result into ``(ok, error, is_agentcad_envelope)``.

    AgentCAD reports business errors inside a successful tool transport, as
    ``{"ok": false, "error": {...}}``; the MCP ``is_error`` flag stays false.
    A result that is not an envelope at all is reported as transport failure,
    which is how a client-side refusal of a bare tool name is recognised.
    """
    stripped = (text or "").strip()
    if not stripped.startswith("{"):
        return False, {"code": "TRANSPORT_ERROR", "message": stripped[:200]}, False
    try:
        obj = json.loads(stripped)
    except json.JSONDecodeError:
        return False, {"code": "UNPARSED", "message": stripped[:200]}, False
    if not isinstance(obj, dict):
        return True, None, False
    is_envelope = isinstance(obj.get("tool"), str)
    if obj.get("ok") is False:
        err = obj.get("error")
        return False, err if isinstance(err, dict) else {"code": "ERROR"}, is_envelope
    return True, None, is_envelope


def _apply_envelope(call: Call, text: str) -> Call:
    ok, err, is_envelope = _envelope(text)
    call.ok = ok
    call.envelope = is_envelope
    if err:
        call.code = err.get("code") or "ERROR"
        call.message = err.get("message")
        call.op = err.get("op")
        call.op_index = err.get("op_index")
        call.hint = err.get("hint")
    return call


def parse_claude(path: Path, task: str) -> list[Call]:
    """Parse a Claude Code session log.

    An assistant message is logged as several events that share ``message.id``
    but carry different content blocks (thinking, text, ``tool_use``), so tool
    calls are deduplicated by block id and the message id only identifies the
    round-trip.
    """
    pending: dict[str, Call] = {}
    calls: list[Call] = []
    turns: dict[str, int] = {}
    seen_blocks: set[str] = set()
    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            kind = event.get("type")
            if kind == "assistant":
                message = event.get("message") or {}
                mid = message.get("id") or event.get("uuid") or ""
                turn = turns.setdefault(mid, len(turns))
                for block in message.get("content") or []:
                    if not isinstance(block, dict) or block.get("type") != "tool_use":
                        continue
                    block_id = block.get("id") or ""
                    if block_id in seen_blocks:
                        continue
                    seen_blocks.add(block_id)
                    pending[block_id] = Call(task=task, index=len(calls), turn=turn,
                                             tool=_bare_tool(block.get("name") or ""),
                                             args=block.get("input"), ok=True)
            elif kind == "user":
                content = (event.get("message") or {}).get("content")
                for block in content if isinstance(content, list) else []:
                    if not isinstance(block, dict) or block.get("type") != "tool_result":
                        continue
                    call = pending.pop(block.get("tool_use_id") or "", None)
                    if call is None:
                        continue
                    calls.append(_apply_envelope(call, _result_text(block.get("content"))))
    return calls


def parse_codex(path: Path, task: str) -> list[Call]:
    """Parse a Codex event stream (``item.completed`` / ``mcp_tool_call``)."""
    started: dict[str, Call] = {}
    calls: list[Call] = []
    turn = -1
    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            kind = event.get("type")
            if kind == "turn.started":
                turn += 1
                continue
            item = event.get("item") or {}
            if item.get("type") != "mcp_tool_call":
                continue
            item_id = item.get("id") or ""
            if kind == "item.started" or item.get("status") == "in_progress":
                started[item_id] = Call(task=task, index=len(calls), turn=turn,
                                        tool=_bare_tool(item.get("tool") or ""),
                                        args=item.get("arguments"), ok=True)
                continue
            call = started.pop(item_id, None)
            if call is None:
                continue
            result = item.get("result") or {}
            parts = result.get("content") if isinstance(result, dict) else None
            text = "".join(p.get("text", "") for p in parts if isinstance(p, dict)) \
                if isinstance(parts, list) else ""
            if item.get("error"):
                call.ok = False
                call.code = "TRANSPORT_ERROR"
                call.message = str(item.get("error"))[:200]
                calls.append(call)
                continue
            calls.append(_apply_envelope(call, text))
    return calls


def parse_trace(path: Path, task: str) -> tuple[str, list[Call]]:
    """Detect the trace format from the first recognised event and parse it."""
    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            kind = event.get("type")
            if kind in ("assistant", "user", "system", "result"):
                return "claude", parse_claude(path, task)
            if kind in ("thread.started", "item.started", "item.completed", "turn.started",
                        "turn.completed"):
                return "codex", parse_codex(path, task)
    return "empty", []


# --- corpus selection --------------------------------------------------------

def discover(corpus: str) -> list[tuple[str, Path]]:
    """Return ``(corpus_name, path)`` for every trace in the selected corpora."""
    names = ["benchcad", "trials"] if corpus == "all" else [corpus]
    found: list[tuple[str, Path]] = []
    for name in names:
        paths = sorted(ROOT.glob(CORPORA[name]))
        found.extend((name, p) for p in paths if p.is_file() and p.suffix == ".jsonl")
    return found


def sample_evenly(items: Sequence[Any], limit: int | None) -> list[Any]:
    """Keep at most ``limit`` items at an even stride, preserving order.

    The stride spreads a small sample across arms and rounds instead of taking
    one alphabetical slice, which would compare only one agent configuration.
    """
    if limit is None or limit >= len(items):
        return list(items)
    if limit <= 0:
        return []
    step = len(items) / limit
    picked = [items[min(len(items) - 1, int(i * step))] for i in range(limit)]
    seen: set[int] = set()
    out = []
    for item in picked:
        if id(item) in seen:
            continue
        seen.add(id(item))
        out.append(item)
    return out


def task_name(path: Path) -> str:
    """Stable task id: the path below ``examples`` without the extension."""
    try:
        return path.relative_to(ROOT / "examples").with_suffix("").as_posix()
    except ValueError:
        return path.as_posix()


# --- operation schema --------------------------------------------------------

@dataclass(slots=True)
class CorpusParam:
    """Stand-in for ``registry.Param`` built from what a successful call used."""

    type: str = "any"
    enum: list[str] | None = None


@dataclass(slots=True)
class CorpusOp:
    """Stand-in for ``registry.OpDef`` for operations this checkout does not have."""

    name: str
    params: dict[str, CorpusParam] = field(default_factory=dict)


def _schema_from_corpus(observed: dict[str, dict[str, Any]]) -> dict[str, CorpusOp]:
    """Build duck-typed op definitions from observed successful argument keys.

    The BenchCAD traces were produced by a build with the 3D part operations
    that this checkout removed, so their schema is not in ``REGISTRY``.  The
    keys a successful call used are the only evidence of that schema available
    here; a parameter name that never appears cannot be suggested anyway.
    """
    out: dict[str, CorpusOp] = {}
    for op, params in observed.items():
        out[op] = CorpusOp(op, {name: CorpusParam() for name in params})
    return out


def opdefs(observed: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Registry definitions first; corpus-derived definitions fill the rest."""
    defs: dict[str, Any] = {name: REGISTRY[name] for name in REGISTRY}
    for name, corpus_op in _schema_from_corpus(observed).items():
        defs.setdefault(name, corpus_op)
    return defs


# --- ground truth ------------------------------------------------------------

def ops_of(call: Call) -> list[Any] | None:
    """The ``operations`` list of an apply_operations call, when well formed."""
    args = call.args
    if not isinstance(args, dict):
        return None
    ops = args.get("operations")
    return ops if isinstance(ops, list) else None


def find_ground_truth(calls: Sequence[Call], index: int) -> Call | None:
    """The next successful call in this task with the same tool.

    For ``apply_operations`` the successful call must also carry an
    ``operations`` list, otherwise the comparison has nothing to align.
    """
    failed = calls[index]
    wants_ops = failed.tool == "apply_operations"
    for cand in calls[index + 1:]:
        if not cand.ok or cand.tool != failed.tool:
            continue
        if wants_ops and ops_of(cand) is None:
            continue
        return cand
    return None


def align_operation(failed: Call, target: Call | None) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Return ``(failed_op, ground_truth_op)`` for the operation that errored.

    ``error.op_index`` is authoritative; when it is missing the operation is
    located by name.  A one-operation batch that the agent rewrote wholesale
    falls back to index 0 so parameter-level errors still have a comparison.
    """
    mine = ops_of(failed) or []
    theirs = ops_of(target) if target is not None else None
    if theirs is None:
        return None, None
    index = failed.op_index
    if not isinstance(index, int):
        index = next((i for i, op in enumerate(mine)
                      if isinstance(op, dict) and op.get("op") == failed.op), None)
    if isinstance(index, int) and 0 <= index < len(mine) and index < len(theirs):
        bad = mine[index] if isinstance(mine[index], dict) else None
        good = theirs[index] if isinstance(theirs[index], dict) else None
        return bad, good
    if len(mine) == 1 and theirs and isinstance(mine[0], dict) and isinstance(theirs[0], dict):
        return mine[0], theirs[0]
    return None, None


def _same_value(left: Any, right: Any) -> bool:
    """JSON-level equality, so 1 and 1.0 count as the same value."""
    if isinstance(left, bool) or isinstance(right, bool):
        return left is right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return float(left) == float(right)
    return left == right


def ground_truth_param(unknown: str, failed_op: dict[str, Any],
                       target_op: dict[str, Any] | None) -> str | None:
    """The key that carries the rejected parameter's value in the successful call.

    Value equality is the strongest evidence that the key means the same thing;
    when two keys hold it, or none does, the ground truth is unknown rather than
    guessed.
    """
    if target_op is None or unknown not in failed_op:
        return None
    value = failed_op[unknown]
    matches = [key for key, held in target_op.items()
               if key != "op" and key not in failed_op and _same_value(held, value)]
    return matches[0] if len(matches) == 1 else None


def parse_unknown_params(message: str | None) -> list[str]:
    """Names the runtime rejected, from ``unknown parameter(s) ['a', 'b']``."""
    if not message:
        return []
    match = _UNKNOWN_PARAMS.search(message)
    if not match:
        return []
    try:
        parsed = ast.literal_eval(f"[{match.group(1)}]")
    except (ValueError, SyntaxError):
        return []
    return [name for name in parsed if isinstance(name, str)]


def parse_enum_error(message: str | None) -> tuple[str, str, list[str]] | None:
    """Split ``op.param: 'VALUE' not in [...]`` into its three parts."""
    if not message:
        return None
    match = _INVALID_ENUM.match(message.strip())
    if not match:
        return None
    try:
        value = ast.literal_eval(match.group("value"))
        allowed = ast.literal_eval(match.group("allowed"))
    except (ValueError, SyntaxError):
        return None
    if not isinstance(value, str) or not isinstance(allowed, list):
        return None
    return match.group("prefix").rsplit(".", 1)[-1], value, [a for a in allowed if isinstance(a, str)]


# --- replay ------------------------------------------------------------------

@dataclass(slots=True)
class Outcome:
    """What the repair layer offered for one error and whether it was right."""

    kind: str
    produced: bool = False
    auto: bool = False
    correct: bool = False
    turns: int | None = None
    detail: dict[str, Any] = field(default_factory=dict)


def _empty_bucket() -> dict[str, Any]:
    return {"errors": 0, "with_ground_truth": 0, "auto_rewritten": 0, "auto_correct": 0,
            "suggested": 0, "suggest_correct": 0, "produced": 0, "converted": 0,
            "wrong_auto": 0}


def _bucket_ratios(bucket: dict[str, Any]) -> dict[str, Any]:
    judged = bucket["with_ground_truth"]
    bucket["coverage"] = round(bucket["converted"] / judged, 3) if judged else None
    bucket["precision_at_1"] = (round(bucket["converted"] / bucket["produced"], 3)
                                if bucket["produced"] else None)
    return bucket


def evaluate_op_name(call: Call, target: Call | None, bad_op: dict[str, Any] | None,
                     good_op: dict[str, Any] | None, catalogue: Sequence[str]) -> Outcome:
    """Would a rejected operation name be turned into the operation the agent used?"""
    bad = call.op or (bad_op or {}).get("op")
    good = (good_op or {}).get("op")
    outcome = Outcome("op_name", detail={"from": bad, "to": good})
    if not isinstance(bad, str):
        return outcome
    canonical = repair.normalize_op(bad)
    if canonical is not None:
        outcome.produced = True
        outcome.auto = True
        outcome.detail["auto_to"] = canonical[0]
    suggestions = repair.suggest_ops(bad, catalogue, 3)
    outcome.detail["suggestions"] = [s["op"] for s in suggestions]
    if suggestions:
        outcome.produced = True
        outcome.detail["top1"] = suggestions[0]["op"]
    registry_only = repair.suggest_ops(bad, list(REGISTRY), 1)
    outcome.detail["registry_top1"] = registry_only[0]["op"] if registry_only else None
    if good is None:
        return outcome
    outcome.turns = target.turn - call.turn if target is not None else None
    if outcome.auto and canonical is not None and canonical[0] == good:
        outcome.correct = True
        outcome.detail["result"] = "auto"
    elif suggestions and suggestions[0]["op"] == good:
        outcome.correct = True
        outcome.detail["result"] = "suggest"
    else:
        outcome.detail["result"] = "miss"
    return outcome


def evaluate_param(call: Call, target: Call | None, bad_op: dict[str, Any] | None,
                   good_op: dict[str, Any] | None, schema: dict[str, Any]) -> Outcome:
    """Would a rejected parameter name be renamed to the key the agent used?"""
    outcome = Outcome("param")
    if bad_op is None:
        return outcome
    # The index only identifies the same operation when the retry kept it; an
    # agent that dropped or inserted operations would otherwise be compared
    # against an unrelated neighbour.
    if good_op is not None and good_op.get("op") != bad_op.get("op"):
        good_op = None
    unknown = parse_unknown_params(call.message)
    if not unknown:
        opdef = schema.get(bad_op.get("op") or "")
        known = set(getattr(opdef, "params", {}) or {})
        unknown = [key for key in bad_op if key != "op" and key not in known][:3]
    if not unknown:
        return outcome
    opdef = schema.get(bad_op.get("op") or "")
    if opdef is None:
        return outcome
    wanted = {name: ground_truth_param(name, bad_op, good_op) for name in unknown}
    outcome.detail.update({"op": bad_op.get("op"), "unknown": unknown, "wanted": wanted})
    raw = {key: value for key, value in bad_op.items() if key != "op"}
    _params, repairs = repair.normalize_params(opdef, raw)
    renamed = {rep["from"]: rep["to"] for rep in repairs if rep.get("rule") == "param.alias"}
    folded = {rep["from"]: rep["to"] for rep in repairs if rep.get("rule") == "param.enum"}
    outcome.detail["auto_renames"] = renamed
    suggestions = repair.suggest_params(opdef, unknown, 3)
    outcome.detail["suggestions"] = [(s.get("param"), s.get("for")) for s in suggestions]
    if repairs:
        outcome.produced = True
        outcome.auto = True
    if suggestions:
        outcome.produced = True
    if target is None or any(wanted[name] is None for name in unknown):
        return outcome
    outcome.turns = target.turn - call.turn
    auto_ok = all(renamed.get(name) == wanted[name] or folded.get(name) == wanted[name]
                  for name in unknown)
    top = {s.get("for"): s.get("param") for s in suggestions}
    suggest_ok = bool(unknown) and all(top.get(name) == wanted[name] for name in unknown)
    if auto_ok:
        outcome.correct = True
        outcome.detail["result"] = "auto"
    elif suggest_ok:
        outcome.correct = True
        outcome.detail["result"] = "suggest"
    else:
        outcome.detail["result"] = "miss"
    return outcome


def evaluate_enum(call: Call, target: Call | None, bad_op: dict[str, Any] | None,
                  good_op: dict[str, Any] | None) -> Outcome:
    """Would a near-miss enum value be folded onto the accepted spelling?"""
    outcome = Outcome("enum")
    parsed = parse_enum_error(call.message)
    if parsed is None or bad_op is None:
        return outcome
    if good_op is not None and good_op.get("op") != bad_op.get("op"):
        good_op = None
    param, value, allowed = parsed
    canonical = repair.normalize_enum(value, allowed)
    outcome.detail.update({"param": param, "from": value, "to": canonical, "allowed": allowed})
    outcome.produced = outcome.auto = canonical is not None
    if target is None or good_op is None or param not in good_op:
        return outcome
    outcome.turns = target.turn - call.turn
    outcome.correct = canonical is not None and _same_value(canonical, good_op[param])
    outcome.detail["result"] = "auto" if outcome.correct else "miss"
    return outcome


def evaluate_selector(call: Call, target: Call | None, bad_op: dict[str, Any] | None,
                      good_op: dict[str, Any] | None) -> Outcome:
    """Would a misspelled filter key be renamed?

    A selector *value* the runtime rejects (``edges: 'bottom'``) is not this
    layer's namespace; only a filter dict with an unknown key is.
    """
    outcome = Outcome("selector")
    parsed = _BAD_SELECTOR.match((call.message or "").strip())
    outcome.detail["value_error"] = bool(parsed)
    if bad_op is None:
        return outcome
    rewritten: dict[str, str] = {}
    for key, value in bad_op.items():
        if isinstance(value, dict):
            _fixed, repairs = repair.normalize_filter(value)
            rewritten.update({rep["from"]: rep["to"] for rep in repairs})
    outcome.detail["key_rewrites"] = rewritten
    if rewritten:
        outcome.produced = outcome.auto = True
        if good_op is not None:
            outcome.correct = all(to in good_op for to in rewritten.values())
    return outcome


def evaluate_trace(calls: Sequence[Call], catalogue: Sequence[str],
                   schema: dict[str, Any]) -> list[tuple[Call, Call | None, Outcome]]:
    """Run the repair layer over every failed call in one task."""
    results: list[tuple[Call, Call | None, Outcome]] = []
    for index, call in enumerate(calls):
        if call.ok:
            continue
        kind = ERROR_KIND.get(call.code or "")
        if kind not in ("op_name", "param", "enum", "selector"):
            continue
        target = find_ground_truth(calls, index)
        bad_op, good_op = align_operation(call, target)
        if kind == "op_name":
            outcome = evaluate_op_name(call, target, bad_op, good_op, catalogue)
        elif kind == "param":
            outcome = evaluate_param(call, target, bad_op, good_op, schema)
        elif kind == "enum":
            outcome = evaluate_enum(call, target, bad_op, good_op)
        else:
            outcome = evaluate_selector(call, target, bad_op, good_op)
        results.append((call, target, outcome))
    return results


# --- synthetic sweep ---------------------------------------------------------

def typo_variants(name: str) -> Iterator[tuple[str, str]]:
    """Deterministic misspellings of ``name`` as ``(variant, bucket)`` pairs."""
    if len(name) >= 5:
        for i in range(len(name)):
            yield name[:i] + name[i + 1:], "delete"
    for i in range(len(name) - 1):
        if name[i] != name[i + 1]:
            yield name[:i] + name[i + 1] + name[i] + name[i + 2:], "transpose"
    for i in range(1, len(name) - 1):
        yield name[:i] + name[i] + name[i:], "double"
    verb, _, rest = name.partition("_")
    if rest:
        for other in ("add", "draw", "create", "make", "set", "edit"):
            if other != verb:
                yield f"{other}_{rest}", "verb"


def sweep_op_names() -> dict[str, Any]:
    """Top-1 accuracy of the op-name matcher over misspellings of every operation."""
    buckets: dict[str, dict[str, int]] = {}
    seen: set[tuple[str, str]] = set()
    for intended in sorted(REGISTRY):
        for variant, bucket in typo_variants(intended):
            if variant == intended or variant in REGISTRY or variant in repair.OP_ALIASES:
                continue
            if (variant, intended) in seen:
                continue
            seen.add((variant, intended))
            alias = repair.normalize_op(variant)
            top = repair.suggest_ops(variant, REGISTRY, 1)
            slot = buckets.setdefault(bucket, {"variants": 0, "alias_hits": 0, "top1_hits": 0})
            slot["variants"] += 1
            alias_hit = alias is not None and alias[0] == intended
            if alias_hit:
                slot["alias_hits"] += 1
            if alias_hit or (top and top[0]["op"] == intended):
                slot["top1_hits"] += 1
    totals = {key: sum(b[key] for b in buckets.values())
              for key in ("variants", "alias_hits", "top1_hits")}
    totals["hit_rate"] = round(totals["top1_hits"] / totals["variants"], 3) if totals["variants"] else None
    totals["buckets"] = buckets
    return totals


def sweep_params() -> dict[str, Any]:
    """Top-1 accuracy of the parameter matcher over misspellings of required names."""
    stat = {"variants": 0, "top1_hits": 0, "ops": 0}
    for name in sorted(REGISTRY):
        opdef = REGISTRY[name]
        known = list(opdef.params)
        stat["ops"] += 1
        for param, spec in opdef.params.items():
            if not getattr(spec, "required", False) or len(param) < 5:
                continue
            seen: set[str] = set()
            for variant, _bucket in typo_variants(param):
                if variant in known or variant in seen:
                    continue
                seen.add(variant)
                top = repair.suggest_params(opdef, [variant], 1)
                stat["variants"] += 1
                if top and top[0].get("param") == param:
                    stat["top1_hits"] += 1
    stat["hit_rate"] = round(stat["top1_hits"] / stat["variants"], 3) if stat["variants"] else None
    return stat


# --- report ------------------------------------------------------------------

def _add(bucket: dict[str, Any], outcome: Outcome) -> None:
    bucket["errors"] += 1
    if outcome.turns is not None:
        bucket["with_ground_truth"] += 1
    bucket["auto_rewritten"] += int(outcome.auto)
    bucket["suggested"] += int(outcome.produced and not outcome.auto)
    bucket["produced"] += int(outcome.produced)
    if outcome.auto and not outcome.correct and outcome.detail.get("result") == "miss":
        bucket["wrong_auto"] += 1
    bucket["auto_correct"] += int(outcome.auto and outcome.correct)
    bucket["suggest_correct"] += int(outcome.correct and not outcome.auto)
    bucket["converted"] += int(outcome.correct)


def build_report(tasks: Sequence[tuple[str, str, list[Call]]], meta: dict[str, Any],
                 tool_catalogue: Iterable[str]) -> dict[str, Any]:
    """Evaluate every extracted error and assemble the JSON document."""
    observed: dict[str, dict[str, Any]] = {}
    for _name, _fmt, calls in tasks:
        for call in calls:
            if not call.ok:
                continue
            for operation in ops_of(call) or []:
                if isinstance(operation, dict) and isinstance(operation.get("op"), str):
                    observed.setdefault(operation["op"], {})
                    observed[operation["op"]].update(
                        {key: None for key in operation if key != "op"})
    schema = opdefs(observed)
    catalogue = sorted(set(REGISTRY) | set(observed))
    known_tools = set(tool_catalogue)

    per_corpus: dict[str, dict[str, Any]] = collections.defaultdict(
        lambda: {"tasks": 0, "calls": 0, "agentcad_calls": 0, "errors": 0,
                 "addressable": 0, "converted": 0})
    tools = collections.Counter()
    codes = collections.Counter()
    buckets: dict[str, dict[str, Any]] = {"op_name": _empty_bucket(), "param": _empty_bucket(),
                                          "enum": _empty_bucket(), "selector": _empty_bucket()}
    turns_all: list[int] = []
    saved = ceiling = 0
    samples: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    op_pairs: collections.Counter = collections.Counter()
    param_pairs: collections.Counter = collections.Counter()
    distribution: dict[str, collections.Counter] = {"op_name": collections.Counter(),
                                                    "param": collections.Counter()}
    registry_hits = 0
    cap = max(0, int(meta.get("max_samples", 12)) // 4)

    for name, _fmt, calls in tasks:
        per_corpus[name]["tasks"] += 1
        per_corpus[name]["calls"] += len(calls)
        agentcad = [call for call in calls if call.tool in known_tools]
        per_corpus[name]["agentcad_calls"] += len(agentcad)
        for call in agentcad:
            if not call.ok:
                per_corpus[name]["errors"] += 1
                codes[call.code or "ERROR"] += 1
                tools[call.tool] += 1
        for call, _target, outcome in evaluate_trace(agentcad, catalogue, schema):
            _add(buckets[outcome.kind], outcome)
            per_corpus[name]["addressable"] += 1
            per_corpus[name]["converted"] += int(outcome.correct)
            if outcome.turns is not None:
                turns_all.append(max(0, outcome.turns))
                ceiling += max(0, outcome.turns)
                if outcome.correct:
                    saved += max(0, outcome.turns)
            if outcome.kind == "op_name":
                op_pairs[(outcome.detail.get("from"), outcome.detail.get("to"))] += 1
                distribution["op_name"][str(outcome.detail.get("from"))] += 1
                if (outcome.detail.get("registry_top1") is not None
                        and outcome.detail.get("registry_top1") == outcome.detail.get("to")):
                    registry_hits += 1
            elif outcome.kind == "param":
                wanted = outcome.detail.get("wanted") or {}
                for unknown in outcome.detail.get("unknown") or []:
                    param_pairs[(outcome.detail.get("op"), unknown, wanted.get(unknown))] += 1
                    distribution["param"][f"{outcome.detail.get('op')}.{unknown}"] += 1
            rows = samples[outcome.kind]
            if len(rows) < cap:
                rows.append({
                    "class": outcome.kind, "task": call.task, "index": call.index,
                    "tool": call.tool, "code": call.code, "op": call.op,
                    "op_index": call.op_index, "message": call.message, "hint": call.hint,
                    "outcome": outcome.detail.get("result", "unknown"),
                    "turns": outcome.turns, "detail": outcome.detail,
                })

    classes = ("op_name", "param", "enum", "selector")
    addressable = sum(buckets[k]["errors"] for k in classes)
    judged = sum(buckets[k]["with_ground_truth"] for k in classes)
    converted = sum(buckets[k]["converted"] for k in classes)
    produced = sum(buckets[k]["produced"] for k in classes)
    for bucket in buckets.values():
        _bucket_ratios(bucket)
    median = sorted(turns_all)[len(turns_all) // 2] if turns_all else None
    mean = round(sum(turns_all) / len(turns_all), 2) if turns_all else None

    replay = {
        "addressable": addressable,
        "with_ground_truth": judged,
        "converted": converted,
        "auto_rewritten": sum(b["auto_rewritten"] for b in buckets.values()),
        "produced": produced,
        "coverage_addressable": round(converted / addressable, 3) if addressable else None,
        "coverage_judged": round(converted / judged, 3) if judged else None,
        "precision_at_1": round(converted / produced, 3) if produced else None,
        "by_class": buckets,
        "registry_only_op_hits": registry_hits,
        "distribution": {name: dict(counter.most_common(12))
                         for name, counter in distribution.items()},
        "gaps": {
            "op_name": [{"from": key[0], "to": key[1], "count": n}
                        for key, n in op_pairs.most_common(12)],
            "param": [{"op": key[0], "from": key[1], "to": key[2], "count": n}
                      for key, n in param_pairs.most_common(12)],
        },
    }

    return {
        "schema": SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "meta": meta,
        "corpora": {name: dict(stats) for name, stats in per_corpus.items()},
        "calls": {
            "agentcad": sum(per_corpus[name]["agentcad_calls"] for name in per_corpus),
            "errors": sum(codes.values()),
            "by_code": dict(codes.most_common()),
            "by_tool": dict(tools.most_common()),
        },
        "catalogue": {"tools": sorted(known_tools), "operations": len(catalogue),
                      "registry_operations": len(REGISTRY),
                      "corpus_only_operations": sorted(set(observed) - set(REGISTRY))},
        "replay": replay,
        "turns": {
            "judged": len(turns_all),
            "median": median,
            "mean": mean,
            "max": max(turns_all) if turns_all else None,
            "saved_measured": saved,
            "saved_ceiling": ceiling,
        },
        "synthetic": {"op_names": sweep_op_names(), "params": sweep_params()},
        "samples": [row for kind in classes for row in samples.get(kind, [])],
        "limitations": LIMITATIONS,
    }


#: Filled while parsing: tool names that ever produced an AgentCAD envelope.
_TOOL_CATALOGUE: set[str] = set()

LIMITATIONS = [
    "This checkout has no 3D part runtime: the BenchCAD corpora were produced by a build "
    "with part operations (add_sketch, pad, pocket, modify_feature, ...) that REGISTRY here "
    "does not contain, so those operation errors can only be judged by the generic rules "
    "(op.alias and difflib), not against the build's real schema.",
    "Ground truth is the agent's next successful call in the same task, which is the agent's "
    "own interpretation of the fix; a different but also-correct call is scored as a miss.",
    "Errors the client rejects before the runtime sees them (a bare tool name is not an MCP "
    "tool) never reach the repair layer and are counted as out of scope.",
    "Turn savings count assistant messages between the failed call and the successful one; "
    "they are an upper bound on latency, not a cost model.",
]


def print_table(report: dict[str, Any]) -> None:
    """Print the short summary tables to stdout."""
    meta = report["meta"]
    print(f"corpus={meta['corpus']} files={meta['files']} tasks={meta['tasks']} "
          f"limit={meta['limit']} seconds={meta['elapsed_seconds']}")
    print()
    header = (f"{'corpus':10s} {'tasks':>6s} {'calls':>7s} {'agentcad':>9s} {'errors':>7s} "
              f"{'addr':>6s} {'conv':>5s}")
    print(header)
    print("-" * len(header))
    for name, stats in report["corpora"].items():
        print(f"{name:10s} {stats['tasks']:6d} {stats['calls']:7d} "
              f"{stats['agentcad_calls']:9d} {stats['errors']:7d} "
              f"{stats['addressable']:6d} {stats['converted']:5d}")
    print()
    print(f"{'class':10s} {'errors':>7s} {'judged':>7s} {'auto':>5s} {'suggest':>8s} "
          f"{'right':>6s} {'prec@1':>7s}")
    print("-" * 60)
    for name, bucket in report["replay"]["by_class"].items():
        precision = bucket["precision_at_1"]
        print(f"{name:10s} {bucket['errors']:7d} {bucket['with_ground_truth']:7d} "
              f"{bucket['auto_rewritten']:5d} {bucket['suggested']:8d} "
              f"{bucket['converted']:6d} "
              f"{(f'{precision:.2f}' if precision is not None else '-'):>7s}")
    replay = report["replay"]
    coverage = replay["coverage_judged"]
    precision = replay["precision_at_1"]
    print("-" * 60)
    print(f"{'total':10s} {replay['addressable']:7d} {replay['with_ground_truth']:7d} "
          f"{replay['auto_rewritten']:5d} "
          f"{replay['produced'] - replay['auto_rewritten']:8d} {replay['converted']:6d} "
          f"{(f'{precision:.2f}' if precision is not None else '-'):>7s}")
    print()
    turns = report["turns"]
    print(f"turns: judged={turns['judged']} median={turns['median']} mean={turns['mean']} "
          f"saved_measured={turns['saved_measured']} ceiling={turns['saved_ceiling']}")
    print(f"coverage: {replay['converted']}/{replay['with_ground_truth']} judged "
          f"({f'{coverage:.1%}' if coverage is not None else 'n/a'}), "
          f"{replay['converted']}/{replay['addressable']} addressable")
    synthetic = report["synthetic"]
    print(f"synthetic: op variants={synthetic['op_names']['variants']} "
          f"hit={synthetic['op_names']['hit_rate']} | "
          f"param variants={synthetic['params']['variants']} "
          f"hit={synthetic['params']['hit_rate']}")
    print(f"errors by code: " + ", ".join(
        f"{code}={count}" for code, count in list(report["calls"]["by_code"].items())[:8]))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", metavar="PATH", help="write the JSON report to PATH")
    parser.add_argument("--limit", type=int, default=None,
                        help="scan at most N trace files (even stride across the corpus)")
    parser.add_argument("--corpus", choices=("all", "benchcad", "trials"), default="all",
                        help="which trace corpora to read (default: all)")
    parser.add_argument("--samples", type=int, default=12,
                        help="how many example errors to embed in the JSON (default: 12)")
    parser.add_argument("--quiet", action="store_true", help="do not print the summary tables")
    args = parser.parse_args(argv)
    if args.limit is not None and args.limit < 0:
        parser.error("--limit must be >= 0")

    started = time.perf_counter()
    selected = sample_evenly(discover(args.corpus), args.limit)
    tasks: list[tuple[str, str, list[Call]]] = []
    formats: collections.Counter = collections.Counter()
    tool_catalogue: set[str] = set()
    for corpus_name, path in selected:
        fmt, calls = parse_trace(path, task_name(path))
        formats[fmt] += 1
        if not calls:
            continue
        for call in calls:
            if call.ok and call.envelope:
                tool_catalogue.add(call.tool)
        tasks.append((corpus_name, fmt, calls))

    meta = {
        "corpus": args.corpus,
        "limit": args.limit,
        "tasks": len(tasks),
        "files": len(selected),
        "formats": dict(formats),
        "samples_requested": args.samples,
        "max_samples": args.samples,
        "elapsed_seconds": None,
        "repair_module": str(Path(repair.__file__).relative_to(ROOT)) if repair.__file__ else None,
        "registry_ops": len(REGISTRY),
    }
    report = build_report(tasks, meta, tool_catalogue)
    meta["elapsed_seconds"] = round(time.perf_counter() - started, 2)
    report["meta"]["elapsed_seconds"] = meta["elapsed_seconds"]

    if not args.quiet:
        print_table(report)
    if args.json:
        target = Path(args.json)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
        if not args.quiet:
            print(f"wrote {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
