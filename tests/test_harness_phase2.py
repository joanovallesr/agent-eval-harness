"""Offline Phase-2 harness tests: fake client, checkpoints, ceiling, contract."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.agent import AgentTrace
from eval.harness import RESULTS_CONTRACT, RunAborted, _score_trace, run_arms
from eval.metrics import TokenPrice
from eval.mini_set import MiniCase, load_and_validate

MINI_SET = Path(__file__).resolve().parents[1] / "data" / "miniset.jsonl"
PRICE = TokenPrice(input=1.0, output=2.0, cache_read=0.1, cache_write=1.5)
USAGE = {
    "input_tokens": 1000,
    "output_tokens": 500,
    "cache_read_input_tokens": 0,
    "cache_creation_input_tokens": 0,
    "thinking_tokens": 100,
}
# 1000*1.0 + 500*2.0 = 2000 / 1e6
CASE_COST = 0.002


def response(content: list, *, stop_reason: str = "tool_use", usage: dict | None = None):
    return SimpleNamespace(
        content=content,
        stop_reason=stop_reason,
        usage=dict(USAGE if usage is None else usage),
    )


class FakeMessages:
    def __init__(self, script: list):
        self.script = list(script)
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if not self.script:
            raise AssertionError("unexpected API call: fake response script is empty")
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class FakeClient:
    def __init__(self, script: list):
        self.messages = FakeMessages(script)


def gold_response(case: MiniCase) -> SimpleNamespace:
    calls = json.loads(case.answers)
    blocks = [
        {
            "type": "tool_use",
            "id": f"toolu_{index}",
            "name": call["name"],
            "input": call["arguments"],
        }
        for index, call in enumerate(calls)
    ]
    return response(blocks)


def load_three_cases() -> list[MiniCase]:
    cases = load_and_validate(MINI_SET)
    return cases[:3]


def test_cost_ceiling_abort_preserves_partial_traces_and_marks_run_aborted(tmp_path):
    cases = load_three_cases()
    client = FakeClient([gold_response(case) for case in cases])

    with pytest.raises(RunAborted) as caught:
        run_arms(
            MINI_SET,
            tmp_path,
            ["haiku-direct"],
            client=client,
            pricing={"claude-haiku-5-5": PRICE},
            cost_ceiling=0.003,
            case_ids=[case.id for case in cases],
            run_id="test-abort",
        )

    run_dir = Path(caught.value.result["run_dir"])
    assert len(client.messages.calls) == 1

    run_meta = json.loads((run_dir / "run.json").read_text())
    assert run_meta["run_status"] == "aborted"
    assert "cost ceiling" in run_meta["abort_reason"]
    assert run_meta["dev_vs_final"] == "mini_smoke"
    assert run_meta["label"] == "mini/smoke"
    assert run_meta["total_cost_usd"] == pytest.approx(CASE_COST)

    checkpoint_1 = run_dir / "haiku-direct" / "checkpoints" / "mini-001__r1.json"
    checkpoint_2 = run_dir / "haiku-direct" / "checkpoints" / "mini-002__r1.json"
    assert checkpoint_1.exists()
    assert not checkpoint_2.exists()

    arm_results = json.loads((run_dir / "haiku-direct" / "results.json").read_text())
    assert arm_results["run"]["run_status"] == "aborted"
    assert len(arm_results["per_case"]) == 1
    assert arm_results["per_case"][0]["task_id"] == "mini-001"
    assert arm_results["summary"]["n_cases_attempted"] == 1
    assert arm_results["summary"]["n_cases_completed"] == 1


def test_checkpoint_resume_does_not_repeat_completed_entries(tmp_path):
    cases = load_three_cases()
    ids = [case.id for case in cases]
    first_client = FakeClient([gold_response(case) for case in cases])

    with pytest.raises(RunAborted):
        run_arms(
            MINI_SET,
            tmp_path,
            ["haiku-direct"],
            client=first_client,
            pricing={"claude-haiku-5-5": PRICE},
            cost_ceiling=0.003,
            case_ids=ids,
            run_id="test-resume",
        )

    run_dir = tmp_path / "test-resume"
    checkpoint_1 = run_dir / "haiku-direct" / "checkpoints" / "mini-001__r1.json"
    before = checkpoint_1.read_text()

    resume_client = FakeClient([gold_response(case) for case in cases[1:]])
    result = run_arms(
        MINI_SET,
        tmp_path,
        ["haiku-direct"],
        client=resume_client,
        pricing={"claude-haiku-5-5": PRICE},
        cost_ceiling=0.01,
        case_ids=ids,
        run_id="test-resume",
    )

    # Only the two unfinished cases hit the fake API.
    assert len(resume_client.messages.calls) == 2
    assert checkpoint_1.read_text() == before

    arm_results = json.loads((run_dir / "haiku-direct" / "results.json").read_text())
    assert [record["task_id"] for record in arm_results["per_case"]] == ids
    assert arm_results["run"]["run_status"] == "completed"
    assert arm_results["summary"]["n_cases_completed"] == 3
    assert arm_results["summary"]["n_repeats"] == 3
    assert result["run_status"] == "completed"
    assert result["total_cost_usd"] == pytest.approx(3 * CASE_COST)

    run_meta = json.loads((run_dir / "run.json").read_text())
    assert run_meta["run_status"] == "completed"
    assert run_meta["resume_history"]
    assert run_meta["resume_history"][0]["previous_status"] == "aborted"
    assert run_meta["resume_history"][0]["previous_abort_reason"]


def test_results_json_contains_required_run_case_and_summary_fields(tmp_path):
    case = load_three_cases()[0]
    client = FakeClient([gold_response(case)])

    run_arms(
        MINI_SET,
        tmp_path,
        ["haiku-agent"],
        client=client,
        pricing={"claude-haiku-5-5": PRICE},
        cost_ceiling=0.01,
        case_ids=[case.id],
        run_id="test-contract",
    )

    arm_results = json.loads(
        (tmp_path / "test-contract" / "haiku-agent" / "results.json").read_text()
    )
    run_block = arm_results["run"]
    summary = arm_results["summary"]
    per_case = arm_results["per_case"][0]

    for field in RESULTS_CONTRACT["run"]:
        assert field in run_block
    for field in RESULTS_CONTRACT["summary"]:
        assert field in summary
    for field in RESULTS_CONTRACT["per_case"]:
        assert field in per_case

    assert run_block["dev_vs_final"] == "mini_smoke"
    assert run_block["label"] == "mini/smoke"
    assert run_block["model_id"] == "claude-haiku-5-5"
    assert run_block["effort"] == "low"
    assert run_block["effort_provisional"] is True
    assert run_block["prompt_version"] == "phase2-mini-v1"
    assert run_block["match_spec_version"] == "1.1"
    assert run_block["run_status"] == "completed"
    assert run_block["max_tokens"] == 1024
    assert run_block["max_tokens_provisional"] is True
    assert summary["exact_match_mean"] == 1.0
    assert summary["cost_usd_total"] == pytest.approx(CASE_COST)
    assert summary["cost_per_completed_task"] == pytest.approx(CASE_COST)
    assert summary["mini_set_only"] is True
    assert per_case["retry_count"] == 0
    assert per_case["format_failure"] is False
    assert per_case["raw_usage"]["thinking_tokens"] == 100
    assert per_case["final_attempt_calls"]

    run_meta = json.loads((tmp_path / "test-contract" / "run.json").read_text())
    assert run_meta["pricing_basis"] == "list_sync"
    assert run_meta["pricing_per_mtok"]["claude-haiku-5-5"]["input"] == 1.0
    assert run_meta["max_tokens"] == 1024
    assert run_meta["max_tokens_provisional"] is True


def test_direct_schema_invalid_call_is_diagnosed_without_format_failure(tmp_path):
    case = load_three_cases()[1]
    client = FakeClient(
        [
            response(
                [
                    {
                        "type": "tool_use",
                        "id": "toolu_bad",
                        "name": "get_weather",
                        "input": {"city": 123},
                    }
                ]
            )
        ]
    )

    run_arms(
        MINI_SET,
        tmp_path,
        ["haiku-direct"],
        client=client,
        pricing={"claude-haiku-5-5": PRICE},
        cost_ceiling=0.01,
        case_ids=[case.id],
        run_id="test-schema-diag",
    )

    arm_results = json.loads(
        (tmp_path / "test-schema-diag" / "haiku-direct" / "results.json").read_text()
    )
    record = arm_results["per_case"][0]
    assert record["schema_failure"] is True
    assert record["final_schema_valid"] is False
    assert record["format_failure"] is False
    assert record["exact"] == 0
    assert arm_results["summary"]["schema_failure_rate"] == 1.0
    assert arm_results["summary"]["format_failure_rate"] == 0.0


def test_root_results_and_summary_are_written(tmp_path):
    case = load_three_cases()[0]
    client = FakeClient([gold_response(case)])

    run_arms(
        MINI_SET,
        tmp_path,
        ["haiku-direct"],
        client=client,
        pricing={"claude-haiku-5-5": PRICE},
        cost_ceiling=0.01,
        case_ids=[case.id],
        run_id="test-root",
    )

    root = json.loads((tmp_path / "test-root" / "results.json").read_text())
    summary = json.loads((tmp_path / "test-root" / "summary.json").read_text())

    assert root["run"]["label"] == "mini/smoke"
    assert "haiku-direct" in root["arms"]
    assert summary["arms"]["haiku-direct"]["exact_match_mean"] == 1.0


def test_harness_scores_text_only_direct_as_zero_and_format_failure():
    case = MiniCase(
        id="text-only",
        query="What's the weather?",
        tools=("get_weather",),
        answers=json.dumps(
            [{"name": "get_weather", "arguments": {"city": "Paris"}}]
        ),
    )
    trace = AgentTrace(
        task_id=case.id,
        arm="haiku-direct",
        final_attempt_blocks=[{"type": "text", "text": "It is sunny."}],
        format_failure=True,
        failure_reason="text_only",
    )

    score = _score_trace(trace, case)

    assert score["exact"] == 0
    assert score["partial"] == 0.0
    assert score["format_failure"] is True
    assert "no_tool_use" in score["format_failure_kind"]


def test_schema_failure_precedence_scores_zero_even_when_calls_equal_gold():
    case = MiniCase(
        id="schema-vs-gold",
        query="What's the weather?",
        tools=("get_weather",),
        answers=json.dumps(
            [{"name": "get_weather", "arguments": {"city": 123}}]
        ),
    )
    trace = AgentTrace(
        task_id=case.id,
        arm="haiku-agent",
        final_attempt_blocks=[
            {"type": "tool_use", "id": "x", "name": "get_weather", "input": {"city": 123}}
        ],
        schema_validation_failed=True,
        format_failure=True,
        failure_reason="schema_validation_after_retries",
    )

    score = _score_trace(trace, case)

    assert score["exact"] == 0
    assert score["partial"] == 0.0
    assert "schema_validation_after_retries" in score["format_failure_kind"]


def test_refusal_with_surviving_tool_call_still_scores_zero():
    case = MiniCase(
        id="refusal-with-call",
        query="What's the weather?",
        tools=("get_weather",),
        answers=json.dumps(
            [{"name": "get_weather", "arguments": {"city": "Paris"}}]
        ),
    )
    trace = AgentTrace(
        task_id=case.id,
        arm="haiku-agent",
        final_attempt_blocks=[
            {"type": "tool_use", "id": "x", "name": "get_weather", "input": {"city": "Paris"}}
        ],
        format_failure=True,
        failure_reason="refusal",
    )

    score = _score_trace(trace, case)

    assert score["exact"] == 0
    assert score["partial"] == 0.0
    assert "refusal" in score["format_failure_kind"]
