"""Phase 5 multi-step loader, validator, and trace runner.

The multi-step set is the judge's only territory. It is separate from the
pre-registered single-turn test: these traces never enter the headline
analysis, and the judge plays no role in the pre-registered test.

Every task ships with deterministic mock results. The loop executes those
fixtures, so a dependent second call has a real result to continue from.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from jsonschema import Draft202012Validator

from agent.multistep_agent import MultiStepAgent, MultiStepTrace
from agent.multistep_tools import TOOLS_BY_NAME, tool_schemas
from eval.config import (
    MULTISTEP_ARMS,
    MULTISTEP_DEV_VS_FINAL,
    MULTISTEP_LABEL,
    MULTISTEP_MAX_STEPS,
    MULTISTEP_MAX_TOKENS,
    MULTISTEP_PROMPT_VERSION,
    MULTISTEP_SET_PATH,
    multistep_effort_for,
    multistep_model_id_for,
)
from eval.metrics import (
    PRICING_PER_MTOK,
    PricingError,
    TokenPrice,
    load_pricing_file,
    price_for,
)
from eval.mini_set import CANDIDATE_TOOLS


class MultiStepError(ValueError):
    """The multi-step set is malformed."""


@dataclass(frozen=True)
class MultiStepCase:
    id: str
    split: str
    query: str
    tools: tuple[str, ...]
    fixtures: tuple[dict[str, Any], ...]
    success_criteria: str
    notes: str = ""

    def to_task(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "split": self.split,
            "query": self.query,
            "tools": list(self.tools),
            "fixtures": [dict(fixture) for fixture in self.fixtures],
            "success_criteria": self.success_criteria,
            "notes": self.notes,
        }


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise MultiStepError(
                    f"{path}:{line_number}: invalid JSON: {exc}"
                ) from exc
            if not isinstance(row, dict):
                raise MultiStepError(f"{path}:{line_number}: row must be an object")
            rows.append(row)
    return rows


def load_cases(path: Path | str = MULTISTEP_SET_PATH) -> list[MultiStepCase]:
    rows = _read_jsonl(Path(path))
    cases: list[MultiStepCase] = []
    for index, row in enumerate(rows, start=1):
        task_id = row.get("id")
        split = row.get("split")
        query = row.get("query")
        tools = row.get("tools")
        fixtures = row.get("fixtures")
        success = row.get("success_criteria")
        if not isinstance(task_id, str) or not task_id:
            raise MultiStepError(f"row {index}: missing non-empty string 'id'")
        if split not in {"tune", "report"}:
            raise MultiStepError(f"{task_id}: split must be 'tune' or 'report'")
        if not isinstance(query, str) or not query.strip():
            raise MultiStepError(f"{task_id}: missing non-empty string 'query'")
        if not isinstance(tools, list) or not all(
            isinstance(name, str) and name for name in tools
        ):
            raise MultiStepError(f"{task_id}: 'tools' must be a list of names")
        if not isinstance(fixtures, list) or not fixtures:
            raise MultiStepError(f"{task_id}: 'fixtures' must be a non-empty list")
        if not isinstance(success, str) or not success.strip():
            raise MultiStepError(
                f"{task_id}: missing non-empty string 'success_criteria'"
            )
        normalised_fixtures: list[dict[str, Any]] = []
        for fixture in fixtures:
            if not isinstance(fixture, dict):
                raise MultiStepError(f"{task_id}: every fixture must be an object")
            normalised_fixtures.append(dict(fixture))
        cases.append(
            MultiStepCase(
                id=task_id,
                split=split,
                query=query,
                tools=tuple(tools),
                fixtures=tuple(normalised_fixtures),
                success_criteria=success,
                notes=str(row.get("notes", "")),
            )
        )
    return cases


def _scalars(value: Any) -> list[Any]:
    if isinstance(value, Mapping):
        out: list[Any] = []
        for item in value.values():
            out.extend(_scalars(item))
        return out
    if isinstance(value, (list, tuple)):
        out = []
        for item in value:
            out.extend(_scalars(item))
        return out
    if isinstance(value, (str, int, float)) and not isinstance(value, bool):
        return [value]
    return []


def _token_in(needle: str, haystack: str) -> bool:
    """Whole-token match, so ``14C`` does not count as a reference to ``1``."""

    if needle == haystack:
        return True
    pattern = r"(?<![A-Za-z0-9])" + re.escape(needle) + r"(?![A-Za-z0-9])"
    return re.search(pattern, haystack) is not None


def _references(value: Any, haystack: Sequence[Any]) -> bool:
    for candidate in haystack:
        if value == candidate:
            return True
        if isinstance(value, str) and isinstance(candidate, str):
            if len(candidate) >= 2 and _token_in(candidate, value):
                return True
            if len(value) >= 2 and _token_in(value, candidate):
                return True
        if isinstance(value, str) and isinstance(candidate, (int, float)):
            if _token_in(str(candidate), value):
                return True
    return False


def dependency_count(case: MultiStepCase) -> int:
    """Count later fixtures whose arguments reference an earlier result.

    This is a structural check on the authored chain, not a claim that every
    call in a task depends on the one before it.
    """

    count = 0
    earlier: list[Any] = []
    for index, fixture in enumerate(case.fixtures):
        args = fixture.get("args") or {}
        values = _scalars(args)
        if index > 0 and any(_references(value, earlier) for value in values):
            count += 1
        earlier.extend(_scalars(fixture.get("result")))
    return count


def validate_case(case: MultiStepCase) -> None:
    if len(set(case.tools)) != len(case.tools):
        raise MultiStepError(f"{case.id}: duplicate names in offered tools")
    unknown = [name for name in case.tools if name not in TOOLS_BY_NAME]
    if unknown:
        raise MultiStepError(
            f"{case.id}: unknown multi-step tool(s): {', '.join(unknown)}"
        )
    held_out = CANDIDATE_TOOLS.intersection(case.tools)
    if held_out:
        raise MultiStepError(
            f"{case.id}: uses frozen held-out candidate tool(s): "
            f"{', '.join(sorted(held_out))}"
        )
    if len(case.fixtures) < 2:
        raise MultiStepError(f"{case.id}: needs at least two scripted tool calls")
    if dependency_count(case) < 1:
        raise MultiStepError(
            f"{case.id}: no later fixture references an earlier mock result"
        )

    for index, fixture in enumerate(case.fixtures, start=1):
        tool = fixture.get("tool")
        args = fixture.get("args")
        if tool not in case.tools:
            raise MultiStepError(
                f"{case.id}: fixture {index} uses {tool!r}, which is not offered"
            )
        if not isinstance(args, dict):
            raise MultiStepError(f"{case.id}: fixture {index} 'args' must be an object")
        if "result" not in fixture:
            raise MultiStepError(f"{case.id}: fixture {index} has no 'result'")
        match = fixture.get("match")
        if match is not None and not isinstance(match, dict):
            raise MultiStepError(f"{case.id}: fixture {index} 'match' must be an object")
        schema = TOOLS_BY_NAME[tool]["input_schema"]
        errors = sorted(
            Draft202012Validator(schema).iter_errors(args),
            key=lambda error: list(error.path),
        )
        if errors:
            detail = "; ".join(error.message for error in errors)
            raise MultiStepError(
                f"{case.id}: fixture {index} args violate {tool!r} schema: {detail}"
            )
        try:
            json.dumps(fixture["result"])
        except (TypeError, ValueError) as exc:
            raise MultiStepError(
                f"{case.id}: fixture {index} result is not JSON-serializable"
            ) from exc
        try:
            tool_schemas(list(case.tools))
        except KeyError as exc:  # defensive: already checked above
            raise MultiStepError(f"{case.id}: {exc}") from exc


def validate_set(cases: Iterable[MultiStepCase]) -> None:
    seen: set[str] = set()
    split_counts = {"tune": 0, "report": 0}
    for case in cases:
        if case.id in seen:
            raise MultiStepError(f"duplicate case id: {case.id}")
        seen.add(case.id)
        split_counts[case.split] += 1
        validate_case(case)
    if not seen:
        raise MultiStepError("multi-step set is empty")
    if split_counts != {"tune": 15, "report": 15}:
        raise MultiStepError(
            "multi-step split must be 15 tune / 15 report tasks, got "
            f"{split_counts['tune']} / {split_counts['report']}"
        )


def load_and_validate(path: Path | str = MULTISTEP_SET_PATH) -> list[MultiStepCase]:
    cases = load_cases(path)
    validate_set(cases)
    return cases


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def _atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def _trace_record(trace: MultiStepTrace, case: MultiStepCase) -> dict[str, Any]:
    return {
        "task_id": case.id,
        "split": case.split,
        "arm": trace.arm,
        "model_id": trace.model_id,
        "effort": trace.effort,
        "status": "completed" if trace.completed else "incomplete",
        "n_steps": trace.n_steps,
        "n_tool_calls": trace.n_tool_calls,
        "final_answer": trace.final_answer,
        "truncated": trace.truncated,
        "refusal": trace.refusal,
        "format_failure": trace.format_failure,
        "max_steps_reached": trace.max_steps_reached,
        "failure_reason": trace.failure_reason,
        "fixture_misses": list(trace.fixture_misses),
        "schema_repair_retries": trace.schema_repair_retries,
        "infrastructure_retries": trace.infrastructure_retries,
        "input_tokens": trace.input_tokens,
        "output_tokens": trace.output_tokens,
        "cost_usd": trace.cost_usd,
        "latency_s": trace.latency_s,
        "cost_ceiling_reached": trace.cost_ceiling_reached,
    }


def _summary(
    records: Sequence[Mapping[str, Any]],
    *,
    arm: str,
    n_tasks: int,
) -> dict[str, Any]:
    completed = [record for record in records if record["status"] == "completed"]
    costs = [float(record["cost_usd"]) for record in records]
    input_tokens = sum(int(record["input_tokens"]) for record in records)
    output_tokens = sum(int(record["output_tokens"]) for record in records)
    return {
        "arm": arm,
        "n_tasks": n_tasks,
        "n_traces": len(records),
        "n_completed": len(completed),
        "n_incomplete": len(records) - len(completed),
        "n_truncated": sum(1 for record in records if record["truncated"]),
        "n_refusal": sum(1 for record in records if record["refusal"]),
        "n_format_failure": sum(
            1 for record in records if record["format_failure"]
        ),
        "n_max_steps_reached": sum(
            1 for record in records if record["max_steps_reached"]
        ),
        "n_with_fixture_miss": sum(
            1 for record in records if record["fixture_misses"]
        ),
        "n_schema_repair": sum(
            1 for record in records if int(record["schema_repair_retries"]) > 0
        ),
        "n_tune_traces": sum(1 for record in records if record["split"] == "tune"),
        "n_report_traces": sum(1 for record in records if record["split"] == "report"),
        "mean_steps": (
            sum(int(record["n_steps"]) for record in records) / len(records)
            if records
            else 0.0
        ),
        "mean_tool_calls": (
            sum(int(record["n_tool_calls"]) for record in records) / len(records)
            if records
            else 0.0
        ),
        "input_tokens_total": input_tokens,
        "output_tokens_total": output_tokens,
        "cost_usd_total": sum(costs),
        "cost_per_task": sum(costs) / len(records) if records else 0.0,
        "latency_p50_s": _percentile([float(r["latency_s"]) for r in records], 0.50),
        "latency_p95_s": _percentile([float(r["latency_s"]) for r in records], 0.95),
    }


def _percentile(values: Sequence[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * q
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    fraction = position - low
    return ordered[low] + (ordered[high] - ordered[low]) * fraction


def _arm_configs(arms: Sequence[str] | None = None) -> list[dict[str, Any]]:
    return [
        {
            "arm": arm,
            "model_id": multistep_model_id_for(arm),
            "effort": multistep_effort_for(arm),
            "effort_transmitted": multistep_effort_for(arm) != "api_default",
        }
        for arm in (arms or MULTISTEP_ARMS)
    ]


def _pricing_rows(prices: Mapping[str, TokenPrice]) -> dict[str, Any]:
    return {model_id: price.to_dict() for model_id, price in prices.items()}


def run_multistep(
    cases_path: Path | str,
    out_dir: Path | str,
    arms: Sequence[str] | None = None,
    *,
    client: Any = None,
    pricing: Mapping[str, TokenPrice] | None = None,
    cost_ceiling: float | None = None,
    run_id: str | None = None,
    resume: bool = True,
    pricing_path: str | None = None,
    case_ids: Sequence[str] | None = None,
) -> dict[str, Any]:
    if cost_ceiling is None or cost_ceiling <= 0:
        raise ValueError("a live multi-step run requires an explicit positive ceiling")
    selected_arms = list(arms or MULTISTEP_ARMS)
    unknown = [arm for arm in selected_arms if arm not in MULTISTEP_ARMS]
    if unknown:
        raise ValueError(f"unknown multi-step arm(s): {', '.join(unknown)}")

    table = dict(PRICING_PER_MTOK if pricing is None else pricing)
    if not table:
        raise PricingError(
            "no pinned list prices are configured; supply --pricing-file"
        )
    prices: dict[str, TokenPrice] = {}
    for arm in selected_arms:
        try:
            prices[arm] = price_for(multistep_model_id_for(arm), table)
        except PricingError as exc:
            raise PricingError(
                f"no pinned list price for {multistep_model_id_for(arm)}"
            ) from exc

    cases = load_and_validate(Path(cases_path))
    if case_ids:
        wanted = list(dict.fromkeys(case_ids))
        by_id = {case.id: case for case in cases}
        unknown_cases = [case_id for case_id in wanted if case_id not in by_id]
        if unknown_cases:
            raise ValueError(f"unknown case id(s): {', '.join(unknown_cases)}")
        cases = [case for case in cases if case.id in set(wanted)]
    if run_id is None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        run_id = f"phase5-multistep-{stamp}-{uuid.uuid4().hex[:8]}"
    run_dir = Path(out_dir) / run_id
    created_at = _now_iso()
    git_commit = _git_commit()
    resume_history: list[dict[str, Any]] = []
    total_cost = 0.0
    status = "running"
    abort_reason: str | None = None
    records: dict[str, list[dict[str, Any]]] = {arm: [] for arm in selected_arms}
    trace_blobs: dict[tuple[str, str], dict[str, Any]] = {}

    if run_dir.exists():
        if not resume:
            raise RuntimeError(f"run directory already exists: {run_dir}")
        existing_path = run_dir / "run.json"
        if not existing_path.exists():
            raise RuntimeError(f"run directory has no run.json: {run_dir}")
        existing = json.loads(existing_path.read_text())
        if existing.get("run_status") == "completed":
            raise RuntimeError(f"run {run_id} is already completed")
        if list(existing.get("selected_arms", selected_arms)) != selected_arms:
            raise RuntimeError("resume arm selection does not match the original run")
        if list(existing.get("case_ids", [case.id for case in cases])) != [
            case.id for case in cases
        ]:
            raise RuntimeError("resume case selection does not match the original run")
        created_at = str(existing.get("created_at", created_at))
        git_commit = str(existing.get("git_commit", git_commit))
        resume_history = list(existing.get("resume_history", []))
        resume_history.append(
            {
                "resumed_at": _now_iso(),
                "previous_status": existing.get("run_status"),
                "previous_abort_reason": existing.get("abort_reason"),
                "previous_total_cost_usd": existing.get("total_cost_usd", 0.0),
            }
        )
        # Lifetime spend includes prior completed and aborted attempts.
        for arm in selected_arms:
            for path in sorted((run_dir / arm / "checkpoints").glob("*.json")):
                try:
                    payload = json.loads(path.read_text())
                except (OSError, json.JSONDecodeError):
                    continue
                total_cost += float(payload.get("cost_usd") or 0.0)

    _atomic_write_json(
        run_dir / "run.json",
        {
            "run_id": run_id,
            "git_commit": git_commit,
            "created_at": created_at,
            "label": MULTISTEP_LABEL,
            "dev_vs_final": MULTISTEP_DEV_VS_FINAL,
            "run_status": "running",
            "abort_reason": None,
            "cost_ceiling_usd": cost_ceiling,
            "total_cost_usd": total_cost,
            "subset_path": str(cases_path),
            "pricing_basis": "list_sync",
            "pricing_path": pricing_path,
            "pricing_per_mtok": _pricing_rows(prices),
            "prompt_version": MULTISTEP_PROMPT_VERSION,
            "max_steps": MULTISTEP_MAX_STEPS,
            "max_tokens": MULTISTEP_MAX_TOKENS,
            "selected_arms": selected_arms,
            "arms": _arm_configs(selected_arms),
            "case_ids": [case.id for case in cases],
            "resume_history": resume_history,
            "note": (
                "multi-step traces only; secondary question; not a headline result"
            ),
        },
    )

    def write_outputs(final_status: str, reason: str | None) -> None:
        summaries = {
            arm: _summary(records[arm], arm=arm, n_tasks=len(cases))
            for arm in selected_arms
        }
        for arm in selected_arms:
            _atomic_write_json(
                run_dir / arm / "results.json",
                {
                    "arm": arm,
                    "model_id": multistep_model_id_for(arm),
                    "effort": multistep_effort_for(arm),
                    "prompt_version": MULTISTEP_PROMPT_VERSION,
                    "summary": summaries[arm],
                    "per_task": records[arm],
                },
            )
        _atomic_write_json(
            run_dir / "summary.json",
            {
                "run_id": run_id,
                "run_status": final_status,
                "abort_reason": reason,
                "total_cost_usd": total_cost,
                "arms": summaries,
            },
        )
        _atomic_write_json(
            run_dir / "results.json",
            {
                "run": {
                    "run_id": run_id,
                    "git_commit": git_commit,
                    "created_at": created_at,
                    "label": MULTISTEP_LABEL,
                    "dev_vs_final": MULTISTEP_DEV_VS_FINAL,
                    "run_status": final_status,
                    "abort_reason": reason,
                    "total_cost_usd": total_cost,
                    "pricing_basis": "list_sync",
                    "pricing_path": pricing_path,
                    "pricing_per_mtok": _pricing_rows(prices),
                    "prompt_version": MULTISTEP_PROMPT_VERSION,
                    "max_steps": MULTISTEP_MAX_STEPS,
                    "max_tokens": MULTISTEP_MAX_TOKENS,
                    "selected_arms": selected_arms,
                    "arms": _arm_configs(selected_arms),
                    "case_ids": [case.id for case in cases],
                },
                "arms": summaries,
            },
        )
        with (run_dir / "traces.jsonl").open("w", encoding="utf-8") as handle:
            for arm in selected_arms:
                for case in cases:
                    blob = trace_blobs.get((arm, case.id))
                    if blob is not None:
                        handle.write(json.dumps(blob, sort_keys=True) + "\n")

    def abort(message: str) -> None:
        nonlocal status, abort_reason
        status = "aborted"
        abort_reason = message
        _atomic_write_json(
            run_dir / "run.json",
            {
                "run_id": run_id,
                "git_commit": git_commit,
                "created_at": created_at,
                "label": MULTISTEP_LABEL,
                "dev_vs_final": MULTISTEP_DEV_VS_FINAL,
                "run_status": status,
                "abort_reason": abort_reason,
                "cost_ceiling_usd": cost_ceiling,
                "total_cost_usd": total_cost,
                "subset_path": str(cases_path),
                "pricing_basis": "list_sync",
                "pricing_path": pricing_path,
                "pricing_per_mtok": _pricing_rows(prices),
                "prompt_version": MULTISTEP_PROMPT_VERSION,
                "max_steps": MULTISTEP_MAX_STEPS,
                "max_tokens": MULTISTEP_MAX_TOKENS,
                "selected_arms": selected_arms,
                "arms": _arm_configs(selected_arms),
                "case_ids": [case.id for case in cases],
                "resume_history": resume_history,
            },
        )
        write_outputs(status, abort_reason)
        raise RuntimeError(message)

    for arm in selected_arms:
        agent = MultiStepAgent(
            model=multistep_model_id_for(arm),
            arm=arm,
            effort=multistep_effort_for(arm),
            client=client,
            price=prices[arm],
            cost_ceiling_usd=float(cost_ceiling),
        )
        checkpoint_dir = run_dir / arm / "checkpoints"
        for case in cases:
            checkpoint_path = checkpoint_dir / f"{case.id}.json"
            if checkpoint_path.exists():
                payload = json.loads(checkpoint_path.read_text())
                if payload.get("status") == "completed":
                    record = dict(payload["per_task"])
                    records[arm].append(record)
                    blob = payload.get("trace")
                    if isinstance(blob, dict):
                        trace_blobs[(arm, case.id)] = blob
                    continue
            if total_cost >= float(cost_ceiling):
                abort(
                    f"cost ceiling reached before {arm}/{case.id}: "
                    f"${total_cost:.6f} >= ${float(cost_ceiling):.6f}"
                )
            trace = agent.run(case.to_task(), starting_cost_usd=total_cost)
            record = _trace_record(trace, case)
            blob = {
                "run_id": run_id,
                "task_id": case.id,
                "split": case.split,
                "arm": arm,
                "model_id": trace.model_id,
                "effort": trace.effort,
                "trace": trace.to_dict(),
                "per_task": record,
            }
            checkpoint = {
                "run_id": run_id,
                "task_id": case.id,
                "arm": arm,
                "status": record["status"],
                "cost_usd": float(record["cost_usd"]),
                "per_task": record,
                "trace": blob,
            }
            _atomic_write_json(checkpoint_path, checkpoint)
            records[arm].append(record)
            trace_blobs[(arm, case.id)] = blob
            total_cost += float(record["cost_usd"])
            if trace.cost_ceiling_reached:
                abort(
                    f"cost ceiling reached after {arm}/{case.id}: "
                    f"${total_cost:.6f} >= ${float(cost_ceiling):.6f}"
                )

    status = "completed"
    _atomic_write_json(
        run_dir / "run.json",
        {
            "run_id": run_id,
            "git_commit": git_commit,
            "created_at": created_at,
            "label": MULTISTEP_LABEL,
            "dev_vs_final": MULTISTEP_DEV_VS_FINAL,
            "run_status": status,
            "abort_reason": None,
            "cost_ceiling_usd": cost_ceiling,
            "total_cost_usd": total_cost,
            "subset_path": str(cases_path),
            "pricing_basis": "list_sync",
            "pricing_path": pricing_path,
            "pricing_per_mtok": _pricing_rows(prices),
            "prompt_version": MULTISTEP_PROMPT_VERSION,
            "max_steps": MULTISTEP_MAX_STEPS,
            "max_tokens": MULTISTEP_MAX_TOKENS,
            "selected_arms": selected_arms,
            "arms": _arm_configs(selected_arms),
            "case_ids": [case.id for case in cases],
            "resume_history": resume_history,
        },
    )
    write_outputs(status, None)
    return {
        "run_id": run_id,
        "run_dir": str(run_dir),
        "run_status": status,
        "total_cost_usd": total_cost,
        "arms": {
            arm: _summary(records[arm], arm=arm, n_tasks=len(cases))
            for arm in selected_arms
        },
    }


def _parse_arms(raw: str) -> list[str]:
    if raw == "all":
        return list(MULTISTEP_ARMS)
    selected = [part.strip() for part in raw.split(",") if part.strip()]
    unknown = [arm for arm in selected if arm not in MULTISTEP_ARMS]
    if unknown:
        raise ValueError(f"unknown multi-step arm(s): {', '.join(unknown)}")
    return selected


def _api_key_status() -> str:
    return "PRESENT" if os.environ.get("ANTHROPIC_API_KEY") else "MISSING"


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 5 multi-step traces")
    parser.add_argument("--subset", default=MULTISTEP_SET_PATH)
    parser.add_argument("--out", default="results")
    parser.add_argument("--arms", default="all")
    parser.add_argument("--cost-ceiling", type=float, default=None)
    parser.add_argument("--pricing-file", default=None)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--cases", default="")
    parser.add_argument("--no-resume", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    try:
        arms = _parse_arms(args.arms)
    except ValueError as exc:
        parser.error(str(exc))
    cases = load_and_validate(Path(args.subset))
    if args.cases:
        wanted = [part.strip() for part in args.cases.split(",") if part.strip()]
        by_id = {case.id: case for case in cases}
        unknown_cases = [case_id for case_id in wanted if case_id not in by_id]
        if unknown_cases:
            parser.error(f"unknown case id(s): {', '.join(unknown_cases)}")
        cases = [case for case in cases if case.id in set(wanted)]
    plan = {
        "subset": args.subset,
        "cases": len(cases),
        "tune_tasks": sum(1 for case in cases if case.split == "tune"),
        "report_tasks": sum(1 for case in cases if case.split == "report"),
        "traces": len(cases) * len(arms),
        "arms": {
            arm: {
                "model_id": multistep_model_id_for(arm),
                "effort": multistep_effort_for(arm),
            }
            for arm in arms
        },
        "prompt_version": MULTISTEP_PROMPT_VERSION,
        "max_steps": MULTISTEP_MAX_STEPS,
        "max_tokens": MULTISTEP_MAX_TOKENS,
        "cost_ceiling_usd": args.cost_ceiling,
        "anthropic_api_key": _api_key_status(),
    }
    if args.dry_run:
        print(json.dumps(plan, indent=2))
        return
    if args.cost_ceiling is None or args.cost_ceiling <= 0:
        parser.error("--cost-ceiling is required for a live run")
    if _api_key_status() != "PRESENT":
        parser.error("ANTHROPIC_API_KEY is MISSING; refusing a live run")
    if not args.pricing_file:
        parser.error("--pricing-file is required")
    pricing = load_pricing_file(Path(args.pricing_file))
    try:
        result = run_multistep(
            Path(args.subset),
            Path(args.out),
            arms,
            pricing=pricing,
            cost_ceiling=args.cost_ceiling,
            run_id=args.run_id,
            resume=not args.no_resume,
            pricing_path=args.pricing_file,
            case_ids=[case.id for case in cases] if args.cases else None,
        )
    except RuntimeError as exc:
        print(json.dumps({"error": str(exc)}, indent=2))
        raise SystemExit(2) from exc
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
