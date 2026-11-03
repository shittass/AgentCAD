"""Turn a rejected call into the call the caller meant.

Three conservative rules, in order of how much is proven:

1. ``normalize_*`` — an unambiguous rename (an alias table entry, an enum value
   that differs only in case or separators). Applied silently and recorded.
2. ``suggest_*`` — close candidates ranked by ``difflib``; never applied, only
   offered to the caller as a ready-to-send call.
3. ``record`` — the audit line that says what was rewritten.

The tables only alias names a caller plausibly writes for an operation that
exists; a test pins every target against ``REGISTRY``, so a rename that loses
its target fails the suite instead of silently dead-ending.
"""
from __future__ import annotations

import difflib
import re
from contextvars import ContextVar
from typing import Any, Iterable

#: repairs applied during the current batch. ``ops`` workflows run inside
#: :class:`collect`; helpers call :func:`note` so nothing is rewritten silently.
ACTIVE_REPAIRS: ContextVar[list[dict[str, Any]] | None] = ContextVar("agentcad_repairs", default=None)
_token = None


class collect:
    """Collect repairs applied by code that has no other channel to the result."""

    def __enter__(self) -> "collect":
        self._bag: list[dict[str, Any]] = []
        self.token = ACTIVE_REPAIRS.set(self._bag)
        return self

    def __exit__(self, *exc: object) -> None:
        ACTIVE_REPAIRS.reset(self.token)

    @property
    def items(self) -> list[dict[str, Any]]:
        return list(self._bag)


def note(rec: dict[str, Any]) -> None:
    """Append one repair to the active collector, when there is one."""
    bag = ACTIVE_REPAIRS.get()
    if bag is not None:
        bag.append(rec)

# --- operation names ---------------------------------------------------------

#: alias -> canonical operation. Targets must exist in ``ops.REGISTRY``.
OP_ALIASES: dict[str, str] = {
    "add_rect": "add_rectangle",
    "draw_rectangle": "add_rectangle",
    "create_rectangle": "add_rectangle",
    "draw_line": "add_line",
    "create_line": "add_line",
    "draw_circle": "add_circle",
    "create_circle": "add_circle",
    "draw_arc": "add_arc",
    "create_arc": "add_arc",
    "draw_polyline": "add_polyline",
    "make_layer": "create_layer",
    "add_layer": "create_layer",
    "remove_entity": "delete",
    "delete_entity": "delete",
    "move_entity": "move",
    "rotate_entity": "rotate",
    "copy_entity": "copy",
    "mirror_entity": "mirror",
    "scale_entity": "scale",
    "insert_text": "add_text",
    "add_label": "add_text",
}

#: canonical operation -> {alias -> canonical parameter}
PARAM_ALIASES: dict[str, dict[str, str]] = {
    "add_text": {"position": "insert", "pos": "insert", "location": "insert",
                 "content": "text", "value": "text", "text_string": "text"},
    "add_mtext": {"position": "insert", "pos": "insert", "content": "text"},
    "add_circle": {"r": "radius", "d": "diameter", "centre": "center", "centre_point": "center"},
    "add_arc": {"centre": "center", "centre_point": "center"},
    "add_rectangle": {"centre": "center", "centre_point": "center", "w": "width", "h": "height"},
    "add_line": {"start_point": "start", "end_point": "end", "from": "start", "to": "end"},
    "add_polyline": {"vertices": "points", "pts": "points"},
    "create_layer": {"layer_name": "name", "colour": "color", "color_name": "color"},
    "move": {"offset": "delta", "vector": "delta"},
    "delete": {"entities": "target", "ids": "target"},
    "modify": {"targets": "target", "entities": "target"},
}

#: common parameter spellings shared by every operation that takes them.
GLOBAL_PARAM_ALIASES: dict[str, str] = {
    "layer_name": "layer",
    "colour": "color",
    "tag_name": "tag",
}

#: values of enum parameters that are spelled differently, folded to a canonical
#: value by ``normalize_enum`` (case and separators are already ignored).
_ENUM_SEPARATORS = re.compile(r"[\s_\-]+")


def record(kind: str, rule: str, *, frm: Any = None, to: Any = None,
           confidence: float = 1.0, why: str | None = None) -> dict[str, Any]:
    """One audit line: what a repair changed and under which rule."""
    out: dict[str, Any] = {"kind": kind, "rule": rule, "confidence": round(confidence, 3)}
    if frm is not None:
        out["from"] = frm
    if to is not None:
        out["to"] = to
    if why:
        out["why"] = why
    return out


def suggested_call(tool: str, arguments: dict[str, Any], why: str,
                   confidence: float = 1.0) -> dict[str, Any]:
    """A complete call the caller can send verbatim, with the reason it is offered."""
    return {"tool": tool, "arguments": arguments, "why": why, "confidence": round(confidence, 3)}


# --- operation names ---------------------------------------------------------

def normalize_op(name: str) -> tuple[str, dict[str, Any]] | None:
    """Return ``(canonical, record)`` when ``name`` is a known alias."""
    target = OP_ALIASES.get(name)
    if target is None:
        return None
    return target, record("alias", "op.alias", frm=name, to=target)


