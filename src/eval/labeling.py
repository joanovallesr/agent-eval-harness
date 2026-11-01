"""Build the blind human-labeling packet from Phase 5 multi-step traces.

The packet contains no model identity, no arm name, no cost, no token counts,
and no judge output. A private mapping file outside the packet joins packet
trace ids back to (task, arm) after the labels exist.
"""

from __future__ import annotations

import argparse
import json
import random
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

BLIND_FORBIDDEN = (
    "haiku",
    "sonnet",
    "claude-",
    "gpt-6",
    "gpt6",
)


class PacketError(ValueError):
    """The packet would leak model identity or is otherwise malformed."""


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise PacketError(f"{path}:{line_number}: invalid JSON: {exc}") from exc
            if not isinstance(row, dict):
                raise PacketError(f"{path}:{line_number}: row must be an object")
            rows.append(row)
    return rows


def _blind_steps(trace: Mapping[str, Any]) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    for step in trace.get("steps") or []:
        if not isinstance(step, Mapping):
            continue
        calls = []
        for call in step.get("tool_calls") or []:
            if not isinstance(call, Mapping):
                continue
            calls.append(
                {
                    "name": call.get("name"),
                    "arguments": call.get("input"),
                }
            )
        results = []
        for result in step.get("tool_results") or []:
            if not isinstance(result, Mapping):
                continue
            results.append(
                {
                    "name": result.get("name"),
                    "result": result.get("result"),
                    "is_error": bool(result.get("is_error")),
                }
            )
        steps.append(
            {
                "step": step.get("step"),
                "assistant_text": step.get("text", ""),
                "tool_calls": calls,
                "tool_results": results,
                "stop_reason": step.get("stop_reason", ""),
                "schema_repair": bool(step.get("schema_repair")),
            }
        )
    return steps


def _packet_trace(
    trace_id: str,
    task: Mapping[str, Any],
    trace: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "trace_id": trace_id,
        "task_id": task.get("id"),
        "query": task.get("query"),
        "offered_tools": list(task.get("tools") or []),
        "mock_results": list(task.get("fixtures") or []),
        "success_criteria": task.get("success_criteria"),
        "trace": {
            "steps": _blind_steps(trace),
            "final_answer": trace.get("final_answer", ""),
            "diagnostics": {
                "completed": bool(trace.get("completed")),
                "truncated": bool(trace.get("truncated")),
                "refusal": bool(trace.get("refusal")),
                "format_failure": bool(trace.get("format_failure")),
                "max_steps_reached": bool(trace.get("max_steps_reached")),
                "fixture_miss": bool(trace.get("fixture_misses")),
                "schema_repair": int(trace.get("schema_repair_retries", 0) or 0) > 0,
            },
        },
    }


def _assert_blind(rows: Iterable[Mapping[str, Any]]) -> None:
    text = "\n".join(json.dumps(row, sort_keys=True) for row in rows).lower()
    for token in BLIND_FORBIDDEN:
        if token in text:
            raise PacketError(
                f"packet would leak a forbidden identity token: {token!r}"
            )


def _assert_packet_dir_blind(packet_dir: Path) -> None:
    """Check every packet file, not just the trace rows."""

    for path in sorted(packet_dir.iterdir()):
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace").lower()
        for token in BLIND_FORBIDDEN:
            if token in text:
                raise PacketError(
                    f"packet file {path.name} would leak {token!r}"
                )


