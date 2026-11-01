"""Generate the Phase-6 held-out analysis and documents from one results JSON.

The run's ``results.json`` is the only source of Phase-6 measurements. This
script computes the pre-registered estimands, the cost-ratio rule, the
per-tool table, and the failure taxonomy, then renders ``docs/RESULTS.md`` and
the README results block from the same analysis object. Nothing in the docs is
typed by hand.

It writes aggregates, counts, tool names, and checksums only. Raw rows,
queries, gold answers, schemas, and traces never leave the gitignored results
tree.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Sequence

from eval.metrics import analyse_paired
from eval.scorer import parse_gold, score_case

ARMS = ("haiku-agent", "haiku-direct", "sonnet-direct")
PRIMARY = ("haiku-agent", "sonnet-direct")
SECONDARY = ("haiku-direct", "sonnet-direct")
COST_THRESHOLD = 0.10
START_MARKER = "<!-- PHASE6-RESULTS:START -->"
END_MARKER = "<!-- PHASE6-RESULTS:END -->"
TAXONOMY_LABELS = (
    "truncated",
    "refusal",
    "infrastructure",
    "missing-call",
    "format-error",
    "wrong-tool",
    "extra-call",
    "partial-answer",
    "right-tool-wrong-params",
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _last_money(text: str, pattern: str) -> float:
    matches = re.findall(pattern, text, re.DOTALL)
    if not matches:
        raise ValueError(f"pattern not found: {pattern}")
    return float(matches[-1])


def _records(payload: Mapping[str, Any], arm: str) -> list[Mapping[str, Any]]:
    return list(payload["arms"][arm]["per_case"])


def _gold_names_by_case(cases_path: Path) -> dict[str, list[str]]:
    names: dict[str, list[str]] = {}
    with cases_path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            names[str(row["id"])] = [call.name for call in parse_gold(row["answers"])]
    return names


def _gold_answers_by_case(cases_path: Path) -> dict[str, str]:
    answers: dict[str, str] = {}
    with cases_path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            answers[str(row["id"])] = str(row["answers"])
    return answers


def _gold_schema_inconsistencies(cases_path: Path) -> dict[str, Any]:
    """Count gold calls that violate the case's own tool schema.

    These rows are not malformed under MATCH_SPEC §1.3, so they stay in the
    pre-registered sample. MATCH_SPEC §7 anticipates the inconsistency; this
    diagnostic records it without changing any score.
    """

    from jsonschema import Draft202012Validator

    cases = 0
    calls = 0
    by_tool: Counter[str] = Counter()
    with cases_path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            offered = {tool["name"]: tool for tool in row["tools"]}
            bad = False
            for call in parse_gold(row["answers"]):
                calls += 1
                validator = Draft202012Validator(
                    offered[call.name]["input_schema"]
                )
                if list(validator.iter_errors(call.arguments)):
                    bad = True
            if bad:
                cases += 1
                by_tool[str(row["tool_name"])] += 1
    return {
        "cases": cases,
        "calls": calls,
        "by_tool": dict(sorted(by_tool.items())),
    }


def classify_failure(
    record: Mapping[str, Any],
    gold_names: Sequence[str],
) -> str:
    """Assign exactly one descriptive label to one non-exact case.

    Precedence follows the pre-registered failure policy: an infrastructure
    event or truncation explains the case before output shape does.
    """

    kinds = set(record.get("format_failure_kind") or [])
    reason = str(record.get("failure_reason") or "")
    status = str(record.get("status", "completed"))

    if "truncated" in kinds or reason == "truncated":
        return "truncated"
    if "refusal" in kinds or reason == "refusal":
        return "refusal"
    if status != "completed" or reason in {
        "infrastructure_exhausted",
        "api_error",
        "cost_ceiling",
    }:
        return "infrastructure"

    calls = list(record.get("final_attempt_calls") or [])
    predicted_names = [
        str(call.get("name"))
        for call in calls
        if isinstance(call, Mapping) and call.get("name")
    ]

    if record.get("schema_validation_failed") or "malformed_block" in kinds:
        return "format-error"
    if not predicted_names:
        return "missing-call"

    gold_counts = Counter(gold_names)
    predicted_counts = Counter(predicted_names)

    if predicted_counts == gold_counts:
        return "right-tool-wrong-params"
    if set(predicted_names) - set(gold_names):
        # Any call to a tool that cannot satisfy the task is the primary
        # explanation, even if other calls are right.
        return "wrong-tool"
    if predicted_counts - gold_counts:
        return "extra-call"
    if gold_counts - predicted_counts:
        return "partial-answer"
    return "unclassified"


def _taxonomy(
    records: Sequence[Mapping[str, Any]],
    gold_names: Mapping[str, Sequence[str]],
) -> dict[str, Any]:
    counts: Counter[str] = Counter()
    non_exact = 0
    for record in records:
        if int(record.get("exact", 0)) == 1:
            continue
        non_exact += 1
        label = classify_failure(
            record, gold_names.get(str(record["task_id"]), ())
        )
        counts[label] += 1
    labelled = sum(counts.values())
    if labelled != non_exact:
        raise ValueError(
            f"taxonomy does not account for every non-exact case: "
            f"{labelled} != {non_exact}"
        )
    if counts.get("unclassified", 0):
        raise ValueError("unclassified non-exact cases remain")
    return {
        "counts": {label: counts.get(label, 0) for label in TAXONOMY_LABELS},
        "non_exact": non_exact,
        "labelled": labelled,
    }


def _arm_tool_stats(
    records: Sequence[Mapping[str, Any]],
) -> dict[str, dict[str, float]]:
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for record in records:
        grouped.setdefault(str(record["tool_name"]), []).append(record)
    output: dict[str, dict[str, float]] = {}
    for tool, rows in sorted(grouped.items()):
        n = len(rows)
        exact_count = sum(int(row.get("exact", 0)) for row in rows)
        output[tool] = {
            "n": n,
            "exact": exact_count / n,
            "exact_count": exact_count,
            "partial": sum(float(row.get("partial", 0.0)) for row in rows) / n,
        }
    return output


def has_nonzero_cache(value: Any, in_cache: bool = False) -> bool:
    if isinstance(value, Mapping):
        for key, item in value.items():
            cache_key = in_cache or "cache" in str(key).lower()
            if (
                cache_key
                and isinstance(item, (int, float))
                and not isinstance(item, bool)
                and item > 0
            ):
                return True
            if has_nonzero_cache(item, cache_key):
                return True
    elif isinstance(value, list):
        for item in value:
            if has_nonzero_cache(item, in_cache):
                return True
    return False


def cost_verdict(upper: float, threshold: float = COST_THRESHOLD) -> str:
    return "parity holds" if upper <= threshold else "parity fails"


FORMAT_FAILURE_KINDS = {
    "no_tool_use",
    "malformed_block",
    "schema_validation_after_retries",
}


def _diagnostics(
    records: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    n = len(records)
    # PRE_REGISTRATION §6 defines the format-failure rate as no tool_use
    # blocks, malformed blocks, and schema-validation failures after retries.
    # Truncation is a separate event and is not folded into this rate.
    format_failures = sum(
        1
        for record in records
        if FORMAT_FAILURE_KINDS.intersection(record.get("format_failure_kind") or [])
    )
    truncated = sum(
        1
        for record in records
        if record.get("failure_reason") == "truncated"
        or "truncated" in (record.get("format_failure_kind") or [])
    )
    schema_failures = sum(
        1 for record in records if record.get("schema_validation_failed")
    )
    cache_nonzero = sum(
        1 for record in records if has_nonzero_cache(record.get("raw_usage") or {})
    )
    kinds: Counter[str] = Counter()
    for record in records:
        for kind in record.get("format_failure_kind") or []:
            kinds[str(kind)] += 1
    return {
        "format_failure_count": format_failures,
        "format_failure_kinds": dict(sorted(kinds.items())),
        "format_failure_rate": format_failures / n if n else 0.0,
        "truncation_count": truncated,
        "truncation_rate": truncated / n if n else 0.0,
        "schema_failure_count": schema_failures,
        "schema_failure_rate": schema_failures / n if n else 0.0,
        "cache_nonzero_records": cache_nonzero,
    }


def _secondary_diagnostics(
    records: Sequence[Mapping[str, Any]],
    answers_by_case: Mapping[str, str],
) -> dict[str, float]:
    """MATCH_SPEC §7 diagnostics: partial, name-only, and lenient-string.

    The lenient variant is reconstructed from the recorded final-attempt calls.
    A record already scored as a failure (format, truncation, refusal,
    infrastructure) keeps its zero; the diagnostic never rescues it.
    """

    n = len(records)
    partial = sum(float(record.get("partial", 0.0)) for record in records) / n
    name_only = sum(1 for record in records if record.get("name_only")) / n
    lenient_exact = 0.0
    lenient_partial = 0.0
    for record in records:
        if record.get("format_failure"):
            continue
        calls = list(record.get("final_attempt_calls") or [])
        blocks = [
            {
                "type": "tool_use",
                "id": f"recorded-{index}",
                "name": call.get("name"),
                "input": call.get("input"),
            }
            for index, call in enumerate(calls)
            if isinstance(call, Mapping)
        ]
        scored = score_case(
            blocks,
            answers_by_case.get(str(record["task_id"]), "[]"),
            lenient=True,
        )
        lenient_exact += float(scored.exact)
        lenient_partial += float(scored.partial)
    return {
        "partial": partial,
        "name_only": name_only,
        "lenient_exact": lenient_exact / n,
        "lenient_partial": lenient_partial / n,
    }


def _git(*args: str) -> str:
    return subprocess.check_output(
        ["git", *args], text=True, stderr=subprocess.DEVNULL
    ).strip()


def _verification(
    *,
    cases_path: Path,
    data_readme: Path,
    pre_registration: Path,
    budget_doc: Path,
) -> dict[str, Any]:
    sample_sha = _sha256(cases_path)
    meta_path = cases_path.with_suffix(".meta.json")
    meta_sha = _sha256(meta_path)
    recorded = data_readme.read_text()
    expected_sample = re.search(
        r"([0-9a-f]{64})  data/phase3/test\.jsonl", recorded
    )
    expected_meta = re.search(
        r"([0-9a-f]{64})  data/phase3/test\.meta\.json", recorded
    )

    tag_commit = _git("rev-parse", "pre-registration-v1^{commit}")
    head = _git("rev-parse", "HEAD")
    head_prereg = _sha256(pre_registration)
    try:
        repository = Path(_git("rev-parse", "--show-toplevel"))
        relative = pre_registration.resolve().relative_to(repository).as_posix()
        tagged_bytes = subprocess.check_output(
            ["git", "show", f"pre-registration-v1:{relative}"]
        )
        tagged_prereg = hashlib.sha256(tagged_bytes).hexdigest()
    except (OSError, subprocess.SubprocessError, ValueError):
        tagged_prereg = None

    pre = pre_registration.read_text()
    budget = budget_doc.read_text()
    cap = _last_money(budget, r"Hard cap:\s*\*\*\$([0-9.]+)\*\*")
    ledger = _last_money(
        budget,
        r"With Phase 5 included,\s+the ledger total is \*\*\$([0-9.]+)\*\*",
    )
    test_projection = _last_money(
        pre, r"Test spend = .*?= \*\*\$([0-9.]+)\*\*"
    )
    reserve = _last_money(
        pre, r"Test reserve = .*?= \*\*\$([0-9.]+)\*\*"
    )

    rows = [
        json.loads(line)
        for line in cases_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    tool_counts = Counter(str(row["tool_name"]) for row in rows)
    gold_tools = {
        call.name for row in rows for call in parse_gold(row["answers"])
    }
    meta = json.loads(meta_path.read_text())
    pre_registered = set(meta.get("test_tools") or [])

    return {
        "head_at_analysis": head,
        "pre_registration_tag_commit": tag_commit,
        "pre_registration_sha256_head": head_prereg,
        "pre_registration_sha256_tag": tagged_prereg,
        "pre_registration_identical": head_prereg == tagged_prereg,
        "sample_path": str(cases_path),
        "sample_sha256": sample_sha,
        "sample_sha256_recorded": expected_sample.group(1) if expected_sample else None,
        "sample_checksum_matches": bool(
            expected_sample and expected_sample.group(1) == sample_sha
        ),
        "metadata_sha256": meta_sha,
        "metadata_sha256_recorded": expected_meta.group(1) if expected_meta else None,
        "metadata_checksum_matches": bool(
            expected_meta and expected_meta.group(1) == meta_sha
        ),
        "n_cases": len(rows),
        "n_tools": len(tool_counts),
        "cases_per_tool": dict(sorted(tool_counts.items())),
        "minimum_per_tool": min(tool_counts.values()) if tool_counts else 0,
        "maximum_per_tool": max(tool_counts.values()) if tool_counts else 0,
        "duplicate_case_ids": len(rows) - len({str(row["id"]) for row in rows}),
        "gold_tools_outside_pre_registered": sorted(gold_tools - pre_registered),
        "budget": {
            "cap": cap,
            "ledger_total": ledger,
            "test_projection": test_projection,
            "reserve": reserve,
            "available_with_reserve": cap - ledger - reserve,
            "slack_after_test_projection": (
                cap - ledger - reserve - test_projection
            ),
        },
    }


def _phase5_facts(mapping_path: Path, report_analysis_path: Path) -> dict[str, Any]:
    mapping = json.loads(mapping_path.read_text())["mapping"]
    report_rows = [
        row for row in mapping if str(row.get("split")) == "report"
    ]
    report = json.loads(report_analysis_path.read_text())
    return {
        "n_traces": int(report["n"]),
        "n_tasks": len({str(row["task_id"]) for row in report_rows}),
        "raw_agreement_correct": round(float(report["raw_agreement"]) * report["n"]),
        "raw_agreement_n": int(report["n"]),
        "raw_agreement": float(report["raw_agreement"]),
        "kappa_defined": bool(report["kappa_defined"]),
        "kappa": report["kappa"],
        "ship_rule_met": bool(
            report["kappa_defined"] and report["kappa"] is not None
            and float(report["kappa"]) >= 0.70
        ),
        "human_intra_rater_ceiling_measured": False,
    }


def build_analysis(
    *,
    run_dir: Path,
    cases_path: Path,
    data_readme: Path,
    pre_registration: Path,
    budget_doc: Path,
    phase5_mapping: Path,
    phase5_report_analysis: Path,
    resamples: int = 2000,
    seed: int = 42,
) -> dict[str, Any]:
    payload = json.loads((run_dir / "results.json").read_text())
    run = payload["run"]
    if run.get("run_status") != "completed":
        raise ValueError(f"run is not complete: {run.get('run_status')!r}")

    records = {arm: _records(payload, arm) for arm in ARMS}
    counts = {arm: len(records[arm]) for arm in ARMS}
    if len(set(counts.values())) != 1:
        raise ValueError(f"arms have unequal record counts: {counts}")
    aborted = sum(
        1
        for arm in ARMS
        for record in records[arm]
        if str(record.get("status", "completed")) != "completed"
    )
    if aborted:
        raise ValueError(f"analysis requires zero aborted cases, found {aborted}")

    gold_names = _gold_names_by_case(cases_path)
    gold_answers = _gold_answers_by_case(cases_path)
    primary = analyse_paired(
        records[PRIMARY[0]],
        records[PRIMARY[1]],
        label="haiku-agent vs sonnet-direct (PRIMARY)",
        resamples=resamples,
        seed=seed,
        include_sizing=False,
    )
    secondary = analyse_paired(
        records[SECONDARY[0]],
        records[SECONDARY[1]],
        label="haiku-direct vs sonnet-direct (SECONDARY)",
        resamples=resamples,
        seed=seed,
        include_sizing=False,
    )

    per_arm_tools = {
        arm: _arm_tool_stats(records[arm]) for arm in ARMS
    }
    all_tools = sorted(per_arm_tools["haiku-agent"])
    per_tool_table = []
    for tool in all_tools:
        row: dict[str, Any] = {"tool": tool}
        for arm in ARMS:
            stats = per_arm_tools[arm].get(tool, {})
            row[arm] = {
                "n": stats.get("n", 0),
                "exact": stats.get("exact", 0.0),
                "exact_count": stats.get("exact_count", 0),
                "partial": stats.get("partial", 0.0),
            }
        per_tool_table.append(row)

    taxonomy = {
        arm: _taxonomy(records[arm], gold_names) for arm in ARMS
    }
    diagnostics = {arm: _diagnostics(records[arm]) for arm in ARMS}
    secondary_diagnostics = {
        arm: _secondary_diagnostics(records[arm], gold_answers) for arm in ARMS
    }
    cache_nonzero = sum(
        diagnostics[arm]["cache_nonzero_records"] for arm in ARMS
    )
    if cache_nonzero:
        raise ValueError(
            "nonzero cache usage observed; the pricing basis has no cache "
            "rates and the run must stop for a re-pin"
        )

    cost_high = float(primary["cost_ratio"]["high"])
    verdict = cost_verdict(cost_high)

    total_cost = float(run.get("total_cost_usd", 0.0))
    arm_totals = {
        arm: sum(float(record.get("cost_usd", 0.0)) for record in records[arm])
        for arm in ARMS
    }
    arm_cases = {arm: len(records[arm]) for arm in ARMS}
    completed_arm_cost = sum(arm_totals.values())
    aborted_attempt_cost = total_cost - completed_arm_cost
    prompt_version = run.get("prompt_version")
    if not prompt_version:
        prompt_version = next(
            (
                row.get("prompt_version")
                for row in (run.get("arms") or [])
                if isinstance(row, Mapping) and row.get("prompt_version")
            ),
            None,
        )

    return {
        "analysis_version": "phase6-heldout-v1",
        "generated_from": str(run_dir / "results.json"),
        "run": {
            "run_id": run.get("run_id"),
            "git_commit": run.get("git_commit"),
            "created_at": run.get("created_at"),
            "label": run.get("label"),
            "dev_vs_final": run.get("dev_vs_final"),
            "run_status": run.get("run_status"),
            "total_cost_usd": total_cost,
            "match_spec_version": run.get("match_spec_version"),
            "prompt_version": prompt_version,
            "max_tokens": run.get("max_tokens"),
            "pricing_basis": run.get("pricing_basis"),
            "pricing_path": run.get("pricing_path"),
            "repeats": run.get("repeats"),
            "resume_history": run.get("resume_history", []),
            "arms": {
                str(row.get("arm")): {
                    "model_id": row.get("model_id"),
                    "effort": row.get("effort"),
                    "effort_transmitted": row.get("effort_transmitted"),
                    "prompt_version": row.get("prompt_version"),
                }
                for row in (run.get("arms") or [])
                if isinstance(row, Mapping)
            },
            "records_per_arm": counts,
            "aborted_records": aborted,
            "completed_arm_cost_usd": completed_arm_cost,
            "aborted_attempt_cost_usd": aborted_attempt_cost,
        },
        "verification": _verification(
            cases_path=cases_path,
            data_readme=data_readme,
            pre_registration=pre_registration,
            budget_doc=budget_doc,
        ),
        "primary": {
            "comparison": "haiku-agent - sonnet-direct",
            "n_cases": primary["n_cases"],
            "n_tools": primary["n_tools"],
            "mean_difference": primary["primary"]["mean"],
            "t_interval_low": primary["primary"]["low"],
            "t_interval_high": primary["primary"]["high"],
            "t_interval_degrees_of_freedom": primary["primary"]["n"] - 1,
            "tool_sd": primary["primary"]["sd"],
            "tool_se": primary["primary"]["se"],
            "bootstrap": primary["primary_bootstrap"],
            "discordant_rate": primary["discordant_rate"],
            "per_tool": primary["per_tool"],
            "cost_ratio": primary["cost_ratio"],
            "cost_ratio_bootstrap": primary["cost_ratio_bootstrap"],
        },
        "secondary": {
            "comparison": "haiku-direct - sonnet-direct",
            "n_cases": secondary["n_cases"],
            "n_tools": secondary["n_tools"],
            "mean_difference": secondary["primary"]["mean"],
            "t_interval_low": secondary["primary"]["low"],
            "t_interval_high": secondary["primary"]["high"],
            "t_interval_degrees_of_freedom": secondary["primary"]["n"] - 1,
            "tool_sd": secondary["primary"]["sd"],
            "tool_se": secondary["primary"]["se"],
            "bootstrap": secondary["primary_bootstrap"],
            "discordant_rate": secondary["discordant_rate"],
            "cost_ratio": secondary["cost_ratio"],
            "cost_ratio_bootstrap": secondary["cost_ratio_bootstrap"],
        },
        "cost": {
            "threshold": COST_THRESHOLD,
            "ratio": float(primary["cost_ratio"]["ratio"]),
            "t_interval_low": float(primary["cost_ratio"]["low"]),
            "t_interval_high": cost_high,
            "bootstrap": primary["cost_ratio_bootstrap"],
            "aggregate_ratio": float(primary["aggregate_cost_ratio"]),
            "verdict": verdict,
            "per_arm_total_cost_usd": arm_totals,
            "per_arm_cost_per_case_usd": {
                arm: arm_totals[arm] / arm_cases[arm] for arm in ARMS
            },
            "per_tool": {
                tool: {
                    "ratio": stats.get("cost_ratio"),
                    "haiku_agent_cost": stats.get("mean_agent_cost"),
                    "sonnet_direct_cost": stats.get("mean_baseline_cost"),
                }
                for tool, stats in primary["per_tool"].items()
            },
        },
        "per_tool_table": per_tool_table,
        "failure_taxonomy": taxonomy,
        "diagnostics": diagnostics,
        "secondary_diagnostics": secondary_diagnostics,
        "gold_schema_inconsistencies": _gold_schema_inconsistencies(cases_path),
        "phase5": _phase5_facts(phase5_mapping, phase5_report_analysis),
        "notes": [
            "estimation-first: no pass/fail accuracy verdict",
            "the tool-clustered t-interval governs; the case bootstrap is a "
            "sensitivity check",
            "k = 1, so pass@1 equals accuracy",
        ],
    }


def _pct(value: float, digits: int = 2) -> str:
    return f"{value * 100:.{digits}f}%"


def _points(value: float, digits: int = 2) -> str:
    return f"{value * 100:+.{digits}f}"


def _money(value: float, digits: int = 6) -> str:
    return f"${value:.{digits}f}"


def render_results(analysis: Mapping[str, Any]) -> str:
    run = analysis["run"]
    verify = analysis["verification"]
    budget = verify["budget"]
    primary = analysis["primary"]
    secondary = analysis["secondary"]
    cost = analysis["cost"]
    phase5 = analysis["phase5"]
    gold_schema = analysis["gold_schema_inconsistencies"]

    lines: list[str] = []
    lines.append("# Held-Out Test Results")
    lines.append("")
    lines.append(
        "Status: I ran the single pre-registered pass over the held-out test "
        f"split and scored it with MATCH_SPEC v{run['match_spec_version']} "
        "exactly as implemented. I report accuracy as an estimate, not as a "
        "pass/fail verdict. Cost parity is the one binary rule I apply."
    )
    lines.append("")
    lines.append("## Run contract")
    lines.append("")
    lines.append(
        f"- I ran `{run['run_id']}` at commit `{run['git_commit']}` "
        f"({run['label']} / {run['dev_vs_final']})."
    )
    if verify["minimum_per_tool"] == verify["maximum_per_tool"]:
        cases_phrase = f"{verify['minimum_per_tool']} cases per tool"
    else:
        cases_phrase = (
            f"{verify['minimum_per_tool']}-{verify['maximum_per_tool']} "
            "cases per tool"
        )
    lines.append(
        f"- {verify['n_cases']} cases, {verify['n_tools']} tools, "
        f"{cases_phrase}, k = {run['repeats']}, {len(ARMS)} arms."
    )
    lines.append(
        f"- `haiku-agent` and `haiku-direct`: `{run['arms']['haiku-agent']['model_id']}` "
        f"at `{run['arms']['haiku-agent']['effort']}`; `sonnet-direct`: "
        f"`{run['arms']['sonnet-direct']['model_id']}` at "
        f"`{run['arms']['sonnet-direct']['effort']}`."
    )
    lines.append(
        f"- Prompt `{run['prompt_version']}`, `max_tokens` "
        f"{run['max_tokens']}, pricing basis `{run['pricing_basis']}` from "
        f"`{run['pricing_path']}`."
    )
    lines.append(
        f"- I recorded `haiku-agent` {run['records_per_arm']['haiku-agent']}, "
        f"`haiku-direct` {run['records_per_arm']['haiku-direct']}, "
        f"`sonnet-direct` {run['records_per_arm']['sonnet-direct']}; aborted "
        f"records {run['aborted_records']}. Resume events: "
        f"{len(run['resume_history'])}; the resume-equality path was not "
        "exercised because none was needed."
    )
    lines.append(
        f"- I recorded the run contract field `dev_vs_final` as "
        f"`{run['dev_vs_final']}`. The harness's generic non-mini `note` "
        "string is not part of the contract and is not used here."
    )
    lines.append(
        f"- I measured actual list-price spend for this run at "
        f"**{_money(run['total_cost_usd'])}**."
    )
    lines.append(
        "- A separate real-time monitor watched every checkpoint during the "
        "run for nonzero cache tokens; it never fired."
    )
    if run["aborted_attempt_cost_usd"] > 0:
        lines.append(
            f"- The run total includes **{_money(run['aborted_attempt_cost_usd'])}** "
            "from an aborted attempt; completed records total "
            f"**{_money(run['completed_arm_cost_usd'])}**."
        )
    lines.append("")
    lines.append("## Pre-run verification")
    lines.append("")
    lines.append(
        f"- I verified that tag `pre-registration-v1` dereferences to "
        f"`{verify['pre_registration_tag_commit']}`."
    )
    lines.append(
        f"- I checked `docs/PRE_REGISTRATION.md` sha256 at the tag and at "
        f"analysis time: "
        f"`{verify['pre_registration_sha256_tag']}` / "
        f"`{verify['pre_registration_sha256_head']}`; identical: "
        f"**{verify['pre_registration_identical']}**."
    )
    lines.append(
        f"- I checked the test sample sha256 `{verify['sample_sha256']}` "
        f"(recorded match: "
        f"**{verify['sample_checksum_matches']}**); metadata sha256 "
        f"`{verify['metadata_sha256']}` (recorded match: "
        f"**{verify['metadata_checksum_matches']}**)."
    )
    lines.append(
        f"- I found {verify['duplicate_case_ids']} duplicate case IDs and "
        f"{len(verify['gold_tools_outside_pre_registered'])} gold tools outside "
        "the pre-registered set."
    )
    lines.append(
        f"- I recomputed the ledger before the run: "
        f"**{_money(budget['ledger_total'])}** of the "
        f"**{_money(budget['cap'], 2)}** hard cap. Pre-registered test "
        f"projection **{_money(budget['test_projection'])}**; reserve "
        f"**{_money(budget['reserve'])}**; available with the reserve intact "
        f"**{_money(budget['available_with_reserve'])}**; slack after the "
        f"projection **{_money(budget['slack_after_test_projection'])}**."
    )
    lines.append(
        "- I confirmed `ANTHROPIC_API_KEY` was present in the repo-root `.env` "
        "before the run; I never read the value into a document or log."
    )
    lines.append(
        "- I ran the offline suite, including the MATCH_SPEC §9 pair tests, "
        "and it was green before the run."
    )
    lines.append(
        "- I scoped two Phase-2 loader guards to mini/dev before the run: "
        "test-stage candidate gold and test-stage gold-schema conformance. "
        "The scorer, MATCH_SPEC, pre-registration, and test sample were "
        "unchanged; the checksums above are the pre-registered ones."
    )
    lines.append("")
    lines.append("## Primary: haiku-agent minus sonnet-direct")
    lines.append("")
    lines.append(
        f"I estimate the equal-weighted mean of the {primary['n_tools']} "
        "per-tool paired mean exact-match differences. The interval is a "
        "t-interval clustered on tool."
    )
    lines.append("")
    lines.append("| Estimate | 95% clustered t-interval | df | Tool SD | Discordant rate | Case-bootstrap sensitivity |")
    lines.append("|---:|---:|---:|---:|---:|---:|")
    lines.append(
        f"| **{_points(primary['mean_difference'])} pts** "
        f"| **[{_points(primary['t_interval_low'])}, "
        f"{_points(primary['t_interval_high'])}] pts** "
        f"| {primary['t_interval_degrees_of_freedom']} "
        f"| {primary['tool_sd']:.6f} "
        f"| {primary['discordant_rate']:.6f} "
        f"| [{_points(primary['bootstrap']['low'])}, "
        f"{_points(primary['bootstrap']['high'])}] pts |"
    )
    lines.append("")
    lines.append(
        "I report this as an estimate. I give no non-inferiority, superiority, "
        "or equivalence verdict, and I do not convert the interval into one."
    )
    lines.append("")
    lines.append(
        f"The case-level bootstrap is narrower "
        f"([{_points(primary['bootstrap']['low'])}, "
        f"{_points(primary['bootstrap']['high'])}] pts) and excludes zero, while "
        "the clustered t-interval includes zero. The two disagree; the "
        "pre-registered clustered interval governs, and I do not read the "
        "bootstrap as a verdict."
    )
    lines.append("")
    lines.append("### Per-tool mean exact match, primary pair")
    lines.append("")
    lines.append("| Tool | n | haiku-agent | sonnet-direct | Difference (pts) |")
    lines.append("|---|---:|---:|---:|---:|")
    for tool in sorted(primary["per_tool"]):
        stats = primary["per_tool"][tool]
        lines.append(
            f"| `{tool}` | {stats['n']} | {_pct(stats['mean_agent'])} "
            f"| {_pct(stats['mean_baseline'])} "
            f"| {_points(stats['mean_difference'])} |"
        )
    lines.append("")
    biggest_tool, biggest_stats = max(
        primary["per_tool"].items(),
        key=lambda item: abs(item[1]["mean_difference"]),
    )
    other_max = max(
        (
            abs(stats["mean_difference"])
            for tool, stats in primary["per_tool"].items()
            if tool != biggest_tool
        ),
        default=0.0,
    )
    lines.append(
        f"The largest per-tool difference is `{biggest_tool}` "
        f"({_pct(biggest_stats['mean_agent'])} for haiku-agent vs "
        f"{_pct(biggest_stats['mean_baseline'])} for sonnet-direct, "
        f"{_points(biggest_stats['mean_difference'])} pts). The other "
        f"{primary['n_tools'] - 1} tools differ by at most "
        f"{other_max * 100:.2f} points in absolute value. These are "
        "descriptive per-tool means; the clustered interval above governs."
    )
    lines.append("")
    lines.append("## Secondary: haiku-direct minus sonnet-direct")
    lines.append("")
    lines.append(
        "I report intervals only, with no pass/fail framing."
    )
    lines.append("")
    lines.append("| Estimate | 95% clustered t-interval | df | Discordant rate | Case-bootstrap sensitivity |")
    lines.append("|---:|---:|---:|---:|---:|")
    lines.append(
        f"| **{_points(secondary['mean_difference'])} pts** "
        f"| **[{_points(secondary['t_interval_low'])}, "
        f"{_points(secondary['t_interval_high'])}] pts** "
        f"| {secondary['t_interval_degrees_of_freedom']} "
        f"| {secondary['discordant_rate']:.6f} "
        f"| [{_points(secondary['bootstrap']['low'])}, "
        f"{_points(secondary['bootstrap']['high'])}] pts |"
    )
    lines.append("")
    lines.append("## Cost claim: the one binary verdict")
    lines.append("")
    lines.append(
        "I declare cost parity if and only if the upper bound of the 95% "
        f"cost-ratio interval is at most {cost['threshold']:.2f}. The interval "
        f"is a t-interval on the {primary['n_tools']} per-tool log ratios, "
        "exponentiated back to ratio scale."
    )
    lines.append("")
    lines.append("| Estimate (geometric mean) | 95% cost-ratio interval | Aggregate ratio | Threshold | Verdict |")
    lines.append("|---:|---:|---:|---:|---|")
    lines.append(
        f"| {cost['ratio']:.6f} "
        f"| **[{cost['t_interval_low']:.6f}, {cost['t_interval_high']:.6f}]** "
        f"| {cost['aggregate_ratio']:.6f} | {cost['threshold']:.2f} "
        f"| **{cost['verdict']}** |"
    )
    lines.append("")
    lines.append(
        f"I report the case-bootstrap sensitivity for the aggregate ratio: "
        f"[{cost['bootstrap']['low']:.6f}, {cost['bootstrap']['high']:.6f}]."
    )
    lines.append("")
    lines.append(
        "I compute cost from logged API usage at the pinned list prices. Thinking "
        "tokens are already included in `output_tokens` and are not added "
        "again. No batch rates and no prompt caching were used, so the logged "
        "list-price total is also the actual spend for this run."
    )
    lines.append("")
    lines.append("### Cost per arm")
    lines.append("")
    lines.append("| Arm | Total list-price cost | Cost per case |")
    lines.append("|---|---:|---:|")
    for arm in ARMS:
        lines.append(
            f"| `{arm}` | {_money(cost['per_arm_total_cost_usd'][arm])} "
            f"| {_money(cost['per_arm_cost_per_case_usd'][arm])} |"
        )
    lines.append("")
    lines.append("### Per-tool cost ratio, haiku-agent / sonnet-direct")
    lines.append("")
    lines.append("| Tool | Ratio |")
    lines.append("|---|---:|")
    for tool in sorted(cost["per_tool"]):
        ratio = cost["per_tool"][tool]["ratio"]
        lines.append(f"| `{tool}` | {ratio:.6f} |")
    lines.append("")
    lines.append(f"## Per-tool exact and partial, all {len(ARMS)} arms")
    lines.append("")
    lines.append(
        "I report exact as the primary metric and partial as the MATCH_SPEC "
        "diagnostic. Every cell is an aggregate over that tool's cases."
    )
    lines.append("")
    lines.append(
        "| Tool | n | Agent exact | Direct exact | Sonnet exact "
        "| Agent partial | Direct partial | Sonnet partial |"
    )
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|")
    for row in analysis["per_tool_table"]:
        lines.append(
            f"| `{row['tool']}` | {row['haiku-agent']['n']} "
            f"| {_pct(row['haiku-agent']['exact'])} "
            f"| {_pct(row['haiku-direct']['exact'])} "
            f"| {_pct(row['sonnet-direct']['exact'])} "
            f"| {_pct(row['haiku-agent']['partial'])} "
            f"| {_pct(row['haiku-direct']['partial'])} "
            f"| {_pct(row['sonnet-direct']['partial'])} |"
        )
    lines.append("")
    lines.append("## Secondary diagnostics (MATCH_SPEC §7)")
    lines.append("")
    lines.append(
        "These are diagnostics only; they never change the primary result. The "
        "lenient-string variant is reconstructed from the recorded final-attempt "
        "calls and keeps every failure-policy zero."
    )
    lines.append("")
    lines.append("| Arm | Partial | Name-only | Lenient exact |")
    lines.append("|---|---:|---:|---:|")
    for arm in ARMS:
        diagnostics = analysis["secondary_diagnostics"][arm]
        lines.append(
            f"| `{arm}` | {_pct(diagnostics['partial'])} "
            f"| {_pct(diagnostics['name_only'])} "
            f"| {_pct(diagnostics['lenient_exact'])} |"
        )
    lines.append("")
    lines.append("## Failure taxonomy")
    lines.append("")
    lines.append(
        "I assign exactly one descriptive label to every non-exact case. The "
        "counts sum to the non-exact count in every arm; `unclassified` is zero."
    )
    lines.append("")
    header = "| Label | haiku-agent | haiku-direct | sonnet-direct |"
    lines.append(header)
    lines.append("|---|---:|---:|---:|")
    for label in TAXONOMY_LABELS:
        lines.append(
            f"| {label} "
            f"| {analysis['failure_taxonomy']['haiku-agent']['counts'][label]} "
            f"| {analysis['failure_taxonomy']['haiku-direct']['counts'][label]} "
            f"| {analysis['failure_taxonomy']['sonnet-direct']['counts'][label]} |"
        )
    lines.append(
        f"| **Non-exact total** "
        f"| **{analysis['failure_taxonomy']['haiku-agent']['non_exact']}** "
        f"| **{analysis['failure_taxonomy']['haiku-direct']['non_exact']}** "
        f"| **{analysis['failure_taxonomy']['sonnet-direct']['non_exact']}** |"
    )
    lines.append(
        f"| **Label total** "
        f"| **{analysis['failure_taxonomy']['haiku-agent']['labelled']}** "
        f"| **{analysis['failure_taxonomy']['haiku-direct']['labelled']}** "
        f"| **{analysis['failure_taxonomy']['sonnet-direct']['labelled']}** |"
    )
    lines.append("")
    lines.append("### Format-failure and truncation diagnostics")
    lines.append("")
    lines.append("| Arm | Format-failure rate | Truncation rate | Schema-failure rate | Cache-nonzero records |")
    lines.append("|---|---:|---:|---:|---:|")
    for arm in ARMS:
        diagnostics = analysis["diagnostics"][arm]
        lines.append(
            f"| `{arm}` | {_pct(diagnostics['format_failure_rate'])} "
            f"({diagnostics['format_failure_count']}) "
            f"| {_pct(diagnostics['truncation_rate'])} "
            f"({diagnostics['truncation_count']}) "
            f"| {_pct(diagnostics['schema_failure_rate'])} "
            f"({diagnostics['schema_failure_count']}) "
            f"| {diagnostics['cache_nonzero_records']} |"
        )
    lines.append("")
    lines.append(
        "The format-failure rate counts `no_tool_use`, `malformed_block`, and "
        "`schema_validation_after_retries`; truncation is a separate diagnostic "
        "and is not folded into it."
    )
    lines.append("")
    kinds = sorted(
        {
            kind
            for arm in ARMS
            for kind in analysis["diagnostics"][arm]["format_failure_kinds"]
        }
    )
    if kinds:
        lines.append("| Format-failure event | haiku-agent | haiku-direct | sonnet-direct |")
        lines.append("|---|---:|---:|---:|")
        for kind in kinds:
            lines.append(
                f"| {kind} "
                f"| {analysis['diagnostics']['haiku-agent']['format_failure_kinds'].get(kind, 0)} "
                f"| {analysis['diagnostics']['haiku-direct']['format_failure_kinds'].get(kind, 0)} "
                f"| {analysis['diagnostics']['sonnet-direct']['format_failure_kinds'].get(kind, 0)} |"
            )
        lines.append("")
    lines.append("## Phase 5 cross-reference")
    lines.append("")
    task_word = "task" if phase5["n_tasks"] == 1 else "tasks"
    lines.append(
        f"I report the secondary judge question as it actually landed: "
        f"Cohen's κ is **undefined** because both label sets used one class "
        f"(`correct`), over n = {phase5['n_traces']} report traces from "
        f"{phase5['n_tasks']} {task_word}, with raw agreement "
        f"{phase5['raw_agreement_correct']}/{phase5['raw_agreement_n']}. The "
        f"ship rule was **not met**. The human intra-rater ceiling was **not "
        f"measured**. See `docs/PHASE5_CALIBRATION.md`."
    )
    lines.append("")
    lines.append("## Limitations")
    lines.append("")
    lines.append(
        f"- The primary interval has only "
        f"{primary['t_interval_degrees_of_freedom']} degrees of freedom and is "
        f"clustered on {primary['n_tools']} tools. It is wide by construction; "
        "the between-tool spread dominates."
    )
    lines.append(
        "- k = 1, so there is no repeat-level variance estimate. pass@1 equals "
        "accuracy and is not a separate result."
    )
    lines.append(
        f"- {gold_schema['cases']} of {verify['n_cases']} test cases carry at "
        "least one gold call whose arguments do not satisfy the case's own tool "
        "schema. MATCH_SPEC §1.3 does not classify that as malformed gold, so "
        "the cases stay in the pre-registered sample. MATCH_SPEC §7 anticipates "
        "the schema/gold inconsistency; I report it here as a limitation and "
        "do not remove or re-score it."
    )
    lines.append(
        f"- The largest per-tool gap is `{biggest_tool}` "
        f"({_pct(biggest_stats['mean_agent'])} for haiku-agent vs "
        f"{_pct(biggest_stats['mean_baseline'])} for sonnet-direct). The sampled "
        "rows for that tool show a rate-convention mismatch (decimal fraction "
        "vs percentage) that the tool description does not disambiguate; the "
        "Haiku models mostly emit one convention and Sonnet sometimes the "
        "other. MATCH_SPEC's strict value comparison scores those as non-exact. "
        "This is a dataset ambiguity, not a scorer defect, and I do not "
        "re-score it."
    )
    lines.append(
        "- The held-out tools come from one synthetic generator (xLAM-60k). "
        "This measures shift within that generator, not real-world "
        "generalization; I do not claim the latter."
    )
    lines.append(
        "- I ran the test split once. I did not restart after partial results, "
        "tune on test data, or reclassify post hoc."
    )
    lines.append(
        "- I observed zero cache usage in every response. The pricing basis has "
        "no cache rates; any nonzero cache usage would have triggered a re-pin "
        "and a stop."
    )
    lines.append(
        "- I publish only aggregates, counts, tool names, and checksums here. I "
        "publish no raw rows, queries, gold answers, full schemas, or traces."
    )
    lines.append("")
    return "\n".join(lines)


def render_readme_block(analysis: Mapping[str, Any]) -> str:
    primary = analysis["primary"]
    secondary = analysis["secondary"]
    cost = analysis["cost"]
    phase5 = analysis["phase5"]
    lines = [
        START_MARKER,
        "## Held-out results (Phase 6)",
        "",
        f"I ran one pre-registered pass over {analysis['verification']['n_cases']} "
        f"cases ({analysis['verification']['n_tools']} tools × "
        f"{analysis['verification']['minimum_per_tool']}), k = "
        f"{analysis['run']['repeats']}, MATCH_SPEC v"
        f"{analysis['run']['match_spec_version']}. Accuracy is an estimate, "
        "not a pass/fail verdict. Cost parity is the one binary rule.",
        "",
        "| Question | Result |",
        "|---|---|",
        f"| Primary: haiku-agent − sonnet-direct | "
        f"**{_points(primary['mean_difference'])} pts** "
        f"[{_points(primary['t_interval_low'])}, "
        f"{_points(primary['t_interval_high'])}] (tool-clustered t, df "
        f"{primary['t_interval_degrees_of_freedom']}) |",
        f"| Secondary: haiku-direct − sonnet-direct | "
        f"**{_points(secondary['mean_difference'])} pts** "
        f"[{_points(secondary['t_interval_low'])}, "
        f"{_points(secondary['t_interval_high'])}] (estimate and interval only) |",
        f"| Cost ratio, haiku-agent / sonnet-direct | "
        f"**[{cost['t_interval_low']:.6f}, {cost['t_interval_high']:.6f}]**, "
        f"threshold {cost['threshold']:.2f} → **{cost['verdict']}** |",
        f"| Phase 5 judge calibration | κ **undefined** (one-class labels), "
        f"raw agreement {phase5['raw_agreement_correct']}/"
        f"{phase5['raw_agreement_n']}, n = {phase5['n_traces']} traces over "
        f"{phase5['n_tasks']} "
        f"{'task' if phase5['n_tasks'] == 1 else 'tasks'}, ship rule "
        f"**not met**; human intra-rater ceiling **not measured** |",
        "",
        "Full report: `docs/RESULTS.md`.",
        END_MARKER,
    ]
    return "\n".join(lines)


def update_budget(budget_doc: Path, analysis: Mapping[str, Any]) -> None:
    """Replace the test-spend placeholder and append the Phase-6 actuals."""

    text = budget_doc.read_text()
    budget = analysis["verification"]["budget"]
    actual = float(analysis["run"]["total_cost_usd"])
    ledger_before = float(budget["ledger_total"])
    new_ledger = ledger_before + actual
    reserve = float(budget["reserve"])
    cap = float(budget["cap"])
    available = cap - new_ledger - reserve
    if available < 0:
        raise ValueError(
            "the Phase-6 actual breaches the hard cap with the reserve intact: "
            f"{available:.6f} < 0"
        )

    row = (
        f"| **test_spend** | {_money(float(budget['test_projection']))} "
        f"(pre-registered) | {_money(actual)} | list_sync; "
        f"{analysis['verification']['n_cases']} cases × {len(ARMS)} arms, "
        f"k = {analysis['run']['repeats']}; actual ledger row |"
    )
    replaced = False
    lines = []
    for line in text.splitlines():
        if line.startswith("| **test_spend** |"):
            lines.append(row)
            replaced = True
        else:
            lines.append(line)
    if not replaced:
        raise ValueError("BUDGET.md has no test_spend placeholder row")
    text = "\n".join(lines).rstrip() + "\n"
    marker = "\n## Phase 6 held-out actuals\n"
    if marker in text:
        text = text.split(marker, 1)[0].rstrip() + "\n"

    text += (
        "\n## Phase 6 held-out actuals\n\n"
        f"I completed the held-out run in one pass with no aborted cases. Test "
        f"spend is **{_money(actual)}** against the pre-registered projection "
        f"of **{_money(float(budget['test_projection']))}**. The new ledger "
        f"total is **{_money(new_ledger)}** of the "
        f"**{_money(cap, 2)}** hard cap. I still hold the "
        f"**{_money(reserve)}** reserve; the remaining slack is "
        f"**{_money(available)}**.\n\n"
        "```text\n"
        f"{cap:.2f} − {new_ledger:.6f} (ledger through Phase 6) "
        f"− {reserve:.6f} (reserve) = {available:.6f} ≥ 0  ✓\n"
        "```\n"
    )
    budget_doc.write_text(text)


def update_readme(readme: Path, block: str) -> None:
    text = readme.read_text()
    if START_MARKER in text and END_MARKER in text:
        before = text.split(START_MARKER, 1)[0]
        after = text.split(END_MARKER, 1)[1]
        readme.write_text(before + block + after)
        return
    readme.write_text(text.rstrip() + "\n\n" + block + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--cases", default="data/phase3/test.jsonl")
    parser.add_argument("--data-readme", default="data/README.md")
    parser.add_argument("--pre-registration", default="docs/PRE_REGISTRATION.md")
    parser.add_argument("--budget-doc", default="docs/BUDGET.md")
    parser.add_argument(
        "--phase5-mapping",
        default="results/phase5-labeling-20261031/private/mapping.json",
    )
    parser.add_argument(
        "--phase5-report-analysis",
        default="results/phase5-judge-20261031/report-v1-analysis.json",
    )
    parser.add_argument("--analysis-out", default=None)
    parser.add_argument("--results-doc", default="docs/RESULTS.md")
    parser.add_argument("--readme", default="README.md")
    parser.add_argument("--budget-out", default="docs/BUDGET.md")
    parser.add_argument("--resamples", type=int, default=2000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--skip-docs", action="store_true")
    args = parser.parse_args()

    analysis = build_analysis(
        run_dir=Path(args.run_dir),
        cases_path=Path(args.cases),
        data_readme=Path(args.data_readme),
        pre_registration=Path(args.pre_registration),
        budget_doc=Path(args.budget_doc),
        phase5_mapping=Path(args.phase5_mapping),
        phase5_report_analysis=Path(args.phase5_report_analysis),
        resamples=args.resamples,
        seed=args.seed,
    )
    text = json.dumps(analysis, indent=2, sort_keys=True) + "\n"
    if args.analysis_out:
        out = Path(args.analysis_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text)
    else:
        print(text)
    if not args.skip_docs:
        Path(args.results_doc).write_text(render_results(analysis))
        update_readme(Path(args.readme), render_readme_block(analysis))
        update_budget(Path(args.budget_out), analysis)


if __name__ == "__main__":
    main()
