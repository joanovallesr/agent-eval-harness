"""Offline tests for the Phase-6 report generator."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from phase6_report import (
    END_MARKER,
    START_MARKER,
    _positive_cache,
    build_analysis,
    classify_failure,
    cost_verdict,
    render_readme_block,
    render_results,
    update_budget,
    update_readme,
)

ROOT = Path(__file__).resolve().parents[1]
TOOLS = [
    "calculate_standard_deviation",
    "average",
    "get_ip_location",
    "find_pairs_with_sum",
    "find_majority_element",
    "chi_square_independence_test",
    "calculate_quarterly_dividend",
    "calculate_investment_return",
]


def _schema() -> dict:
    return {
        "type": "object",
        "properties": {"x": {"type": "integer"}},
        "required": ["x"],
    }


def _case_row(tool: str, index: int) -> dict:
    case_id = f"case-{tool}-{index}"
    return {
        "id": case_id,
        "tool_name": tool,
        "query": "synthetic query",
        "answers": json.dumps(
            [{"name": tool, "arguments": {"x": index + 1}}]
        ),
        "tools": [
            {
                "name": tool,
                "description": "synthetic tool",
                "input_schema": _schema(),
            }
        ],
    }


def _record(
    arm: str,
    tool: str,
    index: int,
    exact: int,
    *,
    cost: float,
    **overrides,
) -> dict:
    record = {
        "task_id": f"case-{tool}-{index}",
        "tool_name": tool,
        "arm": arm,
        "status": "completed",
        "exact": exact,
        "partial": float(exact),
        "name_only": False,
        "format_failure": False,
        "format_failure_kind": [],
        "schema_validation_failed": False,
        "failure_reason": "",
        "final_attempt_calls": [
            {"type": "tool_use", "name": tool, "input": {"x": index + 1}}
        ],
        "input_tokens": 100,
        "output_tokens": 20,
        "raw_usage": {
            "input_tokens": 100,
            "output_tokens": 20,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
        },
        "cost_usd": cost,
        "latency_s": 1.0,
    }
    record.update(overrides)
    return record


def _write_sample(tmp_path: Path) -> tuple[Path, Path]:
    cases = tmp_path / "test.jsonl"
    rows = []
    for tool in TOOLS:
        for index in range(2):
            rows.append(_case_row(tool, index))
    cases.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    meta = tmp_path / "test.meta.json"
    meta.write_text(
        json.dumps({"stage": "test", "test_tools": TOOLS, "n_cases": len(rows)})
    )
    return cases, meta


def _write_run(tmp_path: Path) -> Path:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    per_case = {arm: [] for arm in ("haiku-agent", "haiku-direct", "sonnet-direct")}
    for tool_index, tool in enumerate(TOOLS):
        for index in range(2):
            per_case["haiku-agent"].append(
                _record("haiku-agent", tool, index, 1, cost=0.0001)
            )
            per_case["haiku-direct"].append(
                _record(
                    "haiku-direct",
                    tool,
                    index,
                    1 if tool_index % 2 == 0 else 0,
                    cost=0.0001,
                )
            )
            per_case["sonnet-direct"].append(
                _record(
                    "sonnet-direct",
                    tool,
                    index,
                    1 if tool_index % 2 == 1 else 0,
                    cost=0.002,
                )
            )
    total = sum(
        record["cost_usd"]
        for records in per_case.values()
        for record in records
    )
    payload = {
        "run": {
            "run_id": "synthetic-run",
            "git_commit": "deadbeef",
            "created_at": "2026-10-31T00:00:00+00:00",
            "label": "phase6/heldout",
            "dev_vs_final": "heldout_test",
            "run_status": "completed",
            "total_cost_usd": total,
            "match_spec_version": "1.1",
            "prompt_version": "phase2-mini-v1",
            "max_tokens": 1024,
            "pricing_basis": "list_sync",
            "pricing_path": "config/pricing.list_sync.2026-10-31.json",
            "repeats": 1,
            "resume_history": [],
            "arms": [
                {
                    "arm": "haiku-agent",
                    "model_id": "claude-haiku-5-5",
                    "effort": "low",
                    "effort_transmitted": True,
                    "prompt_version": "phase2-mini-v1",
                },
                {
                    "arm": "haiku-direct",
                    "model_id": "claude-haiku-5-5",
                    "effort": "low",
                    "effort_transmitted": True,
                    "prompt_version": "phase2-mini-v1",
                },
                {
                    "arm": "sonnet-direct",
                    "model_id": "claude-sonnet-5-5",
                    "effort": "api_default",
                    "effort_transmitted": False,
                    "prompt_version": "phase2-mini-v1",
                },
            ],
        },
        "arms": {
            arm: {"per_case": records} for arm, records in per_case.items()
        },
    }
    (run_dir / "results.json").write_text(json.dumps(payload))
    return run_dir


def test_taxonomy_assigns_one_label_to_every_non_exact_case():
    gold = ["average"]
    base = {
        "task_id": "c",
        "tool_name": "average",
        "status": "completed",
        "format_failure_kind": [],
        "failure_reason": "",
        "schema_validation_failed": False,
        "final_attempt_calls": [
            {"type": "tool_use", "name": "average", "input": {"x": 2}}
        ],
    }
    cases = {
        "truncated": dict(base, failure_reason="truncated"),
        "refusal": dict(base, failure_reason="refusal"),
        "infrastructure": dict(base, status="aborted"),
        "missing-call": dict(base, final_attempt_calls=[]),
        "format-error": dict(base, schema_validation_failed=True),
        "wrong-tool": dict(
            base,
            final_attempt_calls=[
                {"type": "tool_use", "name": "get_order", "input": {"x": 2}}
            ],
        ),
        "extra-call": dict(
            base,
            final_attempt_calls=[
                {"type": "tool_use", "name": "average", "input": {"x": 2}},
                {"type": "tool_use", "name": "average", "input": {"x": 2}},
            ],
        ),
        "right-tool-wrong-params": dict(
            base,
            final_attempt_calls=[
                {"type": "tool_use", "name": "average", "input": {"x": 9}}
            ],
        ),
    }
    for expected, record in cases.items():
        assert classify_failure(record, gold) == expected

    partial = dict(
        base,
        final_attempt_calls=[
            {"type": "tool_use", "name": "average", "input": {"x": 2}}
        ],
    )
    assert classify_failure(partial, ["average", "average"]) == "partial-answer"


def test_cache_guard_sees_nested_nonzero_usage():
    assert _positive_cache({"cache_read_input_tokens": 1})
    assert _positive_cache(
        {"cache_creation": {"ephemeral_5m_input_tokens": 3}}
    )
    assert not _positive_cache(
        {"cache_read_input_tokens": 0, "cache_creation": {"ephemeral_5m_input_tokens": 0}}
    )


def test_cost_verdict_is_the_pre_registered_binary_rule():
    assert cost_verdict(0.10) == "parity holds"
    assert cost_verdict(0.099999) == "parity holds"
    assert cost_verdict(0.100001) == "parity fails"


def test_build_and_render_use_one_results_json(tmp_path: Path):
    cases, _ = _write_sample(tmp_path)
    run_dir = _write_run(tmp_path)

    data_readme = tmp_path / "README.md"
    sample_sha = hashlib.sha256(cases.read_bytes()).hexdigest()
    meta_sha = hashlib.sha256(
        (tmp_path / "test.meta.json").read_bytes()
    ).hexdigest()
    data_readme.write_text(
        f"{sample_sha}  data/phase3/test.jsonl\n"
        f"{meta_sha}  data/phase3/test.meta.json\n"
    )

    phase5_mapping = tmp_path / "mapping.json"
    phase5_mapping.write_text(
        json.dumps(
            {
                "mapping": [
                    {"split": "report", "task_id": "task-1"},
                    {"split": "report", "task_id": "task-1"},
                ]
            }
        )
    )
    phase5_report = tmp_path / "report-analysis.json"
    phase5_report.write_text(
        json.dumps(
            {
                "n": 2,
                "raw_agreement": 1.0,
                "kappa_defined": False,
                "kappa": None,
            }
        )
    )

    analysis = build_analysis(
        run_dir=run_dir,
        cases_path=cases,
        data_readme=data_readme,
        pre_registration=ROOT / "docs" / "PRE_REGISTRATION.md",
        budget_doc=ROOT / "docs" / "BUDGET.md",
        phase5_mapping=phase5_mapping,
        phase5_report_analysis=phase5_report,
        resamples=50,
        seed=42,
    )
    assert analysis["phase5"]["n_tasks"] == 1
    assert analysis["phase5"]["n_traces"] == 2

    assert analysis["verification"]["sample_checksum_matches"] is True
    assert analysis["verification"]["metadata_checksum_matches"] is True
    assert analysis["primary"]["n_tools"] == 8
    assert analysis["cost"]["verdict"] == "parity holds"
    for arm in ("haiku-agent", "haiku-direct", "sonnet-direct"):
        taxonomy = analysis["failure_taxonomy"][arm]
        assert taxonomy["labelled"] == taxonomy["non_exact"]

    results = render_results(analysis)
    assert "Held-Out Test Results" in results
    assert "parity holds" in results
    assert "Phase 5 cross-reference" in results

    block = render_readme_block(analysis)
    readme = tmp_path / "README.md"
    readme.write_text(
        f"before\n{START_MARKER}\nold\n{END_MARKER}\nafter\n"
    )
    update_readme(readme, block)
    text = readme.read_text()
    assert text.startswith("before\n")
    assert text.endswith("after\n")
    assert "\nold\n" not in text
    assert "parity holds" in text

    budget = tmp_path / "BUDGET.md"
    budget.write_text((ROOT / "docs" / "BUDGET.md").read_text())
    update_budget(budget, analysis)
    budget_text = budget.read_text()
    assert "Phase 6 held-out actuals" in budget_text
    assert "The new ledger total is" in budget_text
    assert "| **test_spend** |" in budget_text
    assert "— | — | `C · n_t" not in budget_text
