"""The local benchmark harness: statistics, baseline gating, and its deterministic quality checks.

The timings themselves are machine-dependent and belong to whoever runs the benchmark; what these
tests pin down is the logic that reads them (median/percentile, the noise floor, the machine gate)
and the quality half, which must keep passing on any machine.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from benchmarks import harness  # noqa: E402
from benchmarks.quality import run_quality  # noqa: E402
from benchmarks.run import run  # noqa: E402
from benchmarks.workloads import WORKLOADS, select  # noqa: E402


def _sample(name: str, median_ms: float) -> dict:
    return {"name": name, "group": "g", "n": 3, "median_ms": median_ms, "p90_ms": median_ms,
            "min_ms": median_ms, "max_ms": median_ms}


def test_percentile_interpolates_between_neighbours():
    values = [1.0, 2.0, 3.0, 4.0, 5.0]
    assert harness.percentile(values, 0.5) == 3.0
    assert harness.percentile(values, 0.9) == 4.6
    assert harness.percentile([7.5], 0.9) == 7.5


def test_measure_keeps_serialisation_out_of_the_timer_and_flags_failures():
    timing = harness.measure(lambda: {"ok": True, "value": 1}, repeat=2, warmup=0, payload=True)
    assert timing.n == 2 and timing.payload_kb and timing.payload_kb > 0
    try:
        harness.measure(lambda: {"ok": False, "error": {"code": "BOOM", "message": "no"}}, repeat=1, warmup=0)
    except RuntimeError as exc:
        assert "BOOM" in str(exc)
    else:
        raise AssertionError("a failed call must not be recorded as a fast sample")


def test_compare_flags_a_slowdown_but_ignores_the_noise_floor():
    base = {"samples": [_sample("slow", 100.0), _sample("same", 100.0), _sample("tiny", 0.5)]}
    cur = {"samples": [_sample("slow", 180.0), _sample("same", 110.0), _sample("tiny", 9.0)]}
    by_name = {c.name: c for c in harness.compare_samples(base, cur, tolerance=1.5)}
    assert by_name["slow"].status == "slower"
    assert by_name["same"].status == "ok"
    assert by_name["tiny"].status == "ok"  # below the 2 ms floor: scheduling noise, not a regression
    assert [c.name for c in harness.regressions(by_name.values())] == ["slow"]


def test_compare_handles_new_and_dropped_workloads():
    base = {"samples": [_sample("gone", 10.0)]}
    cur = {"samples": [_sample("fresh", 10.0)]}
    by_name = {c.name: c for c in harness.compare_samples(base, cur)}
    assert by_name["fresh"].status == "new" and by_name["gone"].status == "gone"


def test_machine_gate_refuses_a_different_cpu_or_calibration():
    base = {"os": "Windows", "machine": "AMD64", "python": "3.13.15", "ezdxf": "1.4.5",
            "cpu_count": 24, "calibration_ms": 40.0}
    assert harness.env_matches(base, dict(base))[0]
    quiet = {**base, "calibration_ms": 80.0}
    ok, why = harness.env_matches(base, quiet)
    assert not ok and "calibration" in why
    assert not harness.env_matches(base, {**base, "cpu_count": 8})[0]
    assert not harness.env_matches(base, {**base, "ezdxf": "1.5.0"})[0]


def test_every_group_declares_a_profile_and_a_description():
    assert {w.name for w in WORKLOADS} >= {"ops", "read", "session", "mcp"}
    assert all(w.description and w.profiles for w in WORKLOADS)
    assert [w.name for w in select("full")] == [w.name for w in WORKLOADS]
    assert {w.name for w in select("quick")} < {w.name for w in select("full")}
    try:
        select("quick", ["no-such-group"])
    except ValueError as exc:
        assert "no-such-group" in str(exc)
    else:
        raise AssertionError("an unknown --only group must be an error")


def test_quality_checks_pass_on_a_fresh_workspace(tmp_path):
    from agentcad.policy import Policy
    from agentcad.service import Runtime

    checks = run_quality(tmp_path, Runtime(Policy(root=tmp_path)))
    failed = [f"{c.name}: {c.detail}" for c in checks if not c.passed]
    assert not failed, failed
    assert len(checks) == 6


def test_a_quick_subset_runs_end_to_end(tmp_path):
    result = run(profile="quick", only=["discovery"], repeat=1, root=tmp_path, quality=False)
    assert result["errors"] == []
    names = {s["name"] for s in result["samples"]}
    assert {"list_operations", "search_tools"} <= names
    assert result["quality"]["total"] == 0
    saved = harness.save_json(tmp_path / "out.json", result)
    assert harness.load_json(saved)["samples"] == result["samples"]
