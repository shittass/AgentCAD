"""Deterministic correctness checks on drawings the runtime writes to disk.

These are the benchmark's quality half: fast, hermetic assertions on what ends up in the file, so a
change that makes the runtime faster by drawing something wrong fails here instead of in the full
test suite or in an agent trial. They read the saved DXF back with ezdxf rather than trusting the
runtime's own report.
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import ezdxf
from ezdxf import bbox

from .harness import Check
from .workloads import DIMS, SPEC, hole_ops, rect_ops

PLATE_LAYERS = ("OUTLINE", "HOLES", "CENTER", "DIM", "TEXT")


@dataclass
class Case:
    name: str
    description: str
    fn: Callable[[Any, str, Path], tuple[bool, str]]


CASES: list[Case] = []


def case(name: str, description: str):
    def register(fn: Callable[[Any, str, Path], tuple[bool, str]]):
        CASES.append(Case(name, description, fn))
        return fn
    return register


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _open(root: Path, group: str, name: str):
    return ezdxf.readfile(str(root / group / name))


def _ok(result: dict) -> dict:
    if not result.get("ok"):
        raise AssertionError(f"call failed: {result.get('error')}")
    return result


def _validation_passed(result: dict) -> bool:
    return bool(result.get("validation", {}).get("passed"))


def _issue_codes(result: dict) -> list[str]:
    return [i["code"] for i in result.get("validation", {}).get("issues", [])]


@case("plate_from_spec", "a spec plate writes four corner holes and its dimensions")
def plate_from_spec(rt, group: str, root: Path) -> tuple[bool, str]:
    result = _ok(rt.draw_from_spec(SPEC, f"{group}/plate.dxf"))
    if not _validation_passed(result):
        return False, f"validation failed: {_issue_codes(result)}"
    doc = _open(root, group, "plate.dxf")
    msp = doc.modelspace()
    circles = [e for e in msp if e.dxftype() == "CIRCLE"]
    dims = [e for e in msp if e.dxftype() == "DIMENSION"]
    if len(circles) != 4:
        return False, f"{len(circles)} circles, expected 4"
    radii = sorted(round(e.dxf.radius, 3) for e in circles)
    if radii != [4.0, 4.0, 4.0, 4.0]:
        return False, f"hole radii {radii}, expected 4.0 four times"
    if len(dims) < 7:
        return False, f"{len(dims)} dimensions, expected at least 7 from the spec plan"
    outline = bbox.extents([e for e in msp if e.dxf.layer == "OUTLINE"], fast=True)
    extents = (round(outline.extmin.x, 3), round(outline.extmin.y, 3),
               round(outline.extmax.x, 3), round(outline.extmax.y, 3))
    if extents != (0.0, 0.0, 100.0, 60.0):
        return False, f"outline extents {extents}, expected (0, 0, 100, 60)"
    centers = sorted((round(e.dxf.center.x, 3), round(e.dxf.center.y, 3)) for e in circles)
    if centers != [(10.0, 10.0), (10.0, 50.0), (90.0, 10.0), (90.0, 50.0)]:
        return False, f"hole centers {centers}, expected the four corners at 10 mm"
    missing = [n for n in PLATE_LAYERS if n not in doc.layers]
    if missing:
        return False, f"template layers missing: {missing}"
    return True, f"4 holes R4.0, {len(dims)} dimensions, 100.0x60.0 mm, validation passed"


@case("dimensions_attached", "hand-drawn holes get dimensions whose geometry is attached")
def dimensions_attached(rt, group: str, root: Path) -> tuple[bool, str]:
    path = f"{group}/plate.dxf"
    _ok(rt.apply_operations(path, [*rect_ops(100, 60), *hole_ops(4)], create={"units": "mm"}))
    result = _ok(rt.apply_operations(path, DIMS))
    if not _validation_passed(result):
        return False, f"validation failed: {_issue_codes(result)}"
    codes = set(_issue_codes(result))
    bad = {"DIMENSION_NOT_ATTACHED", "DIMENSION_DEGENERATE"} & codes
    if bad:
        return False, f"dimension issues: {sorted(bad)}"
    doc = _open(root, group, "plate.dxf")
    dims = [e for e in doc.modelspace() if e.dxftype() == "DIMENSION"]
    if len(dims) != 3:
        return False, f"{len(dims)} dimensions, expected 3"
    unattached = [d.dxf.handle for d in dims if not d.dxf.hasattr("geometry")]
    if unattached:
        return False, f"dimensions without geometry block: {unattached}"
    return True, f"{len(dims)} dimensions attached to their geometry, validation passed"


@case("undo_restores", "rollback puts the previous geometry back byte for byte")
def undo_restores(rt, group: str, root: Path) -> tuple[bool, str]:
    path = f"{group}/plate.dxf"
    _ok(rt.draw_from_spec(SPEC, path))
    before = _sha(root / group / "plate.dxf")
    _ok(rt.apply_operations(path, [{"op": "add_circle", "center": [50, 30], "diameter": "6mm",
                                    "tag": "probe", "layer": "HOLES"}]))
    first = _sha(root / group / "plate.dxf")
    _ok(rt.apply_operations(path, [{"op": "add_circle", "center": [50, 30], "diameter": "20mm",
                                    "tag": "probe", "layer": "HOLES", "if_tag_exists": "replace"}]))
    after = _sha(root / group / "plate.dxf")
    if len({before, first, after}) != 3:
        return False, "each edit should commit a different file"
    _ok(rt.rollback(path))
    restored = _sha(root / group / "plate.dxf")
    doc = _open(root, group, "plate.dxf")
    probe = [e for e in doc.modelspace() if e.dxftype() == "CIRCLE" and round(e.dxf.radius, 3) == 3.0]
    if restored != first:
        return False, f"rollback sha {restored[:12]} != pre-undo {first[:12]}"
    if not probe:
        return False, "the d=6 probe circle did not come back"
    return True, "rollback restored the exact prior file (sha match) and its d=6 circle"


@case("batch_family", "apply_batch writes three independent drawings in one call")
def batch_family(rt, group: str, root: Path) -> tuple[bool, str]:
    jobs = []
    for i in range(3):
        jobs.append({"path": f"{group}/part{i}.dxf", "create": {"units": "mm"}, "operations": [
            {"op": "add_circle", "center": [10 + i, 10], "diameter": f"{4 + i}mm", "layer": "HOLES"}]})
    result = _ok(rt.apply_batch(jobs))
    if not result["all_passed"]:
        return False, f"batch not all_passed: {result['succeeded']}/{len(jobs)}"
    radii = []
    for i in range(3):
        doc = _open(root, group, f"part{i}.dxf")
        circles = [e for e in doc.modelspace() if e.dxftype() == "CIRCLE"]
        if len(circles) != 1:
            return False, f"part{i}.dxf has {len(circles)} circles, expected 1"
        radii.append(round(circles[0].dxf.radius, 3))
    if radii != [2.0, 2.5, 3.0]:
        return False, f"radii {radii}, expected [2.0, 2.5, 3.0]"
    return True, f"3/3 drawings written, radii {radii}"


@case("revision_guard", "a stale expected_revision is refused and the file is untouched")
def revision_guard(rt, group: str, root: Path) -> tuple[bool, str]:
    path = f"{group}/plate.dxf"
    _ok(rt.draw_from_spec(SPEC, path))
    before = _sha(root / group / "plate.dxf")
    result = rt.apply_operations(path, [{"op": "add_circle", "center": [50, 30], "diameter": "5mm"}],
                                 expected_revision=999_999)
    if result.get("ok"):
        return False, "a stale expected_revision was accepted"
    code = result.get("error", {}).get("code")
    if code != "STALE_REVISION":
        return False, f"error code {code!r}, expected STALE_REVISION"
    if _sha(root / group / "plate.dxf") != before:
        return False, "the refused edit changed the file"
    return True, "refused with STALE_REVISION, file unchanged"


@case("validation_catches", "out-of-bounds geometry is reported, not silently committed")
def validation_catches(rt, group: str, root: Path) -> tuple[bool, str]:
    path = f"{group}/plate.dxf"
    _ok(rt.apply_operations(path, rect_ops(100, 60), create={"units": "mm"}))
    result = rt.apply_operations(path, [{"op": "add_circle", "center": [200, 30], "diameter": "5mm"}],
                                 bounds=[[0, 0], [120, 80]])
    if not result.get("ok"):
        return False, f"the call itself failed: {result.get('error')}"
    if result["validation"]["passed"]:
        return False, "an entity outside the bounds passed validation"
    codes = set(_issue_codes(result))
    if "OUT_OF_BOUNDS" not in codes:
        return False, f"issues {sorted(codes)}, expected OUT_OF_BOUNDS"
    return True, "OUT_OF_BOUNDS reported in the call's own validation"


def run_quality(root: Path, runtime: Any) -> list[Check]:
    """Run every case in its own workspace subdirectory; a crash is a failed check, not a crashed run."""
    checks: list[Check] = []
    (root / "quality").mkdir(parents=True, exist_ok=True)
    for c in CASES:
        group = f"quality/{c.name}"
        (root / group).mkdir(parents=True, exist_ok=True)
        t0 = time.perf_counter()
        try:
            passed, detail = c.fn(runtime, group, root)
        except Exception as exc:  # noqa: BLE001 - a broken runtime must show up as a failed check
            passed, detail = False, f"{type(exc).__name__}: {exc}"
        checks.append(Check(c.name, passed, detail, (time.perf_counter() - t0) * 1000.0))
    return checks
