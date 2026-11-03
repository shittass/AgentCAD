"""Test R: end-to-end repair — a wrong call reaches the same intent through the runtime."""
from __future__ import annotations

import json

from conftest import ok


def _draw_two_layers(rt, blank):
    ok(rt.apply_operations(blank, [
        {"op": "add_rectangle", "corner": [0, 0], "width": 40, "height": 20, "layer": "OUTLINE", "tag": "outline"},
        {"op": "add_circle", "center": [10, 10], "radius": 2, "layer": "HOLES", "tag": "hole.a"},
        {"op": "add_circle", "center": [30, 10], "radius": 2, "layer": "HOLES", "tag": "hole.b"},
    ]))
    return blank


def test_operation_alias_is_applied_and_reported(rt, blank):
    r = ok(rt.apply_operations(blank, [{"op": "draw_rectangle", "corner": [0, 0], "width": 10, "height": 5}]))
    assert r["changes"]["added"], r
    assert r.get("repaired"), "an aliased call must say it was rewritten"
    rep = r["repaired"][0]
    assert rep["from"] == "draw_rectangle" and rep["to"] == "add_rectangle"
    assert r["results"][0]["op"] == "add_rectangle"


def test_parameter_alias_is_applied_and_reported(rt, blank):
    r = ok(rt.apply_operations(blank, [{"op": "add_text", "text": "PLATE", "position": [0, 0]}]))
    assert r["changes"]["added"]
    assert any(x["from"] == "position" and x["to"] == "insert" for x in r.get("repaired", []))


def test_enum_value_is_folded_and_reported(rt, blank):
    r = ok(rt.apply_operations(blank, [{"op": "add_text", "text": "PLATE", "insert": [0, 0],
                                       "align": "MIDDLE_CENTER"}]))
    assert r["changes"]["added"]
    assert any(x["kind"] == "enum" and x["to"] == "middle_center" for x in r.get("repaired", []))


def test_unknown_operation_returns_a_sendable_call(rt, blank):
    r = rt.apply_operations(blank, [{"op": "add_rectangel", "corner": [0, 0], "width": 10, "height": 5}])
    assert not r["ok"]
    err = r["error"]
    assert err["code"] == "UNKNOWN_OP"
    rep = err.get("repair")
    assert rep, "a rejected batch must carry a repair suggestion"
    call = rep.get("suggested_call")
    assert call and call["tool"] == "apply_operations"
    assert call["arguments"]["path"] == blank
    assert call["arguments"]["operations"][0]["op"] == "add_rectangle"


def test_unknown_parameter_returns_a_sendable_call(rt, blank):
    r = rt.apply_operations(blank, [{"op": "add_text", "text": "X", "insert": [0, 0], "heigth": 5}])
    assert not r["ok"]
    err = r["error"]
    assert err["code"] == "UNKNOWN_PARAM"
    rep = err.get("repair") or {}
    cands = rep.get("candidates") or []
    assert any(c["param"] == "height" for c in cands), cands
    call = rep.get("suggested_call") or {}
    assert call.get("arguments", {}).get("operations"), "suggested_call should carry the corrected batch"
    assert call["arguments"]["operations"][0]["height"] == 5


def test_misspelled_selector_key_targets_only_that_layer(rt, blank):
    _draw_two_layers(rt, blank)
    r = ok(rt.inspect_entities(blank, filter={"layr": "HOLES"}))
    assert r["total"] == 2, json.dumps(r, indent=1, default=str)
    assert {e["tag"] for e in r["entities"]} == {"hole.a", "hole.b"}
    assert r.get("repaired"), "the rewritten filter must be reported"


def test_misspelled_selector_key_cannot_empty_the_drawing(rt, blank):
    """Regression: before the fix, {"layr": ...} degraded to every entity on the layout."""
    _draw_two_layers(rt, blank)
    before = ok(rt.read_drawing(blank))["entity_count"]
    r = ok(rt.apply_operations(blank, [{"op": "delete", "target": {"layr": "HOLES"}}]))
    after = ok(rt.read_drawing(blank))["entity_count"]
    assert before - after == 2, (before, after, r["changes"])
    assert len(r["changes"]["deleted"]) == 2


def test_unknown_selector_key_is_rejected_with_candidates(rt, blank):
    _draw_two_layers(rt, blank)
    r = rt.inspect_entities(blank, filter={"foo": "bar"})
    assert not r["ok"]
    err = r["error"]
    assert err["code"] == "BAD_SELECTOR"
    rep = err.get("repair") or {}
    # nothing is close to "foo", so the vocabulary must arrive another way
    assert rep.get("candidates") or "Filter keys" in (err.get("hint") or ""), err


def test_close_selector_key_comes_with_the_call_to_send(rt, blank):
    _draw_two_layers(rt, blank)
    r = rt.inspect_entities(blank, filter={"lyer": "HOLES"})
    assert not r["ok"]
    err = r["error"]
    assert err["code"] == "BAD_SELECTOR"
    rep = err["repair"]
    assert rep["suggested_filter"] == {"layer": "HOLES"}
    call = rep["suggested_call"]
    assert call["tool"] == "inspect_entities"
    assert call["arguments"]["filter"] == {"layer": "HOLES"}
    assert ok(rt.inspect_entities(blank, filter=call["arguments"]["filter"]))["total"] == 2


def test_all_must_be_a_boolean(rt, blank):
    _draw_two_layers(rt, blank)
    r = rt.inspect_entities(blank, filter={"all": "true"})
    assert not r["ok"] and r["error"]["code"] == "BAD_SELECTOR"


def test_all_false_is_not_an_empty_filter(rt, blank):
    _draw_two_layers(rt, blank)
    r = rt.inspect_entities(blank, filter={"all": False})
    assert not r["ok"] and r["error"]["code"] == "EMPTY_SELECTOR"


def test_filter_value_types_are_rejected_not_crashed(rt, blank):
    _draw_two_layers(rt, blank)
    r = rt.inspect_entities(blank, filter={"type": 3})
    assert not r["ok"]
    assert r["error"]["code"] == "BAD_SELECTOR", r["error"]


def test_export_over_an_existing_file_offers_the_overwrite_call(rt, blank):
    _draw_two_layers(rt, blank)
    ok(rt.export_drawing(blank, "preview.svg", "svg"))
    r = rt.export_drawing(blank, "preview.svg", "svg")
    assert not r["ok"] and r["error"]["code"] == "FILE_EXISTS"
    call = r["error"]["repair"]["suggested_call"]
    assert call["tool"] == "export_drawing"
    assert call["arguments"]["output"] == "preview.svg" and call["arguments"]["overwrite"] is True
    ok(rt.export_drawing(blank, "preview.svg", "svg", overwrite=True))
