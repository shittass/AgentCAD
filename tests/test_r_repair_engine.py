"""Repair layer, Scope 1: alias / parameter / enum normalization wired into the engine.

Each test proves one contract: an alias runs under its registered name, a
near-miss enum folds to the one allowed spelling, a rejection carries a
ready-to-send corrected batch, and a clean batch reports no repairs at all.
"""
from __future__ import annotations

import ezdxf
import pytest

from agentcad import repair
from agentcad.engine import execute, prevalidate
from agentcad.errors import AgentCADError
from agentcad.policy import Policy
from conftest import by_tag, ok, reread


def err(r):
    assert not r["ok"], r
    return r["error"]


# --- operation names -----------------------------------------------------------------------------

def test_op_alias_runs_as_the_registered_name(rt, ws, blank):
    r = ok(rt.apply_operations(blank, [{"op": "draw_line", "start": [0, 0], "end": [10, 0], "tag": "l"}]))
    assert r["results"][0]["op"] == "add_line"
    assert by_tag(reread(ws, blank), "l").dxf.end.x == pytest.approx(10)


def test_execute_reports_the_alias_repair(rt):
    doc = ezdxf.new("R2010", units=4)
    res = execute(doc, [{"op": "draw_line", "start": [0, 0], "end": [10, 0]}], "mm", rt.policy)
    assert res["results"][0]["op"] == "add_line"
    assert res["repaired"] == [{"kind": "alias", "rule": "op.alias", "confidence": 1.0,
                                "from": "draw_line", "to": "add_line"}]


def test_alias_is_checked_against_the_policy_allow_list(rt, ws):
    policy = Policy(root=ws, allowed_ops=frozenset({"add_line"}))
    doc = ezdxf.new("R2010", units=4)
    assert execute(doc, [{"op": "draw_line", "start": [0, 0], "end": [1, 0]}], "mm", policy)["added"]
    with pytest.raises(AgentCADError) as exc:
        execute(doc, [{"op": "draw_circle", "center": [0, 0], "radius": 1}], "mm", policy)
    assert exc.value.code == "OP_NOT_ALLOWED"


def test_unknown_op_offers_the_corrected_batch(rt, blank):
    e = err(rt.apply_operations(blank, [{"op": "add_cirle", "center": [0, 0], "radius": 1}]))
    assert e["code"] == "UNKNOWN_OP" and "add_circle" in e["hint"]
    rep = e["details"]["repair"]
    assert rep["kind"] == "suggest" and rep["rule"] == "op.unknown"
    assert rep["candidates"][0]["op"] == "add_circle"
    assert rep["suggested_operations"] == [{"op": "add_circle", "center": [0, 0], "radius": 1}]


def test_offered_operation_batch_runs(rt, ws, blank):
    e = err(rt.apply_operations(blank, [{"op": "add_cirle", "center": [0, 0], "radius": 1, "tag": "c"}]))
    ok(rt.apply_operations(blank, e["details"]["repair"]["suggested_operations"]))
    assert by_tag(reread(ws, blank), "c").dxf.radius == pytest.approx(1)


def test_nothing_close_is_not_dressed_up_as_a_repair(rt, blank):
    assert "details" not in err(rt.apply_operations(blank, [{"op": "zzzzzz"}]))
    assert "details" not in err(rt.apply_operations(blank, [{"op": "add_circle", "center": [0, 0], "radius": 1,
                                                             "qqqqqq": 1}]))


# --- parameters ----------------------------------------------------------------------------------

def test_enum_value_folds_to_the_allowed_spelling(rt):
    doc = ezdxf.new("R2010", units=4)
    res = execute(doc, [{"op": "add_text", "text": "A", "insert": [0, 0], "align": "MIDDLE_CENTER"}], "mm",
                  rt.policy)
    assert res["results"][0]["op"] == "add_text"
    assert res["repaired"] == [{"kind": "enum", "rule": "param.enum", "confidence": 1.0,
                                "from": "MIDDLE_CENTER", "to": "middle_center"}]


def test_param_alias_and_enum_are_reported(rt, ws, blank):
    r = ok(rt.apply_operations(blank, [{"op": "add_text", "text": "A", "position": [0, 0],
                                        "align": "Middle Center", "tag": "t"}]))
    assert r["results"][0]["op"] == "add_text"
    assert by_tag(reread(ws, blank), "t").dxf.text == "A"


def test_unknown_param_offers_the_corrected_batch(rt, blank):
    ops = [{"op": "add_circle", "center": [0, 0], "radius": 1, "diamter": 2}]
    e = err(rt.apply_operations(blank, ops))
    assert e["code"] == "UNKNOWN_PARAM"
    rep = e["details"]["repair"]
    assert rep["kind"] == "suggest" and rep["rule"] == "param.unknown"
    assert rep["candidates"][0] == {"param": "diameter", "for": "diamter", "confidence": 0.933, "why": "close"}
    assert rep["suggested_operations"] == [{"op": "add_circle", "center": [0, 0], "radius": 1, "diameter": 2}]


def test_near_miss_enum_offers_the_allowed_values(rt, blank):
    e = err(rt.apply_operations(blank, [{"op": "add_text", "text": "A", "insert": [0, 0], "align": "middle-centre"}]))
    assert e["code"] == "INVALID_ENUM"
    rep = e["details"]["repair"]
    assert rep["rule"] == "param.enum"
    assert rep["candidates"][0]["param"] == "align" and rep["candidates"][0]["suggestions"] == ["middle_center"]
    assert rep["suggested_operations"][0]["align"] == "middle_center"


# --- the clean path ------------------------------------------------------------------------------

def test_clean_batch_reports_no_repairs(rt):
    doc = ezdxf.new("R2010", units=4)
    res = execute(doc, [{"op": "add_circle", "center": [0, 0], "radius": 3}], "mm", rt.policy)
    assert res["repaired"] == []
    assert res["results"][0]["op"] == "add_circle" and len(res["results"][0]["created"]) == 1


def test_prevalidate_returns_params_and_repairs(rt):
    params, repairs = prevalidate([{"op": "draw_circle", "centre": [0, 0], "r": 2}], "mm", rt.policy)
    assert params[0]["radius"] == pytest.approx(2) and params[0]["center"] == (0.0, 0.0)
    assert [r["rule"] for r in repairs] == ["op.alias", "param.alias", "param.alias"]


def test_repairs_do_not_leak_between_batches(rt):
    doc = ezdxf.new("R2010", units=4)
    execute(doc, [{"op": "draw_line", "start": [0, 0], "end": [1, 0]}], "mm", rt.policy)
    clean = execute(doc, [{"op": "add_line", "start": [0, 0], "end": [0, 1]}], "mm", rt.policy)
    assert clean["repaired"] == []
    assert repair.ACTIVE_REPAIRS.get() is None


def test_failed_batch_releases_the_repair_collector(rt):
    doc = ezdxf.new("R2010", units=4)
    with pytest.raises(AgentCADError) as exc:
        execute(doc, [{"op": "draw_circle", "centre": [0, 0], "r": 1}, {"op": "add_line", "start": [0, 0]}],
                "mm", rt.policy)
    assert exc.value.code == "MISSING_PARAM"
    assert repair.ACTIVE_REPAIRS.get() is None
