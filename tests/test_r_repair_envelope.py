"""Scope 3: the envelope carries the repair advice, the repaired call, and what was rewritten.

The engine worker adds ``"repaired"`` to ``engine.execute``; these tests stub ``engine.execute`` so
they pass whether or not that key has landed yet.
"""
from __future__ import annotations

import inspect

from agentcad import service
from agentcad.errors import AgentCADError, input_error
from conftest import ok

BATCH = [{"op": "add_rect", "corner": [0, 0], "width": 10, "height": 10}]
SUGGESTED = [{"op": "add_rectangle", "corner": [0, 0], "width": 10, "height": 10}]
REPAIR = {
    "kind": "alias", "rule": "op.alias", "confidence": 1.0,
    "candidates": [{"op": "add_rectangle", "confidence": 1.0, "why": "alias"}],
    "suggested_operations": SUGGESTED,
}
REWRITES = [{"kind": "alias", "rule": "op.alias", "from": "add_rect", "to": "add_rectangle", "confidence": 1.0}]


def _apply_like(self, path: str, operations: list[dict], dry_run: bool = False) -> dict:
    return {}


def _read_like(self, path: str, detail: bool = True) -> dict:
    return {}


def _needs_label(self, path: str, operations: list[dict], label: str) -> dict:
    return {}


def stub_execute(monkeypatch, result=None, error=None):
    def run(doc, operations, unit, policy):
        if error is not None:
            raise error
        return result
    monkeypatch.setattr(service.engine, "execute", run)


def test_to_dict_carries_repair_only_when_set():
    plain = input_error("UNKNOWN_OP", "unknown operation: add_rect").to_dict()
    assert plain == {"code": "UNKNOWN_OP", "category": "input", "message": "unknown operation: add_rect",
                     "entities": [], "retryable": False, "partial_changes": False}
    assert "repair" not in plain

    full = input_error("UNKNOWN_OP", "unknown operation: add_rect", op_index=0, op="add_rect",
                       hint="Use add_rectangle", details={"candidates": 1}, repair=REPAIR).to_dict()
    assert full["repair"] == REPAIR
    assert {k: v for k, v in full.items() if k != "repair"} == {
        "code": "UNKNOWN_OP", "category": "input", "message": "unknown operation: add_rect", "entities": [],
        "retryable": False, "partial_changes": False, "op_index": 0, "op": "add_rect", "hint": "Use add_rectangle",
        "details": {"candidates": 1}}


def test_enrichment_promotes_repair_and_builds_suggested_call():
    err: dict = {}
    exc = input_error("UNKNOWN_OP", "unknown operation: add_rect", details={"repair": dict(REPAIR)})
    service._enrich_error(err, exc, "apply_operations", inspect.signature(_apply_like), None,
                          ("blank.dxf", BATCH), {})
    # self is dropped and only the arguments the caller actually passed are bound
    assert err["repair"] == {**REPAIR, "suggested_call": {
        "tool": "apply_operations", "arguments": {"path": "blank.dxf", "operations": SUGGESTED}}}


def test_enrichment_skips_suggested_call_without_operations_parameter():
    err: dict = {}
    exc = input_error("BAD_SELECTOR", "unknown selector key", details={"repair": dict(REPAIR)})
    service._enrich_error(err, exc, "inspect_entities", inspect.signature(_read_like), None, ("blank.dxf",), {})
    assert err["repair"] == REPAIR and "suggested_call" not in err["repair"]


def test_enrichment_skips_suggested_call_when_binding_fails():
    err: dict = {}
    exc = input_error("UNKNOWN_OP", "unknown operation: add_rect", details={"repair": dict(REPAIR)})
    service._enrich_error(err, exc, "apply_operations", inspect.signature(_needs_label), None,
                          ("blank.dxf", BATCH), {})  # label is required: bind raises TypeError
    assert err["repair"] == REPAIR and "suggested_call" not in err["repair"]


def test_enrichment_ignores_repair_without_suggested_operations():
    err: dict = {}
    rep = {k: v for k, v in REPAIR.items() if k != "suggested_operations"}
    exc = input_error("BAD_SELECTOR", "unknown selector key", details={"repair": rep})
    service._enrich_error(err, exc, "apply_operations", inspect.signature(_apply_like), None,
                          ("blank.dxf", BATCH), {})
    assert err["repair"] == rep and "suggested_call" not in err["repair"]


def test_tool_wrapper_promotes_repair_from_a_failed_call(rt, ws, blank, monkeypatch):
    stub_execute(monkeypatch, error=input_error("UNKNOWN_OP", "unknown operation: add_rect",
                                                op_index=0, op="add_rect", details={"repair": dict(REPAIR)}))
    before = (ws / blank).read_bytes()
    r = rt.apply_operations(blank, BATCH)
    assert not r["ok"] and r["tool"] == "apply_operations"
    err = r["error"]
    assert err["code"] == "UNKNOWN_OP" and err["op_index"] == 0
    assert {k: v for k, v in err["repair"].items() if k != "suggested_call"} == REPAIR
    assert err["details"]["repair"] == REPAIR  # the existing channel is untouched
    assert err["repair"]["suggested_call"] == {"tool": "apply_operations",
                                               "arguments": {"path": blank, "operations": SUGGESTED}}
    assert (ws / blank).read_bytes() == before  # a rejected batch changes nothing


def test_tool_wrapper_leaves_an_error_without_repair_alone(rt, blank, monkeypatch):
    stub_execute(monkeypatch, error=input_error("UNKNOWN_OP", "unknown operation: nope"))
    r = rt.apply_operations(blank, BATCH)
    assert not r["ok"]
    assert set(r["error"]) == {"code", "category", "message", "entities", "retryable", "partial_changes"}


def _stub_result(**extra) -> dict:
    return {"results": [{"op": "add_rectangle"}], "added": [], "modified": [], "deleted": [], **extra}


def test_apply_operations_reports_what_was_repaired(rt, blank, monkeypatch):
    stub_execute(monkeypatch, _stub_result(repaired=REWRITES))
    r = ok(rt.apply_operations(blank, BATCH))
    assert r["repaired"] == REWRITES


def test_apply_operations_dry_run_reports_what_would_be_repaired(rt, blank, monkeypatch):
    stub_execute(monkeypatch, _stub_result(repaired=REWRITES))
    r = ok(rt.apply_operations(blank, BATCH, dry_run=True))
    assert r["dry_run"] is True and r["repaired"] == REWRITES


def test_apply_operations_omits_an_empty_repaired(rt, blank, monkeypatch):
    stub_execute(monkeypatch, _stub_result(repaired=[]))
    assert "repaired" not in ok(rt.apply_operations(blank, BATCH))  # nothing rewritten: key stays out
    stub_execute(monkeypatch, _stub_result())  # engine without the key yet
    assert "repaired" not in ok(rt.apply_operations(blank, BATCH, dry_run=True))


def test_selector_alias_is_reported_by_a_successful_envelope(rt, blank):
    r = ok(rt.inspect_entities(blank, filter={"layr": "0"}))
    assert r["repaired"] and r["repaired"][0]["from"] == "layr" and r["repaired"][0]["to"] == "layer"
    assert "repaired" not in ok(rt.read_drawing(blank))  # a call that rewrote nothing stays unchanged


def test_agentcad_error_repair_attribute_round_trips():
    exc = AgentCADError("UNKNOWN_OP", "nope", repair=REPAIR)
    assert exc.repair == REPAIR and exc.to_dict()["repair"] == REPAIR
    assert AgentCADError("UNKNOWN_OP", "nope").repair is None
