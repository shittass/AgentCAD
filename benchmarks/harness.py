"""Timing, environment fingerprint, result schema and baseline comparison for the local benchmark.

The benchmark answers one question in seconds: did the change make the runtime slower or
less correct? It measures the Python runtime in process (no LLM, no AutoCAD) and compares
the numbers with a recorded baseline on the same machine.

Numbers from different machines are not comparable: every comparison is gated on the
environment fingerprint (OS, CPU count, Python and ezdxf versions, and a calibration loop).
"""
from __future__ import annotations

import json
import os
import platform
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

SCHEMA = 1
PACKAGE = Path(__file__).resolve().parent
BASELINE = PACKAGE / "baseline.json"

# A ratio under this is noise on a laptop; workloads faster than FLOOR_MS are not gated
# because process scheduling moves them by multiples.
DEFAULT_TOLERANCE = 1.5
FLOOR_MS = 2.0
CALIBRATION_DRIFT = 0.25  # 25% calibration difference means a different machine or a busy host


@dataclass
class Timing:
    """One workload's samples: milliseconds, plus the median output size for agent-facing calls."""

    n: int
    median_ms: float
    p90_ms: float
    min_ms: float
    max_ms: float
    payload_kb: float | None = None

    @classmethod
    def of(cls, samples: list[float], payloads: list[float] | None = None) -> "Timing":
        """Build a Timing from raw millisecond samples (used where the caller times the steps itself)."""
        if not samples:
            raise ValueError("Timing needs at least one sample")
        return cls(n=len(samples), median_ms=percentile(samples, 0.5), p90_ms=percentile(samples, 0.9),
                   min_ms=min(samples), max_ms=max(samples),
                   payload_kb=percentile(payloads, 0.5) if payloads else None)

    def as_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"n": self.n, "median_ms": round(self.median_ms, 3),
                             "p90_ms": round(self.p90_ms, 3), "min_ms": round(self.min_ms, 3),
                             "max_ms": round(self.max_ms, 3)}
        if self.payload_kb is not None:
            d["payload_kb"] = round(self.payload_kb, 2)
        return d


@dataclass
class Sample:
    """One measured call or sequence, named and grouped for the report."""

    name: str
    group: str
    timing: Timing
    extra: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        d = {"name": self.name, "group": self.group, **self.timing.as_dict()}
        if self.extra:
            d["extra"] = self.extra
        return d


@dataclass
class Check:
    """One deterministic correctness check on a saved drawing (not a timing)."""

    name: str
    passed: bool
    detail: str
    ms: float

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "passed": self.passed, "detail": self.detail, "ms": round(self.ms, 2)}


def percentile(values: list[float], q: float) -> float:
    """Linear-interpolated percentile, q in [0, 1]."""
    xs = sorted(values)
    if not xs:
        raise ValueError("percentile of an empty sample")
    if len(xs) == 1:
        return xs[0]
    pos = (len(xs) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)


def measure(fn: Callable[[], Any], repeat: int = 7, warmup: int = 1,
            payload: bool = False, expect_ok: bool = True) -> Timing:
    """Time ``fn`` ``repeat`` times after ``warmup`` untimed calls; keep the median and the 90th percentile.

    ``payload``: also record the median ``len(json.dumps(result))`` of the return value, which is what an
    agent pays in context for the call. Serialising happens outside the timer. ``expect_ok``: a timed
    runtime call that answers ``ok: false`` raises, so a broken workload cannot report a fast error path.
    """
    def checked() -> Any:
        result = fn()
        if expect_ok and isinstance(result, dict) and result.get("ok") is False:
            error = result.get("error") or {}
            raise RuntimeError(f"{error.get('code')}: {error.get('message')}")
        return result

    for _ in range(max(0, warmup)):
        checked()
    samples: list[float] = []
    sizes: list[float] = []
    for _ in range(max(1, repeat)):
        t0 = time.perf_counter()
        result = checked()
        samples.append((time.perf_counter() - t0) * 1000.0)
        if payload:
            sizes.append(len(json.dumps(result, default=str)) / 1024.0)
    return Timing(n=len(samples), median_ms=percentile(samples, 0.5), p90_ms=percentile(samples, 0.9),
                  min_ms=min(samples), max_ms=max(samples),
                  payload_kb=percentile(sizes, 0.5) if payload else None)


