"""Eval harness: run cases -> arm -> trace -> score -> results JSON.

Phase 2 scope: the hand-written mini-set only. Every result is labelled
``mini/smoke`` and excluded from headline numbers.

One command runs all three arms, or a single arm / case subset for debugging::

    PYTHONPATH=src python -m eval.harness --subset data/miniset.jsonl \
        --out results --arms all --cost-ceiling 1.00 \
        --pricing-file <explicit-pricing.json>

Cost comes from logged API usage at pinned list prices, never from estimates.
A live run requires an explicit per-run ceiling. Case-level checkpoints live
under the gitignored ``results/`` tree so an interrupted run can resume
without repeating completed ``(arm, case, repeat)`` entries.

Results layout (all under the gitignored output directory)::

    <out>/<run_id>/run.json                 combined run metadata + status
    <out>/<run_id>/results.json             combined results (all arms)
    <out>/<run_id>/summary.json             per-arm summaries
    <out>/<run_id>/<arm>/results.json       single-arm results contract
    <out>/<run_id>/<arm>/checkpoints/*.json case-level checkpoints

The single-arm ``results.json`` files preserve the Phase-1 results contract,
extended only with fields Phase 2 needs (run status/label, retries,
infrastructure retries, raw usage, format-failure breakdown). The combined
``run.json`` / ``results.json`` / ``summary.json`` files carry the selected-arm
configuration list instead of one ``arm``/``model_id`` pair; the contract-
shaped records always live in each ``<arm>/results.json``.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from agent.agent import AgentTrace, build_agent
from eval.config import (
    ARMS,
    EFFORT_PROVISIONAL,
    EFFORT_TRANSMITTED,
    MAX_TOKENS,
    MAX_TOKENS_PROVISIONAL,
    MATCH_SPEC_VERSION,
    MINI_SET_DEV_VS_FINAL,
    MINI_SET_LABEL,
    MODEL_ID_STATUS,
    PROMPT_VERSION,
    effort_for,
    model_id_for,
    pricing_for,
)
from eval.metrics import (
    PRICING_PER_MTOK,
    PricingError,
    TokenPrice,
    latency_stats,
    load_pricing_file,
    mean_ci,
    wilson_interval,
)
from eval.mini_set import MiniCase, load_and_validate

RETRY_RATE_DEFINITION = (
    "cases with retry_count > 0 / scored cases, per arm; "
    "mean_retries_per_case = total schema-repair retries / scored cases"
)
FORMAT_FAILURE_RATE_DEFINITION = (
    "cases with format_failure true / scored cases, per arm, broken down by "
    "no_tool_use, malformed_block, schema_validation_after_retries, refusal, "
    "truncated, and infrastructure_exhausted"
)

RESULTS_CONTRACT = {
    "run": [
        "run_id",
        "git_commit",
        "created_at",
        "dev_vs_final",
        "label",
        "run_status",
        "abort_reason",
        "arm",
        "model_id",
        "model_id_status",
        "effort",
        "effort_provisional",
        "effort_transmitted",
        "prompt_version",
        "match_spec_version",
        "repeats",
        "cost_ceiling_usd",
        "subset_path",
        "pricing_basis",
        "max_tokens",
        "max_tokens_provisional",
    ],
    "per_case": [
        "run_id",
        "task_id",
        "arm",
        "model_id",
        "effort",
        "prompt_version",
        "repeat",
        "exact",
        "partial",
        "name_only",
        "format_failure",
        "format_failure_kind",
        "schema_validation_failed",
        "schema_failure",
        "final_schema_valid",
        "retry_count",
        "infrastructure_retry_count",
        "validation_errors",
        "final_attempt_calls",
        "input_tokens",
        "output_tokens",
        "raw_usage",
        "cost_usd",
        "latency_s",
        "failure_reason",
    ],
    "summary": [
        "n_cases",
        "n_cases_attempted",
        "n_cases_completed",
        "n_repeats",
        "exact_match_mean",
        "exact_match_ci_low",
        "exact_match_ci_high",
        "partial_mean",
        "name_only_rate",
        "pass_at_k",
        "pass_at_k_ci_low",
        "pass_at_k_ci_high",
        "retry_rate",
        "mean_retries_per_case",
        "schema_failure_rate",
        "format_failure_rate",
        "format_failure_breakdown",
        "cost_usd_total",
        "cost_per_task",
        "cost_per_completed_task",
        "latency_p50_s",
        "latency_p95_s",
        "mini_set_only",
        "label",
        "dev_vs_final",
    ],
    "retry_rate": RETRY_RATE_DEFINITION,
    "format_failure_rate": FORMAT_FAILURE_RATE_DEFINITION,
}


class RunAborted(RuntimeError):
    """The per-run cost ceiling was reached; partial outputs were preserved."""

    def __init__(self, message: str, result: dict[str, Any]) -> None:
        super().__init__(message)
        self.result = result


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


def _safe_name(value: str) -> str:
    return "".join(
        character if character.isalnum() or character in "._-" else "_"
        for character in value
    )


def _block_breakdown(blocks: Sequence[Any]) -> tuple[bool, bool]:
    saw_tool_use = False
    malformed = False
    for block in blocks:
        if not isinstance(block, dict):
            malformed = True
            continue
        if block.get("type") != "tool_use":
            continue
        saw_tool_use = True
        name = block.get("name")
        arguments = block.get("input")
        if not isinstance(name, str) or name == "" or not isinstance(arguments, dict):
            malformed = True
    return saw_tool_use, malformed


def _score_trace(
    trace: AgentTrace,
    case: MiniCase,
) -> dict[str, Any]:
    """Score one trace, applying the schema-failure precedence rule."""

    from eval.scorer import score_case

    scored = score_case(trace.final_attempt_blocks, case.answers)
    exact = scored.exact
    partial = scored.partial
    name_only = scored.name_only

    kinds: list[str] = []
    if trace.schema_validation_failed:
        kinds.append("schema_validation_after_retries")
    if trace.failure_reason in {
        "refusal",
        "truncated",
        "infrastructure_exhausted",
        "api_error",
    }:
        kinds.append(trace.failure_reason)

    saw_tool_use, malformed = _block_breakdown(trace.final_attempt_blocks)
    if not saw_tool_use and not kinds:
        kinds.append("no_tool_use")
    if malformed:
        kinds.append("malformed_block")
    if scored.format_failure and not kinds:
        kinds.append("no_tool_use" if not saw_tool_use else "malformed_block")

    if trace.schema_validation_failed:
        # EVAL_DESIGN §4 precedence: a retry-exhausted schema failure scores 0
        # even if the emitted call happens to equal gold.
        exact = 0
        partial = 0.0
    if trace.failure_reason in {
        "refusal",
        "truncated",
        "infrastructure_exhausted",
        "api_error",
    }:
        # Symmetric failure policy: these events count as failures even if a
        # partial tool_use block happened to survive in the final message.
        exact = 0
        partial = 0.0

    format_failure = bool(
        kinds or scored.format_failure or trace.format_failure
    )
    # Keep order but remove duplicates.
    kinds = list(dict.fromkeys(kinds))
    return {
        "exact": exact,
        "partial": partial,
        "name_only": name_only,
        "format_failure": format_failure,
        "format_failure_kind": kinds,
    }


def _per_case_record(
    run_id: str,
    trace: AgentTrace,
    case: MiniCase,
    repeat: int,
    score: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "task_id": case.id,
        "arm": trace.arm,
        "model_id": trace.model_id,
        "effort": trace.effort,
        "prompt_version": trace.prompt_version,
        "repeat": repeat,
        "exact": int(score["exact"]),
        "partial": float(score["partial"]),
        "name_only": bool(score["name_only"]),
        "format_failure": bool(score["format_failure"]),
        "format_failure_kind": list(score["format_failure_kind"]),
        "schema_validation_failed": bool(trace.schema_validation_failed),
        "schema_failure": trace.final_schema_valid is False,
        "final_schema_valid": trace.final_schema_valid,
        "retry_count": int(trace.retry_count),
        "infrastructure_retry_count": int(trace.infrastructure_retry_count),
        "validation_errors": list(trace.validation_errors),
        "final_attempt_calls": list(trace.final_attempt_calls),
        "input_tokens": int(trace.input_tokens),
        "output_tokens": int(trace.output_tokens),
        "raw_usage": dict(trace.raw_usage),
        "cost_usd": float(trace.cost_usd),
        "latency_s": float(trace.latency_s),
        "failure_reason": trace.failure_reason,
    }


def _group_by_case(records: Sequence[Mapping[str, Any]]) -> dict[str, list[Mapping[str, Any]]]:
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for record in records:
        grouped.setdefault(str(record["task_id"]), []).append(record)
    return grouped


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _summary(
    records: Sequence[Mapping[str, Any]],
    *,
    n_cases_attempted: int,
) -> dict[str, Any]:
    grouped = _group_by_case(records)
    per_case_exact = [
        _mean([float(record["exact"]) for record in group])
        for group in grouped.values()
    ]
    per_case_partial = [
        _mean([float(record["partial"]) for record in group])
        for group in grouped.values()
    ]
    per_case_name_only = [
        _mean([1.0 if record["name_only"] else 0.0 for record in group])
        for group in grouped.values()
    ]
    per_case_retry = [
        1.0 if any(int(record["retry_count"]) > 0 for record in group) else 0.0
        for group in grouped.values()
    ]
    per_case_schema_failure = [
        1.0 if any(bool(record["schema_failure"]) for record in group) else 0.0
        for group in grouped.values()
    ]
    per_case_format_failure = [
        1.0 if any(bool(record["format_failure"]) for record in group) else 0.0
        for group in grouped.values()
    ]
    pass_at_k = [
        1.0 if any(int(record["exact"]) == 1 for record in group) else 0.0
        for group in grouped.values()
    ]

    n_cases = len(grouped)
    exact_mean = _mean(per_case_exact)
    ci_low, ci_high = mean_ci(per_case_exact)
    pass_rate = _mean(pass_at_k)
    pass_low, pass_high = wilson_interval(sum(pass_at_k), n_cases)

    breakdown: dict[str, int] = {}
    for record in records:
        for kind in record["format_failure_kind"]:
            breakdown[str(kind)] = breakdown.get(str(kind), 0) + 1

    total_cost = sum(float(record["cost_usd"]) for record in records)
    latencies = [float(record["latency_s"]) for record in records]
    stats = latency_stats(latencies)

    return {
        "n_cases": n_cases,
        "n_cases_attempted": n_cases_attempted,
        "n_cases_completed": n_cases,
        "n_repeats": len(records),
        "exact_match_mean": exact_mean,
        "exact_match_ci_low": ci_low,
        "exact_match_ci_high": ci_high,
        "partial_mean": _mean(per_case_partial),
        "name_only_rate": _mean(per_case_name_only),
        "pass_at_k": pass_rate,
        "pass_at_k_ci_low": pass_low,
        "pass_at_k_ci_high": pass_high,
        "retry_rate": _mean(per_case_retry),
        "mean_retries_per_case": (
            sum(int(record["retry_count"]) for record in records) / n_cases
            if n_cases
            else 0.0
        ),
        "schema_failure_rate": _mean(per_case_schema_failure),
        "format_failure_rate": _mean(per_case_format_failure),
        "format_failure_breakdown": breakdown,
        "cost_usd_total": total_cost,
        "cost_per_task": total_cost / n_cases if n_cases else 0.0,
        "cost_per_completed_task": total_cost / n_cases if n_cases else 0.0,
        "latency_p50_s": stats["p50"],
        "latency_p95_s": stats["p95"],
        "mini_set_only": True,
        "label": MINI_SET_LABEL,
        "dev_vs_final": MINI_SET_DEV_VS_FINAL,
    }


def _run_block(
    *,
    run_id: str,
    git_commit: str,
    created_at: str,
    arm: str,
    status: str,
    abort_reason: str | None,
    repeats: int,
    cost_ceiling: float,
    subset_path: str,
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "git_commit": git_commit,
        "created_at": created_at,
        "dev_vs_final": MINI_SET_DEV_VS_FINAL,
        "label": MINI_SET_LABEL,
        "run_status": status,
        "abort_reason": abort_reason,
        "arm": arm,
        "model_id": model_id_for(arm),
        "model_id_status": MODEL_ID_STATUS,
        "effort": effort_for(arm),
        "effort_provisional": EFFORT_PROVISIONAL,
        "effort_transmitted": EFFORT_TRANSMITTED,
        "prompt_version": PROMPT_VERSION,
        "match_spec_version": MATCH_SPEC_VERSION,
        "repeats": repeats,
        "cost_ceiling_usd": cost_ceiling,
        "subset_path": subset_path,
        "pricing_basis": "list_sync",
        "max_tokens": MAX_TOKENS,
        "max_tokens_provisional": MAX_TOKENS_PROVISIONAL,
    }


def _pricing_rows(prices: Mapping[str, TokenPrice]) -> dict[str, dict[str, float | None]]:
    """Record the exact list prices used, so cost is auditable after the run."""

    rows: dict[str, dict[str, float | None]] = {}
    for arm, price in prices.items():
        rows[model_id_for(arm)] = {
            "input": price.input,
            "output": price.output,
            "cache_read": price.cache_read,
            "cache_write": price.cache_write,
        }
    return rows


def _arm_config_rows() -> list[dict[str, Any]]:
    return [
        {
            "arm": arm,
            "model_id": model_id_for(arm),
            "model_id_status": MODEL_ID_STATUS,
            "effort": effort_for(arm),
            "effort_provisional": EFFORT_PROVISIONAL,
            "effort_transmitted": EFFORT_TRANSMITTED,
            "prompt_version": PROMPT_VERSION,
        }
        for arm in ARMS
    ]


def _write_outputs(
    *,
    run_dir: Path,
    run_id: str,
    git_commit: str,
    created_at: str,
    status: str,
    abort_reason: str | None,
    repeats: int,
    cost_ceiling: float,
    subset_path: str,
    arm_records: Mapping[str, Sequence[Mapping[str, Any]]],
    arm_summaries: Mapping[str, Mapping[str, Any]],
    total_cost: float,
    cases: Sequence[MiniCase],
    pricing_rows: Mapping[str, Mapping[str, Any]] | None = None,
    resume_history: Sequence[Mapping[str, Any]] | None = None,
    selected_arms: Sequence[str] | None = None,
) -> None:
    combined_arms: dict[str, Any] = {}
    for arm in ARMS:
        if arm not in arm_records:
            continue
        records = list(arm_records[arm])
        summary = dict(arm_summaries[arm])
        run_block = _run_block(
            run_id=run_id,
            git_commit=git_commit,
            created_at=created_at,
            arm=arm,
            status=status,
            abort_reason=abort_reason,
            repeats=repeats,
            cost_ceiling=cost_ceiling,
            subset_path=subset_path,
        )
        arm_payload = {
            "run": run_block,
            "summary": summary,
            "per_case": records,
        }
        _atomic_write_json(run_dir / arm / "results.json", arm_payload)
        combined_arms[arm] = arm_payload

    run_payload = {
        "run_id": run_id,
        "git_commit": git_commit,
        "created_at": created_at,
        "dev_vs_final": MINI_SET_DEV_VS_FINAL,
        "label": MINI_SET_LABEL,
        "run_status": status,
        "abort_reason": abort_reason,
        "cost_ceiling_usd": cost_ceiling,
        "total_cost_usd": total_cost,
        "repeats": repeats,
        "subset_path": subset_path,
        "match_spec_version": MATCH_SPEC_VERSION,
        "prompt_version": PROMPT_VERSION,
        "pricing_basis": "list_sync",
        "pricing_per_mtok": dict(pricing_rows or {}),
        "max_tokens": MAX_TOKENS,
        "max_tokens_provisional": MAX_TOKENS_PROVISIONAL,
        "resume_history": list(resume_history or []),
        "case_ids": [case.id for case in cases],
        "selected_arms": list(selected_arms or []),
        "arms": _arm_config_rows(),
        "note": "mini/smoke only; excluded from every headline number",
    }
    _atomic_write_json(run_dir / "run.json", run_payload)
    _atomic_write_json(
        run_dir / "summary.json",
        {
            "run_id": run_id,
            "label": MINI_SET_LABEL,
            "dev_vs_final": MINI_SET_DEV_VS_FINAL,
            "run_status": status,
            "abort_reason": abort_reason,
            "total_cost_usd": total_cost,
            "pricing_basis": "list_sync",
            "pricing_per_mtok": dict(pricing_rows or {}),
            "max_tokens": MAX_TOKENS,
            "max_tokens_provisional": MAX_TOKENS_PROVISIONAL,
            "selected_arms": list(selected_arms or []),
            "arm_configs": _arm_config_rows(),
            "arms": dict(arm_summaries),
        },
    )
    _atomic_write_json(
        run_dir / "results.json",
        {
            "run": {
                "run_id": run_id,
                "git_commit": git_commit,
                "created_at": created_at,
                "dev_vs_final": MINI_SET_DEV_VS_FINAL,
                "label": MINI_SET_LABEL,
                "run_status": status,
                "abort_reason": abort_reason,
                "match_spec_version": MATCH_SPEC_VERSION,
                "cost_ceiling_usd": cost_ceiling,
                "total_cost_usd": total_cost,
                "repeats": repeats,
                "pricing_basis": "list_sync",
                "pricing_per_mtok": dict(pricing_rows or {}),
                "max_tokens": MAX_TOKENS,
                "max_tokens_provisional": MAX_TOKENS_PROVISIONAL,
                "resume_history": list(resume_history or []),
                "selected_arms": list(selected_arms or []),
                "arms": _arm_config_rows(),
            },
            "arms": combined_arms,
        },
    )


def _load_checkpoint(
    path: Path,
    *,
    run_id: str,
    arm: str,
    case_id: str,
    repeat: int,
) -> dict[str, Any]:
    payload = json.loads(path.read_text())
    if (
        payload.get("run_id") != run_id
        or payload.get("arm") != arm
        or payload.get("task_id") != case_id
        or payload.get("repeat") != repeat
    ):
        raise RuntimeError(
            f"checkpoint {path} does not match ({run_id}, {arm}, {case_id}, {repeat})"
        )
    return payload


def run_arms(
    cases_path: Path | str,
    out_dir: Path | str,
    arms: Sequence[str] | None = None,
    *,
    client: Any = None,
    pricing: Mapping[str, TokenPrice] | None = None,
    cost_ceiling: float | None = None,
    case_ids: Sequence[str] | None = None,
    repeats: int = 1,
    run_id: str | None = None,
    resume: bool = True,
) -> dict[str, Any]:
    """Run selected arms over the mini-set and write traces + results JSON.

    Offline tests inject ``client`` and ``pricing``. A live call additionally
    requires ``ANTHROPIC_API_KEY`` in the environment and an explicit
    ``cost_ceiling``; the CLI enforces both.
    """

    if cost_ceiling is None or cost_ceiling <= 0:
        raise ValueError("a live run requires an explicit positive --cost-ceiling")
    if repeats < 1:
        raise ValueError("repeats must be >= 1")

    selected_arms = list(arms or ARMS)
    unknown_arms = [arm for arm in selected_arms if arm not in ARMS]
    if unknown_arms:
        raise ValueError(f"unknown arm(s): {', '.join(unknown_arms)}")

    table = dict(PRICING_PER_MTOK if pricing is None else pricing)
    if not table:
        raise PricingError(
            "no pinned list prices are configured; supply --pricing-file "
            "before a live run"
        )
    prices: dict[str, TokenPrice] = {}
    missing: list[str] = []
    for arm in selected_arms:
        try:
            prices[arm] = pricing_for(model_id_for(arm), table)
        except PricingError:
            missing.append(model_id_for(arm))
    if missing:
        raise PricingError(
            "no pinned list price for model(s): " + ", ".join(missing)
        )

    cases = load_and_validate(Path(cases_path))
    if case_ids:
        wanted = list(dict.fromkeys(case_ids))
        by_id = {case.id: case for case in cases}
        unknown = [case_id for case_id in wanted if case_id not in by_id]
        if unknown:
            raise ValueError(f"unknown case id(s): {', '.join(unknown)}")
        cases = [case for case in cases if case.id in set(wanted)]

    if run_id is None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        run_id = f"phase2-mini-{stamp}-{uuid.uuid4().hex[:8]}"
    run_dir = Path(out_dir) / run_id
    git_commit = _git_commit()
    created_at = _now_iso()
    status = "running"
    abort_reason: str | None = None
    total_cost = 0.0
    max_case_cost = 0.0
    prior_total_cost = 0.0
    arm_records: dict[str, list[dict[str, Any]]] = {}
    arm_summaries: dict[str, dict[str, Any]] = {}
    attempted_cases: dict[str, set[str]] = {arm: set() for arm in selected_arms}
    resume_history: list[dict[str, Any]] = []
    subset_path = str(cases_path)
    pricing_rows = _pricing_rows(prices)

    if run_dir.exists():
        if not resume:
            raise RuntimeError(f"run directory already exists: {run_dir}")
        run_json = run_dir / "run.json"
        if not run_json.exists():
            raise RuntimeError(f"run directory has no run.json: {run_dir}")
        existing = json.loads(run_json.read_text())
        if existing.get("run_status") == "completed":
            raise RuntimeError(
                f"run {run_id} is already completed; choose a new --run-id"
            )
        created_at = str(existing.get("created_at", created_at))
        git_commit = str(existing.get("git_commit", git_commit))
        prior_total_cost = float(existing.get("total_cost_usd", 0.0) or 0.0)
        existing_case_ids = existing.get("case_ids")
        if existing_case_ids is not None and list(existing_case_ids) != [
            case.id for case in cases
        ]:
            raise RuntimeError(
                "resume case selection does not match the original run; "
                "checkpoints must not be reordered"
            )
        existing_arms = existing.get("selected_arms")
        if existing_arms is not None and list(existing_arms) != selected_arms:
            raise RuntimeError(
                "resume arm selection does not match the original run"
            )
        resume_history = list(existing.get("resume_history", []))
        resume_history.append(
            {
                "resumed_at": _now_iso(),
                "previous_status": existing.get("run_status"),
                "previous_abort_reason": existing.get("abort_reason"),
                "previous_total_cost_usd": existing.get("total_cost_usd", 0.0),
            }
        )

    # Write run identity before the first API call so a process killed
    # mid-run still leaves a resumable run directory. On resume, the prior
    # abort record is preserved in resume_history.
    _atomic_write_json(
        run_dir / "run.json",
        {
            "run_id": run_id,
            "git_commit": git_commit,
            "created_at": created_at,
            "dev_vs_final": MINI_SET_DEV_VS_FINAL,
            "label": MINI_SET_LABEL,
            "run_status": "running",
            "abort_reason": None,
            "cost_ceiling_usd": cost_ceiling,
            "total_cost_usd": prior_total_cost,
            "repeats": repeats,
            "subset_path": subset_path,
            "match_spec_version": MATCH_SPEC_VERSION,
            "prompt_version": PROMPT_VERSION,
            "pricing_basis": "list_sync",
            "pricing_per_mtok": pricing_rows,
            "max_tokens": MAX_TOKENS,
            "max_tokens_provisional": MAX_TOKENS_PROVISIONAL,
            "resume_history": resume_history,
            "case_ids": [case.id for case in cases],
            "selected_arms": selected_arms,
            "arms": _arm_config_rows(),
            "note": "mini/smoke only; excluded from every headline number",
        },
    )

    def abort(message: str) -> None:
        nonlocal status, abort_reason
        status = "aborted"
        abort_reason = message
        partial_summaries: dict[str, dict[str, Any]] = {}
        for arm_name, partial_records in arm_records.items():
            partial_summaries[arm_name] = arm_summaries.get(arm_name) or _summary(
                partial_records,
                n_cases_attempted=len(attempted_cases.get(arm_name, set())),
            )
        _write_outputs(
            run_dir=run_dir,
            run_id=run_id,
            git_commit=git_commit,
            created_at=created_at,
            status=status,
            abort_reason=abort_reason,
            repeats=repeats,
            cost_ceiling=float(cost_ceiling),
            subset_path=subset_path,
            arm_records=arm_records,
            arm_summaries=partial_summaries,
            total_cost=total_cost,
            cases=cases,
            pricing_rows=pricing_rows,
            resume_history=resume_history,
            selected_arms=selected_arms,
        )
        raise RunAborted(
            message,
            {
                "run_id": run_id,
                "run_dir": str(run_dir),
                "label": MINI_SET_LABEL,
                "dev_vs_final": MINI_SET_DEV_VS_FINAL,
            },
        )

    for arm in selected_arms:
        agent = build_agent(arm, client=client, price=prices[arm])
        records: list[dict[str, Any]] = []
        arm_records[arm] = records
        for case in cases:
            for repeat in range(1, repeats + 1):
                checkpoint_path = (
                    run_dir
                    / arm
                    / "checkpoints"
                    / f"{_safe_name(case.id)}__r{repeat}.json"
                )
                if checkpoint_path.exists():
                    payload = _load_checkpoint(
                        checkpoint_path,
                        run_id=run_id,
                        arm=arm,
                        case_id=case.id,
                        repeat=repeat,
                    )
                    record = dict(payload["per_case"])
                    records.append(record)
                    attempted_cases[arm].add(case.id)
                    total_cost += float(record["cost_usd"])
                    max_case_cost = max(max_case_cost, float(record["cost_usd"]))
                    continue

                # Cost ceiling check happens before the first API call for
                # each subsequent case. The estimate is the largest observed
                # case cost (never a token guess).
                if total_cost >= float(cost_ceiling):
                    abort(
                        f"cost ceiling reached before {arm}/{case.id}/r{repeat}: "
                        f"${total_cost:.6f} >= ${float(cost_ceiling):.6f}"
                    )
                if (
                    max_case_cost > 0
                    and total_cost + max_case_cost >= float(cost_ceiling)
                ):
                    abort(
                        f"cost ceiling would be reached before {arm}/{case.id}/r{repeat}: "
                        f"${total_cost:.6f} + ${max_case_cost:.6f} >= "
                        f"${float(cost_ceiling):.6f}"
                    )

                attempted_cases[arm].add(case.id)
                trace = agent.run(case.to_task())
                score = _score_trace(trace, case)
                record = _per_case_record(run_id, trace, case, repeat, score)
                _atomic_write_json(
                    checkpoint_path,
                    {
                        "run_id": run_id,
                        "task_id": case.id,
                        "arm": arm,
                        "repeat": repeat,
                        "trace": trace.to_dict(),
                        "score": score,
                        "per_case": record,
                    },
                )
                records.append(record)
                total_cost += float(record["cost_usd"])
                max_case_cost = max(max_case_cost, float(record["cost_usd"]))

        arm_summaries[arm] = _summary(
            records,
            n_cases_attempted=len(attempted_cases[arm]),
        )

    _write_outputs(
        run_dir=run_dir,
        run_id=run_id,
        git_commit=git_commit,
        created_at=created_at,
        status="completed",
        abort_reason=None,
        repeats=repeats,
        cost_ceiling=float(cost_ceiling),
        subset_path=subset_path,
        arm_records=arm_records,
        arm_summaries=arm_summaries,
        total_cost=total_cost,
        cases=cases,
        pricing_rows=pricing_rows,
        resume_history=resume_history,
        selected_arms=selected_arms,
    )
    return {
        "run_id": run_id,
        "run_dir": str(run_dir),
        "run_status": "completed",
        "abort_reason": None,
        "label": MINI_SET_LABEL,
        "dev_vs_final": MINI_SET_DEV_VS_FINAL,
        "total_cost_usd": total_cost,
        "arms": {
            arm: {
                "summary": arm_summaries[arm],
                "results_path": str(run_dir / arm / "results.json"),
            }
            for arm in selected_arms
        },
    }


def run_subset(
    cases_path: Path,
    out_dir: Path,
    baseline: bool = False,
    *,
    arms: Sequence[str] | None = None,
    client: Any = None,
    pricing: Mapping[str, TokenPrice] | None = None,
    cost_ceiling: float | None = None,
    case_ids: Sequence[str] | None = None,
    repeats: int = 1,
    run_id: str | None = None,
    resume: bool = True,
) -> dict[str, Any]:
    """Run one arm over one subset and return its summary.

    Kept as the Phase-1 compatibility entry point; Phase 2 adds the multi-arm
    :func:`run_arms`.
    """

    selected = ["sonnet-direct"] if baseline else list(arms or ["haiku-agent"])
    result = run_arms(
        cases_path,
        out_dir,
        selected,
        client=client,
        pricing=pricing,
        cost_ceiling=cost_ceiling,
        case_ids=case_ids,
        repeats=repeats,
        run_id=run_id,
        resume=resume,
    )
    return result["arms"][selected[0]]["summary"]


def _parse_arms(raw: str, baseline: bool) -> list[str]:
    if baseline:
        if raw != "all":
            raise ValueError("--baseline and --arms cannot both select arms")
        return ["sonnet-direct"]
    if raw == "all":
        return list(ARMS)
    selected = [part.strip() for part in raw.split(",") if part.strip()]
    unknown = [arm for arm in selected if arm not in ARMS]
    if unknown:
        raise ValueError(f"unknown arm(s): {', '.join(unknown)}")
    return selected


def _api_key_status() -> str:
    return "PRESENT" if os.environ.get("ANTHROPIC_API_KEY") else "MISSING"


def main() -> None:
    parser = argparse.ArgumentParser(description="Agent eval harness (Phase 2 mini-set)")
    parser.add_argument("--subset", required=True, help="JSONL case file")
    parser.add_argument("--out", default="results", help="output directory")
    parser.add_argument(
        "--baseline",
        action="store_true",
        help="Sonnet-5.5 direct-prompt baseline only, no agent loop",
    )
    parser.add_argument(
        "--gate",
        action="store_true",
        help="fail (exit 2) if below baselines/main.json",
    )
    parser.add_argument(
        "--arms",
        default="all",
        help="comma-separated arms, or 'all' (default: all)",
    )
    parser.add_argument(
        "--cases",
        default="",
        help="comma-separated case ids for debugging (default: all)",
    )
    parser.add_argument("--repeats", type=int, default=1, help="repeats per case")
    parser.add_argument(
        "--cost-ceiling",
        type=float,
        default=None,
        help="required per-run cost ceiling in USD for a live run",
    )
    parser.add_argument(
        "--pricing-file",
        default=None,
        help="JSON mapping model id -> pinned per-MTok list prices",
    )
    parser.add_argument("--run-id", default=None, help="reuse this run id to resume")
    parser.add_argument(
        "--no-resume",
        action="store_true",
        help="refuse to reuse an existing run directory",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="validate cases and print the run plan without calling the API",
    )
    args = parser.parse_args()

    cases = load_and_validate(Path(args.subset))
    if args.cases:
        wanted = [part.strip() for part in args.cases.split(",") if part.strip()]
        by_id = {case.id: case for case in cases}
        unknown = [case_id for case_id in wanted if case_id not in by_id]
        if unknown:
            parser.error(f"unknown case id(s): {', '.join(unknown)}")
        cases = [case for case in cases if case.id in set(wanted)]
    selected_arms = _parse_arms(args.arms, args.baseline)

    plan = {
        "subset": args.subset,
        "cases": len(cases),
        "case_ids": [case.id for case in cases],
        "label": MINI_SET_LABEL,
        "arms": selected_arms,
        "model_ids": {arm: model_id_for(arm) for arm in selected_arms},
        "model_id_status": MODEL_ID_STATUS,
        "effort": {arm: effort_for(arm) for arm in selected_arms},
        "effort_provisional": EFFORT_PROVISIONAL,
        "prompt_version": PROMPT_VERSION,
        "match_spec_version": MATCH_SPEC_VERSION,
        "max_tokens": MAX_TOKENS,
        "max_tokens_provisional": MAX_TOKENS_PROVISIONAL,
        "repeats": args.repeats,
        "cost_ceiling_usd": args.cost_ceiling,
        "anthropic_api_key": _api_key_status(),
        "pricing_file": args.pricing_file,
    }
    if args.dry_run:
        print(json.dumps(plan, indent=2))
        return

    if args.cost_ceiling is None or args.cost_ceiling <= 0:
        parser.error("--cost-ceiling is required for a live run")
    if _api_key_status() != "PRESENT":
        parser.error(
            "ANTHROPIC_API_KEY is MISSING; refusing a live run "
            "(the harness never asks for or prints a secret)"
        )

    pricing: dict[str, TokenPrice] | None = None
    if args.pricing_file:
        pricing = load_pricing_file(Path(args.pricing_file))
    elif not PRICING_PER_MTOK:
        parser.error(
            "no pinned list prices are configured; supply --pricing-file "
            "(see the Phase-2 pause report)"
        )

    try:
        result = run_arms(
            Path(args.subset),
            Path(args.out),
            selected_arms,
            pricing=pricing,
            cost_ceiling=args.cost_ceiling,
            case_ids=[case.id for case in cases] if args.cases else None,
            repeats=args.repeats,
            run_id=args.run_id,
            resume=not args.no_resume,
        )
    except RunAborted as exc:
        print(json.dumps({"error": str(exc), **exc.result}, indent=2))
        raise SystemExit(2) from exc
    except PricingError as exc:
        parser.error(str(exc))

    print(json.dumps(result, indent=2))
    if args.gate:
        raise NotImplementedError("Phase 7: implement the CI gate comparison")


if __name__ == "__main__":
    main()
