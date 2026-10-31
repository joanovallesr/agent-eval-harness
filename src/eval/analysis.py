"""Dry-run the full Phase-3 analysis pipeline on a results directory.

This module reads a run's combined ``results.json`` (never raw dataset rows),
runs the paired estimator for the primary and secondary comparisons, and
writes an aggregate analysis JSON. The primary comparison is
``haiku-agent`` vs ``sonnet-direct``; ``haiku-direct`` is secondary and gets
intervals only, with no pass/fail verdict.

The output is a dev analysis draft, not a pre-registration and not a held-out
result.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping

from eval.mini_set import CANDIDATE_TOOLS
from eval.metrics import analyse_paired, failure_mode_counts, pilot_cost_gate


def _load_run(run_dir: Path) -> dict[str, Any]:
    path = run_dir / "results.json"
    if not path.exists():
        raise FileNotFoundError(f"missing {path}")
    payload = json.loads(path.read_text())
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _arm_records(
    results: Mapping[str, Any],
    arm: str,
) -> list[Mapping[str, Any]]:
    arms = results.get("arms")
    if not isinstance(arms, Mapping) or arm not in arms:
        raise ValueError(f"run has no arm {arm!r}")
    records = arms[arm].get("per_case")
    if not isinstance(records, list):
        raise ValueError(f"arm {arm!r} has no per_case list")
    return records


def _tool_map_from_cases(path: Path) -> dict[str, str]:
    mapping: dict[str, str] = {}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            task_id = row.get("id")
            tool = row.get("tool_name")
            if isinstance(task_id, str) and isinstance(tool, str) and tool:
                mapping[task_id] = tool
    return mapping


def _spend_projection(
    *,
    cost_by_arm: Mapping[str, float],
    k_by_arm: Mapping[str, int],
    n_t: int,
    cap: float,
    spent_before: float,
    reserve: float,
) -> dict[str, Any]:
    remaining = cap - spent_before - reserve
    by_c: dict[str, Any] = {}
    for C in (6, 7, 8):
        arms = {
            arm: C * n_t * int(k_by_arm[arm]) * float(cost_by_arm[arm])
            for arm in cost_by_arm
        }
        spend = sum(arms.values())
        by_c[str(C)] = {
            "test_spend": spend,
            "per_arm_test_spend": arms,
            "remaining_after_required_reserve": remaining,
            "fits": spend <= remaining,
        }
    return {
        "cap": cap,
        "spent_before": spent_before,
        "reserve": reserve,
        "remaining_after_required_reserve": remaining,
        "n_t": n_t,
        "k_by_arm": dict(k_by_arm),
        "cost_per_case_by_arm": dict(cost_by_arm),
        "by_C": by_c,
    }


def analyse_run(
    run_dir: Path,
    *,
    cases_path: Path | None = None,
    resamples: int = 2000,
    seed: int = 42,
    delta: float = 0.05,
    assumed_gap: float = 0.0,
    k: int | None = None,
    k_by_arm: Mapping[str, int] | None = None,
    cap: float = 20.0,
    spent_before: float | None = None,
    reserve: float | None = None,
) -> dict[str, Any]:
    results = _load_run(run_dir)
    tool_by_case = _tool_map_from_cases(cases_path) if cases_path else {}

    agent = _arm_records(results, "haiku-agent")
    sonnet = _arm_records(results, "sonnet-direct")
    haiku_direct = _arm_records(results, "haiku-direct")

    budget_ok = None
    spend = None
    spend_k = dict(k_by_arm or {})
    if k is not None:
        for arm in ("haiku-agent", "haiku-direct", "sonnet-direct"):
            spend_k.setdefault(arm, k)
    if spent_before is not None and reserve is not None and spend_k:
        arm_costs = {
            arm: (
                sum(float(record.get("cost_usd", 0.0)) for record in records)
                / len(records)
                if records
                else 0.0
            )
            for arm, records in (
                ("haiku-agent", agent),
                ("haiku-direct", haiku_direct),
                ("sonnet-direct", sonnet),
            )
        }
        # n_t comes from sizing, so compute sizing once without a budget
        # constraint first.
        provisional = analyse_paired(
            agent,
            sonnet,
            tool_by_case=tool_by_case,
            label="haiku-agent vs sonnet-direct",
            resamples=resamples,
            seed=seed,
            delta=delta,
            assumed_gap=assumed_gap,
            excluded_tools=CANDIDATE_TOOLS,
        )
        n_t = (provisional.get("sizing") or {}).get("n_t")
        if isinstance(n_t, int):
            spend = _spend_projection(
                cost_by_arm=arm_costs,
                k_by_arm=spend_k,
                n_t=n_t,
                cap=cap,
                spent_before=spent_before,
                reserve=reserve,
            )
            budget_ok = {
                C: bool(spend["by_C"][str(C)]["fits"]) for C in (6, 7, 8)
            }

    primary = analyse_paired(
        agent,
        sonnet,
        tool_by_case=tool_by_case,
        label="haiku-agent vs sonnet-direct (PRIMARY)",
        resamples=resamples,
        seed=seed,
        delta=delta,
        assumed_gap=assumed_gap,
        budget_ok=budget_ok,
        excluded_tools=CANDIDATE_TOOLS,
    )
    secondary = analyse_paired(
        haiku_direct,
        sonnet,
        tool_by_case=tool_by_case,
        label="haiku-direct vs sonnet-direct (SECONDARY, intervals only)",
        resamples=resamples,
        seed=seed,
        delta=delta,
        assumed_gap=assumed_gap,
        include_sizing=False,
    )

    run_block = results.get("run", {})
    arm_configs = run_block.get("arms") or []
    run_contract = {
        "run_id": run_block.get("run_id"),
        "git_commit": run_block.get("git_commit"),
        "label": run_block.get("label"),
        "dev_vs_final": run_block.get("dev_vs_final"),
        "model_ids": {
            str(row.get("arm")): row.get("model_id")
            for row in arm_configs
            if isinstance(row, dict)
        },
        "efforts": {
            str(row.get("arm")): row.get("effort")
            for row in arm_configs
            if isinstance(row, dict)
        },
        "prompt_versions": {
            str(row.get("arm")): row.get("prompt_version")
            for row in arm_configs
            if isinstance(row, dict)
        },
        "match_spec_version": run_block.get("match_spec_version"),
        "pricing_basis": run_block.get("pricing_basis"),
        "max_tokens": run_block.get("max_tokens"),
    }
    failure_counts = {
        "haiku-agent": failure_mode_counts(agent),
        "haiku-direct": failure_mode_counts(haiku_direct),
        "sonnet-direct": failure_mode_counts(sonnet),
    }
    # Counts of cases per tool, not tools above a threshold.
    per_tool_case_counts: dict[str, int] = {}
    for record in agent:
        if str(record.get("status", "completed")) != "completed":
            continue
        tool = str(record.get("tool_name") or tool_by_case.get(str(record["task_id"]), ""))
        if tool:
            per_tool_case_counts[tool] = per_tool_case_counts.get(tool, 0) + 1

    return {
        "analysis_version": "phase3-dry-run-v1",
        "run_id": run_block.get("run_id"),
        "run_status": run_block.get("run_status"),
        "label": run_block.get("label"),
        "dev_vs_final": run_block.get("dev_vs_final"),
        "git_commit": run_block.get("git_commit"),
        "primary_comparison": "haiku-agent vs sonnet-direct",
        "secondary_comparison": "haiku-direct vs sonnet-direct",
        "run_contract": run_contract,
        "primary": primary,
        "secondary": secondary,
        "cost_ratio_gate": pilot_cost_gate(primary["aggregate_cost_ratio"]),
        "sizing_inputs": primary.get("sizing", {}),
        "failure_modes": failure_counts,
        "per_tool_case_counts": dict(sorted(per_tool_case_counts.items())),
        "subset_metadata": run_block.get("subset_metadata", {}),
        "spend_projection": spend,
        "notes": [
            "dev/dry-run only; C and n_t are not locked here",
            "secondary comparisons carry intervals only, no pass/fail",
            "case-level bootstrap is a sensitivity check; the tool-clustered "
            "t-interval governs",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase-3 paired analysis dry run")
    parser.add_argument("--run-dir", required=True, help="results/<run_id>")
    parser.add_argument("--cases", default=None, help="case JSONL for tool names")
    parser.add_argument("--out", default=None, help="output analysis JSON path")
    parser.add_argument("--resamples", type=int, default=2000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--delta", type=float, default=0.05)
    parser.add_argument("--assumed-gap", type=float, default=0.0)
    parser.add_argument("--k", type=int, default=None)
    parser.add_argument(
        "--k-by-arm",
        default="",
        help="comma-separated arm=k overrides for the spend projection",
    )
    parser.add_argument("--cap", type=float, default=20.0)
    parser.add_argument("--spent-before", type=float, default=None)
    parser.add_argument("--reserve", type=float, default=None)
    args = parser.parse_args()
    k_by_arm: dict[str, int] = {}
    for part in args.k_by_arm.split(","):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            parser.error(f"--k-by-arm expects arm=k, got {part!r}")
        arm, value = (piece.strip() for piece in part.split("=", 1))
        k_by_arm[arm] = int(value)

    analysis = analyse_run(
        Path(args.run_dir),
        cases_path=Path(args.cases) if args.cases else None,
        resamples=args.resamples,
        seed=args.seed,
        delta=args.delta,
        assumed_gap=args.assumed_gap,
        k=args.k,
        k_by_arm=k_by_arm or None,
        cap=args.cap,
        spent_before=args.spent_before,
        reserve=args.reserve,
    )
    text = json.dumps(analysis, indent=2, sort_keys=True) + "\n"
    if args.out:
        path = Path(args.out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    else:
        print(text)


if __name__ == "__main__":
    main()
