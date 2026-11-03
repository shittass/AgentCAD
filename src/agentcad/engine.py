"""Transactional operation engine.

``execute`` runs an operation batch against an in-memory document:

1. every op is validated (known op, policy allow-list, typed params, units)
   before anything is touched;
2. ops run in order; a failure aborts the whole batch (the caller discards the
   in-memory document, so the file on disk is never partially modified);
3. results carry created / modified / deleted entity ids per op;
4. a name, parameter or enum value that only differs by a known alias is
   rewritten to the registered spelling and reported under ``repaired``
   (see :mod:`agentcad.repair`).

``reconcile=True`` is used for spec-driven revisions: an op whose tag already
exists updates the existing entity *in place* (same handle) instead of adding
a new one, unchanged entities are left untouched, and tags that belonged to
the previous plan but not to the new one are removed.
"""
from __future__ import annotations

import difflib
import traceback
from typing import Any

from ezdxf.document import Drawing

from . import progress, repair
from .errors import AgentCADError, input_error, policy_error
from .model import ACTIVE_LAYOUT, entity_to_dict, get_tag, is_model, set_tag
from .ops import REGISTRY, Ctx, coerce
from .policy import Policy


def prevalidate(ops: list[Any], unit: str | None,
                policy: Policy) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Validate a whole batch before any of it runs.

    Returns the coerced parameters per operation and the repairs applied to get
    there. Each repair is also passed to :func:`agentcad.repair.note`, so a batch
    validated inside a running :func:`execute` reports them with the outer batch.
    """
    if not isinstance(ops, list):
        raise input_error("INVALID_PLAN", "operations must be a list")
    if len(ops) > policy.max_ops_per_batch:
        raise policy_error("BUDGET_EXCEEDED", f"{len(ops)} operations exceed the batch limit {policy.max_ops_per_batch}")
    out: list[dict[str, Any]] = []
    repairs: list[dict[str, Any]] = []
    for i, raw in enumerate(ops):
        if not isinstance(raw, dict) or not isinstance(raw.get("op"), str):
            raise input_error("INVALID_OP", f"operations[{i}] must be an object with an 'op' field", op_index=i)
        name, rec = _resolve_op(raw["op"])
        if rec is not None:
            _record_repair(repairs, rec)
        if name not in REGISTRY:
            raise _unknown_op(ops, i, raw["op"])
        policy.check_op_allowed(name, i)
        opdef = REGISTRY[name]
        params, renamed = repair.normalize_params(opdef, raw)
        for applied in renamed:
            _record_repair(repairs, applied)
        try:
            out.append(coerce(opdef, params, unit, i))
        except AgentCADError as exc:
            _explain_param(exc, opdef, params, ops, i)
            raise
    return out, repairs


def _resolve_op(name: str) -> tuple[str, dict[str, Any] | None]:
    """Registered name for ``name`` and, when it is an alias, the repair that renamed it."""
    if name in REGISTRY:
        return name, None
    hit = repair.normalize_op(name)
    return hit if hit is not None else (name, None)


def canonical_op(name: str) -> str:
    """The registered spelling for ``name``, leaving an unknown name untouched.

    Callers that dispatch on the caller's spelling without going through
    ``prevalidate`` (the engineering record helpers) use this so an alias cannot
    pass validation and then miss ``REGISTRY``.
    """
    return _resolve_op(name)[0]


def _record_repair(repairs: list[dict[str, Any]], rec: dict[str, Any]) -> None:
    repairs.append(rec)
    repair.note(rec)


def _corrected(ops: list[Any], index: int, *, op_name: str | None = None,
               renames: dict[str, str] | None = None,
               values: dict[str, Any] | None = None) -> list[Any]:
    """The same batch with one operation rewritten, ready for the caller to resend."""
    out: list[Any] = []
    for j, raw in enumerate(ops):
        item = dict(raw) if isinstance(raw, dict) else raw
        if j == index and isinstance(item, dict):
            if op_name is not None:
                item["op"] = op_name
            for old, new in (renames or {}).items():
                if old in item and new not in item:
                    item[new] = item.pop(old)
            for key, value in (values or {}).items():
                if key in item:
                    item[key] = value
        out.append(item)
    return out


def _unknown_op(ops: list[Any], index: int, name: str) -> AgentCADError:
    """``UNKNOWN_OP`` carrying ranked candidates and the batch with the best one applied."""
    candidates = repair.suggest_ops(name, REGISTRY)
    close = [c["op"] for c in candidates]
    details: dict[str, Any] = {}
    if candidates:
        details["repair"] = {"kind": "suggest", "rule": "op.unknown",
                             "confidence": candidates[0]["confidence"],
                             "candidates": candidates,
                             "suggested_operations": _corrected(ops, index, op_name=candidates[0]["op"])}
    return input_error("UNKNOWN_OP", f"Unknown operation {name!r}", op_index=index, op=name,
                       hint=f"Did you mean {close}?" if close else "Use list_operations for the catalog",
                       details=details)


def _unknown_keys(opdef, params: dict[str, Any]) -> list[str]:
    """Parameters ``coerce`` rejects for this operation (its own UNKNOWN_PARAM rule)."""
    known = opdef.all_params()
    return [k for k in params if k not in known and k not in ("op", "comment")]


def _bad_enums(opdef, params: dict[str, Any]) -> list[tuple[str, Any, list[str]]]:
    """Enum parameters whose given value is not allowed (its own INVALID_ENUM rule)."""
    bad: list[tuple[str, Any, list[str]]] = []
    for name, p in opdef.all_params().items():
        allowed = list(p.enum or [])
        if p.type == "enum" and name in params and params[name] is not None and params[name] not in allowed:
            bad.append((name, params[name], allowed))
    return bad


def _explain_param(exc: AgentCADError, opdef, params: dict[str, Any], ops: list[Any], index: int) -> None:
    """Attach a ready-to-send corrected batch to a parameter ``coerce`` rejected."""
    if exc.op != opdef.name or "repair" in exc.details:
        return
    if exc.code == "UNKNOWN_PARAM":
        candidates = repair.suggest_params(opdef, _unknown_keys(opdef, params))
        if not candidates:
            return
        renames: dict[str, str] = {}
        for cand in candidates:
            renames.setdefault(cand["for"], cand["param"])
        rep: dict[str, Any] = {"kind": "suggest", "rule": "param.unknown",
                               "confidence": candidates[0]["confidence"], "candidates": candidates}
        if renames:
            rep["suggested_operations"] = _corrected(ops, index, renames=renames)
        exc.details["repair"] = rep
    elif exc.code == "INVALID_ENUM":
        candidates, values, best = [], {}, 0.0
        for name, value, allowed in _bad_enums(opdef, params):
            close = difflib.get_close_matches(str(value), allowed, n=1, cutoff=0.6)
            ratio = round(difflib.SequenceMatcher(None, str(value), close[0]).ratio(), 3) if close else 0.0
            best = max(best, ratio)
            candidates.append({"param": name, "value": value, "allowed": allowed, "suggestions": close})
            if close:
                values[name] = close[0]
        if not candidates:
            return
        rep = {"kind": "suggest", "rule": "param.enum", "confidence": best, "candidates": candidates}
        if values:
            rep["suggested_operations"] = _corrected(ops, index, values=values)
        exc.details["repair"] = rep


def execute(doc: Drawing, ops: list[dict[str, Any]], unit: str | None, policy: Policy, *,
            reconcile: bool = False, previous_tags: set[str] | None = None) -> dict[str, Any]:
    with repair.collect() as bag:
        res = _run_batch(doc, ops, unit, policy, reconcile=reconcile, previous_tags=previous_tags)
        res["repaired"] = list(bag.items)
        return res


def _run_batch(doc: Drawing, ops: list[dict[str, Any]], unit: str | None, policy: Policy, *,
               reconcile: bool = False, previous_tags: set[str] | None = None) -> dict[str, Any]:
    params_list, _ = prevalidate(ops, unit, policy)  # prevalidate notes its own repairs
    ctx = Ctx(doc, unit, policy)
    results = []
    produced_tags: set[str] = set()
    unchanged: list[str] = []
    for i, (raw, params) in enumerate(zip(ops, params_list)):
        name = _resolve_op(raw["op"])[0]  # an aliased op runs, and reports, as its registered name
        opdef = REGISTRY[name]
        ctx.op_index = i
        progress.step(i, len(ops), raw)
        params = dict(params)
        layout = params.pop("layout", None)
        token = None
        try:
            ctx.use_layout(layout)
            token = ACTIVE_LAYOUT.set(None if is_model(layout) else layout)
            tag = params.get("tag") if opdef.common_draw else None
            if reconcile and tag and ctx.tags().get(tag):
                res = _reconcile_op(ctx, opdef, params, tag, unchanged)
            else:
                res = opdef.fn(ctx, **params)
            if tag:
                produced_tags.add(tag)
        except AgentCADError as exc:
            exc.op_index = i
            exc.op = name
            exc.partial_changes = False
            raise
        except Exception as exc:  # library failure: report as internal, keep traceback for the journal
            raise AgentCADError("INTERNAL_ERROR", f"{name} failed: {type(exc).__name__}: {exc}", category="internal",
                                op=name, op_index=i, details={"traceback": traceback.format_exc(limit=6)})
        finally:
            if token is not None:
                ACTIVE_LAYOUT.reset(token)
            ctx.use_layout(None)
        ctx.refs[i] = list(res.get("created") or res.get("modified") or res.get("existing") or [])
        results.append({"index": i, "op": name, **res})
    if reconcile and previous_tags:
        stale = sorted(previous_tags - produced_tags)
        for t in stale:
            for e in list(ctx.tags().get(t, [])):
                ctx.removed(e)
        if stale:
            results.append({"index": len(results), "op": "reconcile_remove_stale", "deleted_tags": stale})
    n = len(doc.modelspace())
    if n > policy.max_entities:
        raise policy_error("BUDGET_EXCEEDED", f"Drawing would contain {n} entities (limit {policy.max_entities})")
    return {"results": results, "added": ctx.added, "modified": ctx.modified, "deleted": ctx.deleted,
            "unchanged": unchanged, "tags": sorted(produced_tags)}


def _comparable(e) -> dict[str, Any]:
    d = entity_to_dict(e)
    d.pop("id", None)
    return d


def _reconcile_op(ctx: Ctx, opdef, params: dict[str, Any], tag: str, unchanged: list[str]) -> dict[str, Any]:
    old = ctx.tags()[tag][0]
    p = dict(params)
    p["tag"] = None
    res = opdef.fn(ctx, **p)
    created = res.get("created") or []
    if len(created) != 1:
        # multi-entity ops (e.g. leader + text): replace wholesale
        for e in list(ctx.tags().get(tag, [])):
            ctx.removed(e)
        if created:
            set_tag(ctx.doc, ctx.doc.entitydb[created[0]], tag)
        ctx._tags = None
        return res
    new = ctx.doc.entitydb[created[0]]
    if old.dxftype() != new.dxftype():
        ctx.removed(old)
        set_tag(ctx.doc, new, tag)
        ctx._tags = None
        return {"created": created, "replaced": old.dxf.handle}
    a, b = _comparable(old), _comparable(new)
    b["tag"] = tag
    if a == b:
        _discard(ctx, new)
        unchanged.append(old.dxf.handle)
        return {"unchanged": [old.dxf.handle]}
    update_in_place(ctx, old, new)
    _discard(ctx, new)
    ctx.touched(old)
    return {"modified": [old.dxf.handle]}


def _discard(ctx: Ctx, e) -> None:
    blk = e.dxf.get("geometry") if e.dxftype() == "DIMENSION" else None
    h = e.dxf.handle
    if h in ctx.added:
        ctx.added.remove(h)
    ctx.msp.delete_entity(e)
    if blk and blk in ctx.doc.blocks:
        ctx.doc.blocks.delete_block(blk, safe=False)


COPY_ATTRS = {
    "LINE": ["start", "end"],
    "CIRCLE": ["center", "radius"],
    "ARC": ["center", "radius", "start_angle", "end_angle"],
    "POINT": ["location"],
    "TEXT": ["text", "insert", "align_point", "height", "rotation", "halign", "valign"],
    "MTEXT": ["insert", "char_height", "width", "attachment_point"],
    "DIMENSION": ["defpoint", "defpoint2", "defpoint3", "defpoint4", "defpoint5", "angle", "text", "dimtype",
                  "text_midpoint", "dimstyle"],
    "INSERT": ["name", "insert", "xscale", "yscale", "zscale", "rotation"],
    "ELLIPSE": ["center", "major_axis", "ratio", "start_param", "end_param"],
}


def update_in_place(ctx: Ctx, old, new) -> None:
    t = old.dxftype()
    for k in ("layer", "color", "linetype", "lineweight"):
        if new.dxf.hasattr(k):
            old.dxf.set(k, new.dxf.get(k))
        elif old.dxf.hasattr(k):
            old.dxf.discard(k)
    if t == "LWPOLYLINE":
        old.set_points(list(new.get_points("xyseb")), format="xyseb")
        old.closed = new.closed
    elif t == "SPLINE":
        old.fit_points = list(new.fit_points)
        old.control_points = list(new.control_points)
    elif t in COPY_ATTRS:
        for k in COPY_ATTRS[t]:
            if new.dxf.hasattr(k):
                old.dxf.set(k, new.dxf.get(k))
            elif old.dxf.hasattr(k):
                old.dxf.discard(k)
        if t == "MTEXT":
            old.text = new.text
        if t == "INSERT":
            for a in new.attribs:
                oa = old.get_attrib(a.dxf.tag)
                if oa is not None:
                    oa.dxf.text = a.dxf.text
        if t == "DIMENSION":
            from .ops.edit import rerender_dimension
            rerender_dimension(ctx, old)
    else:
        raise AgentCADError("UNSUPPORTED_ENTITY", f"In-place update of {t} is not supported", category="internal",
                            entities=[old.dxf.handle])


def plan_tags(ops: list[dict[str, Any]]) -> set[str]:
    return {o["tag"] for o in ops if isinstance(o, dict) and o.get("tag")}


def tags_in(doc: Drawing) -> set[str]:
    return {t for e in doc.modelspace() if (t := get_tag(e))}