def build_packet(
    run_dir: Path | str,
    cases_path: Path | str,
    packet_dir: Path | str,
    mapping_path: Path | str,
    rubric_path: Path | str,
    *,
    seed: int = 20261031,
) -> dict[str, Any]:
    run_dir = Path(run_dir)
    packet_dir = Path(packet_dir)
    mapping_path = Path(mapping_path)
    if packet_dir.resolve() in mapping_path.resolve().parents or (
        mapping_path.resolve() == packet_dir.resolve()
    ):
        raise PacketError("the private mapping must live outside the packet directory")

    cases = {
        str(case["id"]): case
        for case in _read_jsonl(Path(cases_path))
        if isinstance(case, dict) and case.get("id")
    }
    trace_rows = _read_jsonl(run_dir / "traces.jsonl")
    if not trace_rows:
        raise PacketError(f"no traces found in {run_dir / 'traces.jsonl'}")

    assembled: list[dict[str, Any]] = []
    for row in trace_rows:
        task_id = str(row.get("task_id"))
        task = cases.get(task_id)
        if task is None:
            raise PacketError(f"trace names unknown task {task_id!r}")
        assembled.append(
            {
                "task_id": task_id,
                "arm": row.get("arm"),
                "model_id": row.get("model_id"),
                "effort": row.get("effort"),
                "task": task,
                "trace": row.get("trace") or {},
            }
        )

    random.Random(seed).shuffle(assembled)

    packet_rows: list[dict[str, Any]] = []
    mapping_rows: list[dict[str, Any]] = []
    for index, item in enumerate(assembled, start=1):
        trace_id = f"trace-{index:03d}"
        packet_rows.append(
            _packet_trace(trace_id, item["task"], item["trace"])
        )
        mapping_rows.append(
            {
                "trace_id": trace_id,
                "task_id": item["task_id"],
                "arm": item["arm"],
                "model_id": item["model_id"],
                "effort": item["effort"],
                "split": item["task"].get("split"),
            }
        )
    _assert_blind(packet_rows)

    packet_dir.mkdir(parents=True, exist_ok=True)
    with (packet_dir / "traces.jsonl").open("w", encoding="utf-8") as handle:
        for row in packet_rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    with (packet_dir / "labels.jsonl").open("w", encoding="utf-8") as handle:
        for row in packet_rows:
            handle.write(
                json.dumps(
                    {"trace_id": row["trace_id"], "label": "", "notes": ""},
                    sort_keys=True,
                )
                + "\n"
            )
    shutil.copyfile(Path(rubric_path), packet_dir / "RUBRIC.md")
    (packet_dir / "README.md").write_text(
        "# Multi-step labeling packet\n\n"
        "This packet is blind: it contains no model identity, no arm name, no "
        "cost or token count, and no judge output. Label every trace in\n"
        "`traces.jsonl` against `RUBRIC.md` and write the label into the\n"
        "matching row of `labels.jsonl`.\n\n"
        "Allowed labels: `correct`, `partial`, `wrong`.\n\n"
        "Each row in `traces.jsonl` carries the task, the scripted mock\n"
        "results (the world the agent saw), the full tool-call chain, and the\n"
        "final answer. Label the outcome, not the style. If a trace is\n"
        "ambiguous, use the notes column and apply the rubric's decision\n"
        "order.\n",
        encoding="utf-8",
    )
    metadata = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "n_traces": len(packet_rows),
        "seed": seed,
        "rubric": Path(rubric_path).name,
        "n_tasks": len({row["task_id"] for row in packet_rows}),
    }
    (packet_dir / "packet.json").write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    _assert_packet_dir_blind(packet_dir)
    mapping_path.parent.mkdir(parents=True, exist_ok=True)
    mapping_path.write_text(
        json.dumps(
            {
                "packet_dir": str(packet_dir),
                "run_dir": str(run_dir),
                "seed": seed,
                "mapping": mapping_rows,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return metadata


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the blind labeling packet")
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--cases", required=True)
    parser.add_argument("--packet-dir", required=True)
    parser.add_argument("--mapping", required=True)
    parser.add_argument("--rubric", required=True)
    parser.add_argument("--seed", type=int, default=20261031)
    args = parser.parse_args()
    metadata = build_packet(
        args.run_dir,
        args.cases,
        args.packet_dir,
        args.mapping,
        args.rubric,
        seed=args.seed,
    )
    print(json.dumps(metadata, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