def measure_process(argv: list[str], repeat: int = 3, env: dict[str, str] | None = None,
                    cwd: str | Path | None = None) -> Timing:
    """Time a fresh-process run of ``argv`` end to end (interpreter start included)."""
    samples: list[float] = []
    for _ in range(max(1, repeat)):
        t0 = time.perf_counter()
        subprocess.run(argv, cwd=str(cwd) if cwd else None, env=env, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, check=False)
        samples.append((time.perf_counter() - t0) * 1000.0)
    return Timing(n=len(samples), median_ms=percentile(samples, 0.5), p90_ms=percentile(samples, 0.9),
                  min_ms=min(samples), max_ms=max(samples))


def calibration() -> float:
    """A machine-speed probe with no I/O, no CAD and no randomness: fixed Python + json work.

    It normalises the numbers across machines: a host running 30% slower shows up here first, so a
    baseline comparison can say "different machine" instead of "regression".
    """
    data = [{"i": i, "s": str(i)} for i in range(200)]

    def work() -> float:
        acc = 0.0
        for _ in range(120):
            for row in data:
                acc += row["i"]
            json.dumps(data)
        return acc

    return measure(work, repeat=3, warmup=1).median_ms


def _git_state() -> dict[str, str]:
    try:
        head = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=str(PACKAGE.parent),
                              capture_output=True, text=True, check=False).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain"], cwd=str(PACKAGE.parent),
                               capture_output=True, text=True, check=False).stdout.strip()
        return {"commit": head or "unknown", "dirty": bool(dirty)}
    except OSError:
        return {"commit": "unknown", "dirty": False}


def environment() -> dict[str, Any]:
    """What the numbers depend on: pinned versions, OS, CPU count, calibration, checkout state."""
    import ezdxf

    try:
        from agentcad import __version__ as agentcad_version
    except Exception:  # noqa: BLE001 - the benchmark also runs against a broken tree, and must still report
        agentcad_version = "unknown"
    return {
        "os": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "ezdxf": ezdxf.__version__,
        "agentcad": agentcad_version,
        "cpu_count": os.cpu_count(),
        "calibration_ms": round(calibration(), 3),
        "git": _git_state(),
    }


def env_matches(base: dict[str, Any], cur: dict[str, Any]) -> tuple[bool, str]:
    """Whether two runs are comparable, and why not when they are not."""
    for key in ("os", "machine"):
        if base.get(key) != cur.get(key):
            return False, f"{key} changed ({base.get(key)} -> {cur.get(key)})"
    for key in ("python", "ezdxf"):
        b, c = str(base.get(key, "")).split(".")[:2], str(cur.get(key, "")).split(".")[:2]
        if b != c:
            return False, f"{key} changed ({base.get(key)} -> {cur.get(key)})"
    if base.get("cpu_count") != cur.get("cpu_count"):
        return False, f"cpu_count changed ({base.get('cpu_count')} -> {cur.get('cpu_count')})"
    b_cal, c_cal = float(base.get("calibration_ms") or 0), float(cur.get("calibration_ms") or 0)
    if b_cal > 0 and c_cal > 0 and abs(c_cal / b_cal - 1.0) > CALIBRATION_DRIFT:
        return False, f"calibration differs by {100 * (c_cal / b_cal - 1):+.0f}%"
    return True, "same machine and versions"


@dataclass
class Comparison:
    """One workload's current sample against the baseline sample of the same name."""

    name: str
    baseline_ms: float
    current_ms: float
    ratio: float
    status: str  # "ok" | "slower" | "faster" | "new" | "gone"

    @property
    def gated(self) -> bool:
        return self.status == "slower"


