"""Test R: a filter key the selector does not know is reported, never ignored.

An ignored key silently widens the selection to the whole active layout, so
``{"layr": "HOLES"}`` used to mean "every entity" and ``delete`` with it emptied
the drawing. Misspellings the repair table knows are rewritten (and recorded);
the rest stop the call.
"""
from __future__ import annotations

import pytest

from agentcad import model, repair
from agentcad.errors import AgentCADError
from conftest import ok, reread


def layered(rt, name="layers.dxf"):
    """OUTLINE: one rectangle. HOLES: two circles."""
    ok(rt.create_drawing(name, units="mm"))
    ok(rt.apply_operations(name, [
        {"op": "add_rectangle", "corner": [0, 0], "width": 100, "height": 60, "layer": "OUTLINE", "tag": "box"},
        {"op": "add_circle", "center": [10, 10], "radius": 4, "layer": "HOLES", "tag": "h1"},
        {"op": "add_circle", "center": [90, 10], "radius": 4, "layer": "HOLES", "tag": "h2"},
    ]))
    return name


# --- accepted spellings are rewritten and recorded ----------------------------------------------

def test_misspelled_layer_key_selects_only_that_layer(rt):
    name = layered(rt)
    got = ok(rt.inspect_entities(name, filter={"layr": "HOLES"}))["entities"]
    assert [e["layer"] for e in got] == ["HOLES", "HOLES"]
    assert {e["type"] for e in got} == {"CIRCLE"}


def test_applied_alias_is_recorded_for_the_batch(rt, ws):
    name = layered(rt)
    with repair.collect() as bag:
        ents = model.select(reread(ws, name), {"layr": "HOLES"})
    assert [e.dxf.layer for e in ents] == ["HOLES", "HOLES"]
    assert bag.items == [{"kind": "alias", "rule": "selector.key", "confidence": 1.0,
                          "from": "layr", "to": "layer"}]


def test_recorded_alias_does_not_leak_outside_the_collector(rt, ws):
    name = layered(rt)
    model.select(reread(ws, name), {"lyr": "HOLES"})
    assert repair.ACTIVE_REPAIRS.get() is None


# --- unknown keys stop the call ----------------------------------------------------------------

def test_unknown_key_raises_bad_selector_with_candidates(rt, ws):
    name = layered(rt)
    with pytest.raises(AgentCADError) as caught:
        model.select(reread(ws, name), {"typo": 1})
    err = caught.value
    assert err.code == "BAD_SELECTOR" and err.category == "input"
    rep = err.details["repair"]
    assert rep["kind"] == "suggest" and rep["rule"] == "selector.key"
    assert [c["key"] for c in rep["candidates"]] == ["type"]
    assert rep["suggested_filter"] == {"type": 1}  # the key the caller meant, ready to resend
    assert rep["suggested_arguments"] == {"target": {"type": 1}}
    assert "type, layer, tag, tag_prefix, layout, all" in (err.hint or "")


def test_unknown_key_surfaces_through_the_envelope(rt):
    name = layered(rt)
    e = rt.inspect_entities(name, filter={"lyer": "HOLES"})["error"]
    assert e["code"] == "BAD_SELECTOR"
    rep = e["details"]["repair"]
    assert rep["candidates"][0]["key"] == "layer" and rep["candidates"][0]["for"] == "lyer"
    assert rep["candidates"][0]["confidence"] >= 0.5


def test_explicit_key_plus_its_alias_is_reported_not_silently_dropped(rt):
    name = layered(rt)
    e = rt.inspect_entities(name, filter={"layer": "HOLES", "layr": "OUTLINE"})["error"]
    assert e["code"] == "BAD_SELECTOR"
    assert e["details"]["repair"]["candidates"][0]["key"] == "layer"


def test_unknown_key_deletes_nothing(rt, ws):
    """The data-loss regression: the call must refuse instead of selecting the whole layout."""
    name = layered(rt)
    err = rt.apply_operations(name, [{"op": "delete", "target": {"lyer": "OUTLINE"}}])["error"]
    assert err["code"] == "BAD_SELECTOR"
    assert sorted(e.dxftype() for e in reread(ws, name).modelspace()) == ["CIRCLE", "CIRCLE", "LWPOLYLINE"]


def test_a_key_no_candidate_resembles_still_lists_the_accepted_keys(rt):
    name = layered(rt)
    err = rt.inspect_entities(name, filter={"nonsense": 1})["error"]
    assert err["code"] == "BAD_SELECTOR"
    assert err["details"]["repair"]["candidates"] == []
    assert "type, layer, tag, tag_prefix, layout, all" in (err["hint"] or "")


# --- accepted forms behave as before ------------------------------------------------------------

def test_misspelled_layer_delete_keeps_the_rest_of_the_drawing(rt, ws):
    name = layered(rt)
    r = ok(rt.apply_operations(name, [{"op": "delete", "target": {"layr": "OUTLINE"}}]))
    assert len(r["changes"]["deleted"]) == 1
    assert r["repaired"] == [{"kind": "alias", "rule": "selector.key", "confidence": 1.0,
                              "from": "layr", "to": "layer"}]
    doc = reread(ws, name)
    assert sorted(e.dxftype() for e in doc.modelspace()) == ["CIRCLE", "CIRCLE"]
    assert {e.dxf.layer for e in doc.modelspace()} == {"HOLES"}


def test_all_layout_and_plain_filters_are_unchanged(rt):
    name = layered(rt)
    assert len(ok(rt.inspect_entities(name, filter={"type": "CIRCLE"}))["entities"]) == 2
    assert len(ok(rt.inspect_entities(name, filter={"tag_prefix": "h"}))["entities"]) == 2
    assert len(ok(rt.inspect_entities(name, filter={"type": ["CIRCLE", "LWPOLYLINE"]}))["entities"]) == 3
    # "all" wins over the other keys, as before; it is not passed to the matcher
    assert len(ok(rt.inspect_entities(name, filter={"all": True, "layer": "HOLES"}))["entities"]) == 3
    # layout is consumed before matching, so it is not an unknown key
    assert len(ok(rt.inspect_entities(name, filter={"layout": "Model", "layer": "HOLES"}))["entities"]) == 2


def test_empty_filter_still_refuses_to_select_everything(rt):
    name = layered(rt)
    e = rt.apply_operations(name, [{"op": "delete", "target": {}}])["error"]
    assert e["code"] == "EMPTY_SELECTOR" and "all" in (e["hint"] or "")


def test_all_true_deletes_every_entity(rt, ws):
    name = layered(rt)
    r = ok(rt.apply_operations(name, [{"op": "delete", "target": {"all": True}}]))
    assert len(r["changes"]["deleted"]) == 3
    assert not list(reread(ws, name).modelspace())
