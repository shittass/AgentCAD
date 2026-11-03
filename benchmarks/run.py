"""Command line for the local benchmark.

    python -m benchmarks                       # quick profile, ~10 s, compares with benchmarks/baseline.json
    python -m benchmarks --profile full        # adds MCP, exports and a 2k-entity drawing
    python -m benchmarks --only ops,read       # a subset of workload groups
    python -m benchmarks --save                # record a new baseline for this machine
    python -m benchmarks --gate                # exit 1 on a performance regression (CI or a pre-push check)

Exit codes: 0 all quality checks passed (and no gated regression), 1 a check, a crashed workload or
the gate failed, 2 the command line was wrong.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from .harness import (BASELINE, DEFAULT_TOLERANCE, compare_samples, env_matches, environment, load_json,
                      regressions, render, result_dict, save_json)
from .quality import run_quality
from .workloads import WORKLOADS, Context, select


def make_runtime(root: Path) -> Any:
    """The runtime under test, with the live viewer off (a benchmark must not open a browser)."""
    os.environ["AGENTCAD_VIEWER"] = "0"
    from agentcad.policy import Policy
    from agentcad.service import Runtime
    return Runtime(Policy(root=root))


def run(profile: str = "quick", only: list[str] | None = None, repeat: int | None = None,
        root: Path | None = None, quality: bool = True) -> dict[str, Any]:
    """Measure the selected workloads in ``root`` (a fresh temporary directory by default)."""
    work = Path(root) if root else Path(tempfile.mkdtemp(prefix="agentcad-bench-"))
    work.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    runtime = make_runtime(work)
    env = environment()
    ctx = Context(work, runtime, profile, repeat,
                  process_env={**os.environ, "AGENTCAD_VIEWER": "0", "PYTHONUTF8": "1"})
    samples = []
    errors = []
    for workload in select(profile, only):
        try:
            samples.extend(workload.fn(ctx))
        except Exception as exc:  # noqa: BLE001 - one broken group must not hide the other numbers
            errors.append({"workload": workload.name, "error": f"{type(exc).__name__}: {exc}"})
            print(f"workload {workload.name!r} failed: {type(exc).__name__}: {exc}", file=sys.stderr)
    checks = run_quality(work, runtime) if quality else []
    result = result_dict(profile, env, samples, checks, time.perf_counter() - started)
    result["errors"] = errors
    result["work_dir"] = str(work)
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m benchmarks", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--profile", choices=("quick", "full"), default="quick",
                        help="quick keeps the run near ten seconds; full adds the slow paths")
    parser.add_argument("--only", metavar="GROUPS", help="comma-separated workload groups (see --list)")
    parser.add_argument("--repeat", type=int, metavar="N", help="override the repeat count of every workload")
    parser.add_argument("--root", type=Path, metavar="DIR", help="workspace directory (default: a temp directory)")
    parser.add_argument("--keep", action="store_true", help="keep the workspace after the run")
    parser.add_argument("--no-quality", action="store_true", help="skip the correctness checks")
    parser.add_argument("--baseline", type=Path, metavar="PATH",
                        help=f"baseline to compare against (default: {BASELINE.name} next to the code, if present)")
    parser.add_argument("--tolerance", type=float, default=DEFAULT_TOLERANCE, metavar="RATIO",
                        help=f"slowdown that counts as a regression (default {DEFAULT_TOLERANCE})")
    parser.add_argument("--gate", action="store_true", help="exit 1 when a workload exceeds the tolerance")
    parser.add_argument("--save", nargs="?", const=str(BASELINE), metavar="PATH",
                        help=f"record this run as the baseline (default: {BASELINE})")
    parser.add_argument("--json", type=Path, metavar="PATH", help="write the full result JSON")
    parser.add_argument("--list", action="store_true", help="list workload groups and exit")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.list:
        for w in WORKLOADS:
            profiles = "quick+full" if w.profiles == {"quick", "full"} else next(iter(w.profiles))
            print(f"{w.name:<11} {profiles:<11} {w.description}")
        return 0
    only = [name for name in (args.only or "").split(",") if name.strip()] or None
    try:
        result = run(args.profile, only, args.repeat, args.root, quality=not args.no_quality)
    except ValueError as exc:  # unknown group in --only
        print(f"error: {exc}", file=sys.stderr)
        return 2

    baseline = Path(args.baseline) if args.baseline else (BASELINE if BASELINE.exists() else None)
    comparisons = []
    note = ""
    if baseline and baseline.exists():
        base = load_json(baseline)
        comparable, note = env_matches(base.get("environment", {}), result["environment"])
        if comparable:
            comparisons = compare_samples(base, result, args.tolerance)
        else:
            note = f"not compared with {baseline.name}: {note}"
    print(render(result, comparisons))
    if note:
        print(note)

    if args.json:
        print(f"\nresult: {save_json(args.json, result)}")
    if args.save:
        # The workspace path is this run's temporary directory; a committed baseline should not carry it.
        baseline_payload = {k: v for k, v in result.items() if k != "work_dir"}
        print(f"baseline saved: {save_json(Path(args.save), baseline_payload)}")
    if not args.keep and args.root is None:
        shutil.rmtree(result["work_dir"], ignore_errors=True)

    failed = [c for c in result["quality"]["checks"] if not c["passed"]]
    regressed = regressions(comparisons) if args.gate else []
    if result["errors"] or failed or regressed:
        for error in result["errors"]:
            print(f"FAILED workload {error['workload']}: {error['error']}", file=sys.stderr)
        for check in failed:
            print(f"FAILED check {check['name']}: {check['detail']}", file=sys.stderr)
        for cmp in regressed:
            print(f"FAILED gate {cmp.name}: {cmp.current_ms:.2f} ms vs {cmp.baseline_ms:.2f} ms "
                  f"({cmp.ratio:.2f}x)", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
