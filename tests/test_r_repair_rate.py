"""Test R: the repair rate the benchmark reports, and the live counters behind it.

The rate has a fixed denominator on purpose: the corpus in ``benchmarks/repair.py`` is a regression
gate, so a dropped alias or a broken wiring path shows up as a lower number instead of a silent
loss of a capability.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from benchmarks.repair import (  # noqa: E402
    FALSE_REPAIR_LIMIT, REPAIR_FLOOR, candidate_top1, measure)
from conftest import ok  # noqa: E402


def test_fixed_corpus_is_repaired_and_touches_no_correct_call(tmp_path):
    result = measure(tmp_path / "repair")
    assert result["eligible"] >= 10
    assert result["miss"] == 0
    assert result["repair_rate"] == 1.0 and result["repair_rate"] >= REPAIR_FLOOR
    assert result["zero_turn_rate"] >= 0.75
    assert result["false_repair"] == FALSE_REPAIR_LIMIT
    assert result["control_ok"] == result["controls"] >= 3
    outcomes = {c["name"]: c["outcome"] for c in result["cases"]}
    assert outcomes["op draw_rectangle"] == "auto"
    assert outcomes["selector layr"] == "auto"
    assert outcomes["op add_circl"] == "suggested"
    assert outcomes["param heigth"] == "suggested"


def test_candidate_top1_stays_high():
    got = candidate_top1()
    assert got["variants"] > 200
    assert got["top1"] >= 0.95


def test_runtime_reports_the_repairs_it_applied(rt, blank):
    ok(rt.apply_operations(blank, [{"op": "draw_rectangle", "corner": [0, 0], "width": 5, "height": 5}]))
    stats = rt.get_metrics()["repair"]
    assert stats["repaired_calls"] == 1 and stats["repairs"] == 1
    assert stats["rate"] == 1.0 and stats["suggested_calls"] == 0


def test_runtime_counts_a_failed_call_as_unrepaired(rt, blank):
    r = rt.apply_operations(blank, [{"op": "add_circl", "center": [0, 0], "radius": 1}])
    assert not r["ok"] and r["error"]["repair"]["suggested_call"]
    stats = rt.get_metrics()["repair"]
    assert stats["suggested_calls"] == 1 and stats["repaired_calls"] == 0 and stats["rate"] == 0.0


def test_quick_benchmark_prints_a_repair_rate(tmp_path):
    from benchmarks.run import run
    result = run(profile="quick", only=["discovery"], repeat=1, root=tmp_path, quality=True)
    repair = result["repair"]
    assert repair["eligible"] >= 10 and repair["repair_rate"] == 1.0
    assert repair["candidates"]["top1"] >= 0.95
