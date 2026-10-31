"""Offline end-to-end test of the Phase-3 analysis dry-run pipeline."""

from __future__ import annotations

import json

import pytest

from eval.analysis import analyse_run


def _records(tools=("a", "b", "c"), n=10):
    records = {}
    for arm in ("haiku-agent", "haiku-direct", "sonnet-direct"):
        rows = []
        for tool_index, tool in enumerate(tools):
            for index in range(n):
                task_id = f"{tool}{index}"
                if arm == "sonnet-direct":
                    exact = 1 if index < (8 - tool_index) else 0
                    cost = 1.0
                elif arm == "haiku-agent":
                    exact = 1
                    cost = 0.05
                else:
                    exact = 1 if index < 9 else 0
                    cost = 0.02
                rows.append(
                    {
                        "task_id": task_id,
                        "tool_name": tool,
                        "status": "completed",
                        "exact": exact,
                        "partial": float(exact),
                        "cost_usd": cost,
                        "input_tokens": 100,
                        "output_tokens": 20,
                        "raw_usage": {
                            "output_tokens_details": {"thinking_tokens": 5}
                        },
                        "format_failure_kind": [],
                        "failure_reason": "",
                    }
                )
        records[arm] = rows
    return records


def _run_payload(records):
    return {
        "run": {
            "run_id": "dev-test",
            "run_status": "completed",
            "label": "phase3/dev",
            "dev_vs_final": "dev",
            "git_commit": "test",
            "match_spec_version": "1.1",
            "pricing_basis": "list_sync",
            "max_tokens": 1024,
            "arms": [
                {
                    "arm": arm,
                    "model_id": f"model-{arm}",
                    "effort": "low",
                    "prompt_version": "test-prompt",
                }
                for arm in ("haiku-agent", "haiku-direct", "sonnet-direct")
            ],
            "subset_metadata": {"stage": "dev", "seed": 42},
        },
        "arms": {
            arm: {"per_case": rows} for arm, rows in records.items()
        },
    }


def test_analyse_run_reports_primary_secondary_sizing_and_spend(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "results.json").write_text(
        json.dumps(_run_payload(_records())) + "\n"
    )
    cases_path = tmp_path / "dev.jsonl"
    cases_path.write_text(
        "\n".join(
            json.dumps({"id": f"{tool}{index}", "tool_name": tool})
            for tool in ("a", "b", "c")
            for index in range(10)
        )
        + "\n"
    )

    analysis = analyse_run(
        run_dir,
        cases_path=cases_path,
        resamples=200,
        seed=5,
        k=1,
        spent_before=0.1,
        reserve=0.1,
    )

    assert analysis["primary_comparison"] == "haiku-agent vs sonnet-direct"
    assert analysis["primary"]["n_tools"] == 3
    assert analysis["primary"]["primary"]["mean"] == pytest.approx(
        (0.2 + 0.3 + 0.4) / 3
    )
    assert analysis["secondary"]["label"].startswith("haiku-direct")
    assert "sizing" not in analysis["secondary"]
    assert analysis["primary"]["sizing"]["components"]["m"] == 3
    assert analysis["primary"]["sizing"]["n_t"] >= 100
    assert analysis["spend_projection"]["k_by_arm"]["haiku-agent"] == 1
    assert "by_C" in analysis["spend_projection"]
    assert analysis["run_contract"]["efforts"]
    assert analysis["sizing_inputs"]["components"]["m"] == 3
    assert analysis["subset_metadata"]["stage"] == "dev"
