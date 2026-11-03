"""Test R: the replay harness measures the repair layer against recorded agent errors."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import replay_repairs as replay  # noqa: E402


def _error(code: str, message: str, *, op: str | None = None,
           op_index: int | None = None) -> str:
    return json.dumps({"ok": False, "tool": "apply_operations",
                       "error": {"code": code, "message": message, "op": op,
                                 "op_index": op_index}})


def _ok(tool: str) -> str:
    return json.dumps({"ok": True, "tool": tool, "changes": {}})


def _write_claude_trace(path: Path, exchanges) -> None:
    """Write one Claude Code session: each exchange is a failed call plus its retry.

    ``exchanges`` items are ``(tool_name, failed_args, result_text, good_args,
    good_tool)``.  Every call gets its own assistant message, so one exchange
    spans two round-trips.
    """
    lines: list[dict] = []
    turn = 0
    for tool, failed_args, result_text, good_args, good_tool in exchanges:
        for args, text in ((failed_args, result_text), (good_args, _ok(good_tool))):
            turn += 1
            tool_id = f"toolu_{turn}"
            lines.append({"type": "assistant", "message": {"id": f"msg_{turn}", "content": [
                {"type": "tool_use", "id": tool_id, "name": tool, "input": args}]}})
            lines.append({"type": "user", "message": {"content": [
                {"type": "tool_result", "tool_use_id": tool_id,
                 "content": [{"type": "text", "text": text}]}]}})
    path.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")


def _fixture_exchanges():
    """Three errors the repair layer handles today, plus one it cannot reach."""
    return [
        ("mcp__agentcad__apply_operations",
         {"path": "p.dxf", "operations": [{"op": "draw_rectangle", "width": 10, "height": 20}]},
         _error("UNKNOWN_OP", "Unknown operation 'draw_rectangle'",
                op="draw_rectangle", op_index=0),
         {"path": "p.dxf", "operations": [{"op": "add_rectangle", "width": 10, "height": 20}]},
         "apply_operations"),
        ("mcp__agentcad__apply_operations",
         {"path": "p.dxf", "operations": [{"op": "add_text", "text": "A", "insert": [0, 0],
                                           "align": "MIDDLE_CENTER"}]},
         _error("INVALID_ENUM", "add_text.align: 'MIDDLE_CENTER' not in "
                "['left', 'center', 'middle', 'middle_center']",
                op="add_text", op_index=0),
         {"path": "p.dxf", "operations": [{"op": "add_text", "text": "A", "insert": [0, 0],
                                           "align": "middle_center"}]},
         "apply_operations"),
        ("mcp__agentcad__apply_operations",
         {"path": "p.dxf", "operations": [{"op": "add_text", "content": "B",
                                           "position": [5, 5]}]},
         _error("UNKNOWN_PARAM", "add_text: unknown parameter(s) ['content', 'position']",
                op="add_text", op_index=0),
         {"path": "p.dxf", "operations": [{"op": "add_text", "text": "B", "insert": [5, 5]}]},
         "apply_operations"),
        # The client rejects a bare tool name before the runtime ever sees it.
        ("apply_operations",
         {"path": "p.dxf", "operations": [{"op": "add_line", "start": [0, 0], "end": [1, 1]}]},
         "<tool_use_error>Error: No such tool available: apply_operations</tool_use_error>",
         {"path": "p.dxf", "operations": [{"op": "add_line", "start": [0, 0], "end": [1, 1]}]},
         "apply_operations"),
    ]


def _report_for(trace: Path) -> dict:
    fmt, calls = replay.parse_trace(trace, "fixture/task")
    assert fmt == "claude"
    meta = {"corpus": "fixture", "limit": None, "tasks": 1, "files": 1,
            "formats": {"claude": 1}, "samples_requested": 12, "max_samples": 12,
            "elapsed_seconds": 0.0, "repair_module": None,
            "registry_ops": len(replay.REGISTRY)}
    return replay.build_report([("fixture", "claude", calls)], meta, {"apply_operations"})


def test_fixture_trace_reports_auto_rewrites(tmp_path):
    trace = tmp_path / "task.jsonl"
    _write_claude_trace(trace, _fixture_exchanges())

    report = _report_for(trace)

    assert report["calls"]["errors"] == 4
    replay_part = report["replay"]
    assert replay_part["addressable"] == 3
    assert replay_part["converted"] == 3
    assert replay_part["auto_rewritten"] == 3
    assert replay_part["coverage_judged"] == 1.0
    assert replay_part["precision_at_1"] == 1.0
    assert replay_part["by_class"]["op_name"]["converted"] == 1
    assert replay_part["by_class"]["param"]["converted"] == 1
    assert replay_part["by_class"]["enum"]["converted"] == 1
    # Each in-place repair removes the one round-trip the agent spent on the retry.
    assert report["turns"]["saved_measured"] == 3
    assert report["turns"]["saved_ceiling"] == 3
    assert {"from": "draw_rectangle", "to": "add_rectangle", "count": 1} \
        in replay_part["gaps"]["op_name"]


def test_transport_errors_are_counted_but_out_of_scope(tmp_path):
    trace = tmp_path / "task.jsonl"
    _write_claude_trace(trace, _fixture_exchanges())

    report = _report_for(trace)

    assert report["calls"]["by_code"]["TRANSPORT_ERROR"] == 1
    assert report["replay"]["addressable"] == report["calls"]["errors"] - 1


def test_assistant_message_split_across_events_keeps_its_tool_call(tmp_path):
    """A message id repeats with different blocks; the tool call must survive once."""
    trace = tmp_path / "split.jsonl"
    events = [
        {"type": "assistant", "message": {"id": "msg_1", "content": [
            {"type": "thinking", "thinking": "plan"}]}},
        {"type": "assistant", "message": {"id": "msg_1", "content": [
            {"type": "tool_use", "id": "toolu_1", "name": "mcp__agentcad__apply_operations",
             "input": {"path": "a.dxf", "operations": [{"op": "add_line",
                                                        "start": [0, 0], "end": [1, 1]}]}}]}},
        {"type": "assistant", "message": {"id": "msg_1", "content": [
            {"type": "tool_use", "id": "toolu_1", "name": "mcp__agentcad__apply_operations",
             "input": {"path": "a.dxf", "operations": [{"op": "add_line",
                                                        "start": [0, 0], "end": [1, 1]}]}}]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "toolu_1", "content": [
                {"type": "text", "text": _ok("apply_operations")}]}]}},
    ]
    trace.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")

    fmt, calls = replay.parse_trace(trace, "fixture/split")

    assert fmt == "claude"
    assert len(calls) == 1
    assert calls[0].tool == "apply_operations"
    assert calls[0].ok


def test_sample_evenly_spreads_the_limit():
    items = list(range(100))
    assert replay.sample_evenly(items, 5) == [0, 20, 40, 60, 80]
    assert replay.sample_evenly(items, None) == items
    assert replay.sample_evenly(items, 0) == []
    assert len(replay.sample_evenly(items, 150)) == 100


@pytest.mark.skipif(len(replay.discover("all")) < 20, reason="trace corpora absent")
def test_cli_writes_a_bounded_report(tmp_path, capsys):
    target = tmp_path / "report.json"
    assert replay.main(["--limit", "20", "--corpus", "all", "--json", str(target)]) == 0
    printed = capsys.readouterr().out
    assert "class" in printed and "total" in printed and "synthetic" in printed

    report = json.loads(target.read_text(encoding="utf-8"))
    assert report["schema"] == replay.SCHEMA
    assert report["meta"]["files"] == 20
    assert report["meta"]["tasks"] <= report["meta"]["files"]
    assert report["meta"]["elapsed_seconds"] < 60

    calls = report["calls"]
    replay_part = report["replay"]
    assert set(replay_part["by_class"]) == {"op_name", "param", "enum", "selector"}
    assert calls["errors"] == sum(stats["errors"] for stats in report["corpora"].values())
    assert replay_part["converted"] <= replay_part["addressable"] <= calls["errors"]
    assert replay_part["converted"] <= replay_part["produced"]
    for key in ("coverage_judged", "coverage_addressable", "precision_at_1"):
        value = replay_part[key]
        assert value is None or 0.0 <= value <= 1.0, key

    turns = report["turns"]
    assert turns["saved_measured"] <= turns["saved_ceiling"]
    assert turns["saved_measured"] >= 0
    assert turns["median"] is None or turns["median"] >= 0

    synthetic = report["synthetic"]
    assert synthetic["op_names"]["variants"] > 0
    assert 0.0 <= synthetic["op_names"]["hit_rate"] <= 1.0
    assert synthetic["op_names"]["top1_hits"] <= synthetic["op_names"]["variants"]
    assert synthetic["params"]["variants"] > 0
    assert 0.0 <= synthetic["params"]["hit_rate"] <= 1.0
    assert len(synthetic["op_names"]["buckets"]) >= 3

    assert len(report["limitations"]) >= 3
    assert report["generated_at"]
