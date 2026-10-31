"""Task 0 resume semantics and Phase-3 effort plumbing (offline)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.agent import build_agent
from eval.config import (
    effort_request_params,
    select_shared_haiku_effort,
    select_sonnet_effort,
)
from eval.metrics import pilot_cost_gate
from eval.harness import run_arms
from eval.metrics import TokenPrice
from eval.mini_set import load_and_validate

MINI_SET = Path(__file__).resolve().parents[1] / "data" / "miniset.jsonl"
PRICE = TokenPrice(input=1.0, output=2.0, cache_read=0.1, cache_write=1.5)
USAGE = {
    "input_tokens": 1000,
    "output_tokens": 500,
    "cache_read_input_tokens": 0,
    "cache_creation_input_tokens": 0,
    "output_tokens_details": {"thinking_tokens": 100},
}
CASE_COST = 0.002


def response(content: list, *, stop_reason: str = "tool_use"):
    return SimpleNamespace(content=content, stop_reason=stop_reason, usage=dict(USAGE))


class FakeMessages:
    def __init__(self, script: list):
        self.script = list(script)
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if not self.script:
            raise AssertionError("unexpected API call")
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class FakeClient:
    def __init__(self, script: list):
        self.messages = FakeMessages(script)


def gold_response(case) -> SimpleNamespace:
    calls = json.loads(case.answers)
    return response(
        [
            {
                "type": "tool_use",
                "id": f"toolu_{index}",
                "name": call["name"],
                "input": call["arguments"],
            }
            for index, call in enumerate(calls)
        ]
    )


def test_aborted_case_is_re_attempted_and_its_zero_is_not_inherited(tmp_path):
    case = load_and_validate(MINI_SET)[0]

    first_client = FakeClient([gold_response(case)])
    with pytest.raises(Exception) as caught:
        run_arms(
            MINI_SET,
            tmp_path,
            ["haiku-direct"],
            client=first_client,
            pricing={"claude-haiku-5-5": PRICE},
            cost_ceiling=CASE_COST,
            case_ids=[case.id],
            run_id="test-aborted-resume",
        )
    assert type(caught.value).__name__ == "RunAborted"

    run_dir = tmp_path / "test-aborted-resume"
    checkpoint_path = (
        run_dir / "haiku-direct" / "checkpoints" / f"{case.id}__r1.json"
    )
    aborted_checkpoint = json.loads(checkpoint_path.read_text())
    assert aborted_checkpoint["status"] == "aborted"
    assert aborted_checkpoint["per_case"]["status"] == "aborted"
    assert aborted_checkpoint["per_case"]["exact"] == 0

    aborted_results = json.loads(
        (run_dir / "haiku-direct" / "results.json").read_text()
    )
    assert aborted_results["summary"]["n_cases_aborted"] == 1
    assert aborted_results["summary"]["n_cases_completed"] == 0

    resume_client = FakeClient([gold_response(case)])
    result = run_arms(
        MINI_SET,
        tmp_path,
        ["haiku-direct"],
        client=resume_client,
        pricing={"claude-haiku-5-5": PRICE},
        cost_ceiling=0.01,
        case_ids=[case.id],
        run_id="test-aborted-resume",
    )

    # The aborted case really was re-attempted rather than inherited.
    assert len(resume_client.messages.calls) == 1
    completed = json.loads(checkpoint_path.read_text())
    assert completed["status"] == "completed"
    assert completed["per_case"]["status"] == "completed"
    assert completed["per_case"]["exact"] == 1

    resumed_results = json.loads(
        (run_dir / "haiku-direct" / "results.json").read_text()
    )
    assert resumed_results["summary"]["n_cases_completed"] == 1
    assert resumed_results["summary"]["n_cases_aborted"] == 0
    assert [record["exact"] for record in resumed_results["per_case"]] == [1]
    # Lifetime actual spend includes the aborted attempt and the re-attempt.
    assert result["total_cost_usd"] == pytest.approx(2 * CASE_COST)


def test_effort_is_transmitted_only_through_output_config():
    assert effort_request_params("api_default") == {}
    assert effort_request_params("low") == {"output_config": {"effort": "low"}}
    with pytest.raises(ValueError):
        effort_request_params("minimal")

    client = FakeClient(
        [
            response(
                [
                    {
                        "type": "tool_use",
                        "id": "toolu_1",
                        "name": "get_weather",
                        "input": {"city": "Paris"},
                    }
                ]
            )
        ]
    )
    agent = build_agent(
        "haiku-direct",
        client=client,
        price=PRICE,
        effort="low",
        request_params=effort_request_params("low"),
    )
    trace = agent.run(
        {
            "id": "weather",
            "query": "Weather in Paris?",
            "tools": ["get_weather"],
        }
    )

    call = client.messages.calls[0]
    assert call["output_config"] == {"effort": "low"}
    assert "effort" not in call
    assert trace.effort == "low"
    assert trace.effort_transmitted is True
    assert trace.raw_usage["output_tokens_details"]["thinking_tokens"] == 100


def test_harness_runs_a_case_file_with_its_own_tool_definitions(tmp_path):
    case_path = tmp_path / "xlam.jsonl"
    payload = {
        "id": "xlam-test",
        "query": "Add one and two.",
        "tools": [
            {
                "name": "add_numbers",
                "description": "Add two integers.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "a": {"type": "integer"},
                        "b": {"type": "integer"},
                    },
                    "required": ["a", "b"],
                },
            }
        ],
        "answers": json.dumps(
            [{"name": "add_numbers", "arguments": {"a": 1, "b": 2}}]
        ),
        "tool_name": "add_numbers",
        "split": "dev",
        "label": "dev",
    }
    case_path.write_text(json.dumps(payload) + "\n")
    (tmp_path / "xlam.meta.json").write_text(
        json.dumps({"stage": "dev", "seed": 42}) + "\n"
    )
    client = FakeClient(
        [
            response(
                [
                    {
                        "type": "tool_use",
                        "id": "toolu_1",
                        "name": "add_numbers",
                        "input": {"a": 1, "b": 2},
                    }
                ]
            )
        ]
    )

    result = run_arms(
        case_path,
        tmp_path / "results",
        ["haiku-direct"],
        client=client,
        pricing={"claude-haiku-5-5": PRICE},
        cost_ceiling=0.01,
        run_id="xlam-run",
        label="phase3/dev",
        dev_vs_final="dev",
    )

    assert result["run_status"] == "completed"
    assert client.messages.calls[0]["tools"][0]["name"] == "add_numbers"
    run_meta = json.loads(
        (tmp_path / "results" / "xlam-run" / "run.json").read_text()
    )
    assert run_meta["label"] == "phase3/dev"
    assert run_meta["dev_vs_final"] == "dev"
    assert run_meta["subset_metadata"]["stage"] == "dev"
    arm_results = json.loads(
        (tmp_path / "results" / "xlam-run" / "haiku-direct" / "results.json").read_text()
    )
    assert arm_results["per_case"][0]["tool_name"] == "add_numbers"
    assert arm_results["per_case"][0]["exact"] == 1


def test_api_default_transmits_no_effort_field():
    client = FakeClient(
        [
            response(
                [
                    {
                        "type": "tool_use",
                        "id": "toolu_1",
                        "name": "get_weather",
                        "input": {"city": "Paris"},
                    }
                ]
            )
        ]
    )
    agent = build_agent("haiku-direct", client=client, price=PRICE)
    trace = agent.run(
        {"id": "weather", "query": "Weather in Paris?", "tools": ["get_weather"]}
    )

    call = client.messages.calls[0]
    assert "output_config" not in call
    assert "effort" not in call
    assert trace.effort == "api_default"
    assert trace.effort_transmitted is False


def test_sonnet_effort_rule_never_weakens_the_baseline():
    assert (
        select_sonnet_effort(
            {"api_default": 0.90, "high": 0.90, "max": 0.92}
        )
        == "max"
    )
    assert (
        select_sonnet_effort(
            {"api_default": 0.92, "high": 0.90, "max": 0.89}
        )
        == "api_default"
    )
    with pytest.raises(ValueError):
        select_sonnet_effort({"high": 0.9})


def test_haiku_effort_rule_picks_lowest_within_tolerance():
    scores = {
        "haiku-agent": {
            "low": 0.80,
            "medium": 0.86,
            "high": 0.90,
            "xhigh": 0.90,
            "max": 0.91,
        },
        "haiku-direct": {
            "low": 0.81,
            "medium": 0.87,
            "high": 0.90,
            "xhigh": 0.90,
            "max": 0.91,
        },
    }
    # Best worst-case is 0.91; with tolerance 0.02, medium (0.86) is too low,
    # high (0.90) is the lowest within tolerance.
    assert select_shared_haiku_effort(scores, tolerance=0.02) == "high"
    assert select_shared_haiku_effort(scores, tolerance=0.06) == "medium"


def test_pilot_cost_gate_uses_the_010_bar_without_tuning():
    assert pilot_cost_gate(0.09)["passes"] is True
    assert pilot_cost_gate(0.10)["passes"] is True
    assert pilot_cost_gate(0.11)["passes"] is False
