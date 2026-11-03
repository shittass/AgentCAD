"""Test R: the repair table turns a rejected call into the call the caller meant."""
from __future__ import annotations

import pytest

from agentcad import repair
from agentcad.ops import REGISTRY
from agentcad.ops.registry import COMMON_ALL, COMMON_DRAW


def params_of(op: str) -> dict:
    return {**COMMON_ALL, **COMMON_DRAW, **REGISTRY[op].params}


def test_every_op_alias_targets_a_registered_operation():
    missing = {alias: target for alias, target in repair.OP_ALIASES.items() if target not in REGISTRY}
    assert not missing


def test_every_param_alias_targets_a_parameter_of_that_operation():
    bad = {}
    for op, table in repair.PARAM_ALIASES.items():
        assert op in REGISTRY, op
        known = params_of(op)
        for alias, target in table.items():
            if target not in known:
                bad[f"{op}.{alias}"] = target
    assert not bad


def test_global_param_aliases_target_common_parameters():
    known = {**COMMON_ALL, **COMMON_DRAW}
    assert all(target in known for target in repair.GLOBAL_PARAM_ALIASES.values())


def test_normalize_op_returns_canonical_name_and_record():
    got = repair.normalize_op("draw_rectangle")
    assert got is not None
    canonical, rec = got
    assert canonical == "add_rectangle"
    assert rec["kind"] == "alias" and rec["from"] == "draw_rectangle" and rec["to"] == "add_rectangle"
    assert rec["confidence"] == 1.0


def test_normalize_op_ignores_registered_name():
    assert repair.normalize_op("add_rectangle") is None


def test_suggest_ops_ranks_a_misspelling_first():
    got = repair.suggest_ops("add_circl", REGISTRY)
    assert got and got[0]["op"] == "add_circle"
    assert got[0]["confidence"] >= 0.6


def test_suggest_ops_uses_alias_with_full_confidence():
    got = repair.suggest_ops("draw_circle", REGISTRY)
    assert got[0] == {"op": "add_circle", "confidence": 1.0, "why": "alias"}


def test_normalize_enum_folds_case_and_separators():
    allowed = ["left", "middle_center", "top_left"]
    assert repair.normalize_enum("MIDDLE_CENTER", allowed) == "middle_center"
    assert repair.normalize_enum("Middle Center", allowed) == "middle_center"
    assert repair.normalize_enum("middle-center", allowed) == "middle_center"


def test_normalize_enum_does_not_guess_when_ambiguous():
    assert repair.normalize_enum("a_b", ["a_b", "a-b"]) is None
    assert repair.normalize_enum("nope", ["left", "center"]) is None
    assert repair.normalize_enum(3, ["3"]) is None


def test_normalize_params_renames_and_reports():
    raw = {"text": "PLATE", "position": [0, 0]}
    params, repairs = repair.normalize_params(REGISTRY["add_text"], raw)
    assert params["insert"] == [0, 0] and "position" not in params
    assert [r["from"] for r in repairs] == ["position"] and repairs[0]["to"] == "insert"


def test_normalize_params_keeps_explicit_canonical_key():
    raw = {"text": "PLATE", "insert": [1, 1], "position": [0, 0]}
    params, repairs = repair.normalize_params(REGISTRY["add_text"], raw)
    assert params["insert"] == [1, 1]
    # the alias is left in place, so coerce reports it instead of hiding the conflict
    assert "position" in params
    assert repairs == []


def test_normalize_params_folds_enum_values():
    raw = {"text": "PLATE", "insert": [0, 0], "align": "MIDDLE_CENTER"}
    params, repairs = repair.normalize_params(REGISTRY["add_text"], raw)
    assert params["align"] == "middle_center"
    assert repairs and repairs[0]["kind"] == "enum" and repairs[0]["to"] == "middle_center"


def test_normalize_params_handles_a_common_enum_parameter():
    raw = {"text": "PLATE", "insert": [0, 0], "if_tag_exists": "SKIP"}
    params, repairs = repair.normalize_params(REGISTRY["add_text"], raw)
    assert params["if_tag_exists"] == "skip"
    assert repairs and repairs[0]["to"] == "skip"


def test_suggest_params_ranks_a_misspelling():
    got = repair.suggest_params(REGISTRY["add_text"], ["heigth"])
    assert got and got[0]["param"] == "height" and got[0]["for"] == "heigth"


def test_suggest_params_uses_alias_with_full_confidence():
    got = repair.suggest_params(REGISTRY["add_text"], ["pos"])
    assert got[0] == {"param": "insert", "for": "pos", "confidence": 1.0, "why": "alias"}


def test_normalize_filter_rewrites_known_key_aliases():
    flt, repairs = repair.normalize_filter({"layr": "HOLES"})
    assert flt == {"layer": "HOLES"}
    assert repairs[0]["from"] == "layr" and repairs[0]["to"] == "layer"


def test_normalize_filter_does_not_override_an_explicit_key():
    flt, repairs = repair.normalize_filter({"layer": "HOLES", "layr": "OUTLINE"})
    assert flt["layer"] == "HOLES" and "layr" in flt and repairs == []


def test_suggest_selector_keys_finds_the_intended_key():
    got = repair.suggest_selector_keys(["layr"])
    assert got and got[0]["key"] == "layer"


def test_collect_captures_notes_only_inside_the_context():
    repair.note({"kind": "alias"})  # outside: silently dropped
    with repair.collect() as bag:
        repair.note({"kind": "alias", "rule": "op.alias"})
        repair.note({"kind": "enum", "rule": "param.enum"})
    assert [r["rule"] for r in bag.items] == ["op.alias", "param.enum"]
    assert repair.ACTIVE_REPAIRS.get() is None


def test_record_and_suggested_call_shapes():
    rec = repair.record("alias", "op.alias", frm="a", to="b")
    assert rec == {"kind": "alias", "rule": "op.alias", "confidence": 1.0, "from": "a", "to": "b"}
    call = repair.suggested_call("apply_operations", {"path": "p.dxf"}, "because")
    assert call["tool"] == "apply_operations" and call["arguments"] == {"path": "p.dxf"}
    assert call["confidence"] == 1.0