def suggest_ops(name: str, known: Iterable[str], limit: int = 3) -> list[dict[str, Any]]:
    """Rank canonical operations for a name that is not registered."""
    known = list(known)
    alias = OP_ALIASES.get(name)
    out: list[dict[str, Any]] = []
    if alias in known:
        out.append({"op": alias, "confidence": 1.0, "why": "alias"})
    for cand in difflib.get_close_matches(name, [k for k in known if k != alias], n=limit, cutoff=0.6):
        ratio = difflib.SequenceMatcher(None, name, cand).ratio()
        out.append({"op": cand, "confidence": round(min(0.95, ratio), 3), "why": "close"})
    return out[:limit]


# --- parameters --------------------------------------------------------------

def _rename_params(op: str, raw: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    table = {**GLOBAL_PARAM_ALIASES, **PARAM_ALIASES.get(op, {})}
    if not table:
        return raw, []
    out, repairs = dict(raw), []
    for key in list(raw):
        target = table.get(key)
        if target is None or target in raw:
            continue
        out[target] = out.pop(key)
        repairs.append(record("alias", "param.alias", frm=key, to=target))
    return out, repairs


def normalize_enum(value: Any, allowed: Iterable[str]) -> str | None:
    """Fold a near-miss enum value onto the one allowed spelling, if unique.

    ``MIDDLE_CENTER``, ``middle-center`` and ``Middle Center`` all fold to
    ``middle_center``; two different allowed values folding to the same key is
    ambiguous and returns ``None`` instead of guessing.
    """
    if not isinstance(value, str):
        return None
    key = _ENUM_SEPARATORS.sub("_", value.strip().lower())
    matches = [a for a in allowed if _ENUM_SEPARATORS.sub("_", a.strip().lower()) == key]
    return matches[0] if len(matches) == 1 else None


def _all_params(opdef: Any) -> dict[str, Any]:
    """Op-specific plus common parameters; ``coerce`` validates against the same set."""
    getter = getattr(opdef, "all_params", None)
    return getter() if callable(getter) else opdef.params


def normalize_params(opdef: Any, raw: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Apply alias and enum normalization for one operation.

    ``opdef`` is duck-typed: ``.name`` and parameters as an ordered mapping of
    name to an object with ``type`` and, for enums, ``enum``.
    """
    params, repairs = _rename_params(opdef.name, raw)
    for name, spec in _all_params(opdef).items():
        if name not in params or getattr(spec, "type", None) != "enum":
            continue
        canonical = normalize_enum(params[name], getattr(spec, "enum", None) or [])
        if canonical is not None and canonical != params[name]:
            repairs.append(record("enum", "param.enum", frm=params[name], to=canonical))
            params[name] = canonical
    return params, repairs


def suggest_params(opdef: Any, unknown: Iterable[str], limit: int = 3) -> list[dict[str, Any]]:
    """Rank parameters of ``opdef`` for parameter names it does not accept."""
    known = list(_all_params(opdef))
    table = {**GLOBAL_PARAM_ALIASES, **PARAM_ALIASES.get(opdef.name, {})}
    out: list[dict[str, Any]] = []
    for name in unknown:
        alias = table.get(name)
        if alias in known:
            out.append({"param": alias, "for": name, "confidence": 1.0, "why": "alias"})
            continue
        for cand in difflib.get_close_matches(name, known, n=limit, cutoff=0.6):
            out.append({"param": cand, "for": name,
                        "confidence": round(difflib.SequenceMatcher(None, name, cand).ratio(), 3),
                        "why": "close"})
    return out[:limit]


# --- selectors ---------------------------------------------------------------

#: filter-dict key -> canonical key (``select`` in ``model``)
SELECTOR_KEY_ALIASES: dict[str, str] = {
    "layr": "layer", "lyr": "layer", "layers": "layer", "on_layer": "layer",
    "types": "type", "entity_type": "type", "kind": "type",
    "tags": "tag",
    "prefix": "tag_prefix", "tag-startswith": "tag_prefix",
}

SELECTOR_KEYS = ("type", "layer", "tag", "tag_prefix", "layout", "all")


def normalize_filter(flt: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Rewrite known misspellings of selector keys; leave the rest for the caller to report."""
    out, repairs = dict(flt), []
    for key in list(flt):
        target = SELECTOR_KEY_ALIASES.get(key)
        if target is None or target in flt:
            continue
        out[target] = out.pop(key)
        repairs.append(record("alias", "selector.key", frm=key, to=target))
    return out, repairs


def suggest_selector_keys(unknown: Iterable[str]) -> list[dict[str, Any]]:
    """Rank the accepted selector keys for unknown ones."""
    out: list[dict[str, Any]] = []
    for key in unknown:
        for cand in difflib.get_close_matches(key, list(SELECTOR_KEYS), n=1, cutoff=0.5):
            out.append({"key": cand, "for": key,
                        "confidence": round(difflib.SequenceMatcher(None, key, cand).ratio(), 3)})
    return out