def compare_samples(base: dict[str, Any], cur: dict[str, Any],
                    tolerance: float = DEFAULT_TOLERANCE) -> list[Comparison]:
    """Match samples by name; a workload is slower only when it exceeds tolerance and the noise floor."""
    base_by = {s["name"]: s for s in base.get("samples", [])}
    cur_by = {s["name"]: s for s in cur.get("samples", [])}
    out: list[Comparison] = []
    for name in [*cur_by, *(n for n in base_by if n not in cur_by)]:
        if name in base_by and name in cur_by:
            b = float(base_by[name]["median_ms"])
            c = float(cur_by[name]["median_ms"])
            ratio = c / b if b > 0 else 0.0
            if c < FLOOR_MS or b < FLOOR_MS:
                status = "ok"
            elif ratio > tolerance:
                status = "slower"
            elif ratio < 1.0 / tolerance:
                status = "faster"
            else:
                status = "ok"
            out.append(Comparison(name, b, c, ratio, status))
        elif name in cur_by:
            out.append(Comparison(name, 0.0, float(cur_by[name]["median_ms"]), 0.0, "new"))
        else:
            out.append(Comparison(name, float(base_by[name]["median_ms"]), 0.0, 0.0, "gone"))
    return out


def regressions(comparisons: Iterable[Comparison]) -> list[Comparison]:
    return [c for c in comparisons if c.gated]


def result_dict(profile: str, env: dict[str, Any], samples: list[Sample],
                checks: list[Check], elapsed_s: float) -> dict[str, Any]:
    passed = sum(1 for c in checks if c.passed)
    return {
        "schema": SCHEMA,
        "profile": profile,
        "created": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "elapsed_s": round(elapsed_s, 3),
        "environment": env,
        "samples": [s.as_dict() for s in samples],
        "quality": {"passed": passed, "total": len(checks), "checks": [c.as_dict() for c in checks]},
    }


def load_json(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def save_json(path: str | Path, payload: dict[str, Any]) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return p


def _fmt_ms(value: float) -> str:
    return f"{value:,.2f} ms" if value >= 10 else f"{value:.2f} ms"


def render(result: dict[str, Any], comparisons: list[Comparison] | None = None) -> str:
    """The report a developer reads: one row per workload, the baseline delta, and the quality checks."""
    env = result["environment"]
    lines = [f"AgentCAD {result['profile']} benchmark | {env['os']} {env['release']} | Python {env['python']} |"
             f" ezdxf {env['ezdxf']} | {env.get('cpu_count')} CPUs | {env['git']['commit']}"
             f"{' (dirty)' if env['git'].get('dirty') else ''}", ""]
    by_name = {c.name: c for c in (comparisons or [])}
    head = f"{'group':<11} {'workload':<30} {'n':>3} {'median':>11} {'p90':>11} {'payload':>9} {'vs base':>9}"
    lines += [head, "-" * len(head)]
    current_group = None
    for s in result["samples"]:
        if s["group"] != current_group:
            if current_group is not None:
                lines.append("")
            current_group = s["group"]
        payload = f"{s['payload_kb']:.1f} KB" if "payload_kb" in s else "-"
        cmp_ = by_name.get(s["name"])
        if cmp_ is None:
            delta = ""
        elif cmp_.status == "new":
            delta = "new"
        else:
            delta = f"{cmp_.ratio:.2f}x"
            if cmp_.status == "slower":
                delta = f"SLOW {cmp_.ratio:.2f}x"
            elif cmp_.status == "faster":
                delta = f"fast {cmp_.ratio:.2f}x"
        lines.append(f"{s['group']:<11} {s['name']:<30} {s['n']:>3} {_fmt_ms(s['median_ms']):>11} "
                     f"{_fmt_ms(s['p90_ms']):>11} {payload:>9} {delta:>9}")
    quality = result["quality"]
    for s in result["samples"]:
        steps = s.get("extra", {}).get("steps")
        if not steps:
            continue
        lines += ["", f"{s['name']}: {len(steps)} calls, {_fmt_ms(s['median_ms'])} total"]
        for step in steps:
            lines.append(f"  {step['step']:<16} {_fmt_ms(step['ms']):>10}  {step['payload_kb']:>6.1f} KB")
    lines += ["", f"quality: {quality['passed']}/{quality['total']} checks passed"]
    for c in quality["checks"]:
        lines.append(f"  {'PASS' if c['passed'] else 'FAIL'}  {c['name']:<28} {c['detail']}")
    if comparisons:
        slower = [c for c in comparisons if c.status == "slower"]
        faster = [c for c in comparisons if c.status == "faster"]
        lines.append("")
        lines.append(f"baseline: {len(faster)} faster, {len(slower)} slower"
                     + (f" -> {', '.join(c.name for c in slower)}" if slower else ""))
    lines.append(f"elapsed: {result['elapsed_s']:.1f} s")
    return "\n".join(lines)
