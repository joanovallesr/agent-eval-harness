"""Offline agent-loop tests with a fake Anthropic client.

No test in this file makes a network call or needs an API key.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from agent.agent import AgentTrace, build_agent, validate_tool_calls
from eval.config import (
    EFFORT_BY_ARM,
    MAX_HTTP_REQUESTS_PER_CASE_AGENT,
    MAX_HTTP_REQUESTS_PER_CASE_DIRECT,
    MAX_SCHEMA_REPAIR_RETRIES,
)
from eval.metrics import TokenPrice

PRICE = TokenPrice(input=1.0, output=2.0, cache_read=0.1, cache_write=1.5)
USAGE = {
    "input_tokens": 1000,
    "output_tokens": 500,
    "cache_read_input_tokens": 0,
    "cache_creation_input_tokens": 0,
    "thinking_tokens": 0,
}


def tool_block(name: str, arguments: dict, block_id: str = "toolu_test") -> dict:
    return {"type": "tool_use", "id": block_id, "name": name, "input": arguments}


def text_block(text: str) -> dict:
    return {"type": "text", "text": text}


def response(
    content: list,
    *,
    stop_reason: str = "tool_use",
    usage: dict | None = None,
):
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


def make_agent(arm: str, script: list, **kwargs):
    client = FakeClient(script)
    agent = build_agent(
        arm,
        client=client,
        price=PRICE,
        sleep=lambda _seconds: None,
        **kwargs,
    )
    return agent, client


def weather_task() -> dict:
    return {
        "id": "case-weather",
        "query": "What's the weather in Paris?",
        "tools": ["get_weather"],
    }


def test_phase2_effort_is_api_default_and_retry_bounds_are_documented():
    assert set(EFFORT_BY_ARM.values()) == {"api_default"}
    assert MAX_HTTP_REQUESTS_PER_CASE_AGENT == 9
    assert MAX_HTTP_REQUESTS_PER_CASE_DIRECT == 3


def test_valid_tool_use_succeeds_for_direct_and_agent_arms():
    for arm in ("haiku-direct", "haiku-agent"):
        agent, client = make_agent(
            arm,
            [response([tool_block("get_weather", {"city": "Paris"})])],
        )

        trace = agent.run(weather_task())

        assert len(client.messages.calls) == 1
        assert trace.final_attempt_calls == [
            {
                "type": "tool_use",
                "id": "toolu_test",
                "name": "get_weather",
                "input": {"city": "Paris"},
            }
        ]
        assert trace.retry_count == 0
        assert trace.schema_validation_failed is False
        assert trace.format_failure is False
        assert trace.model_id == "claude-haiku-5-5"
        assert trace.effort == "api_default"
        assert trace.effort_provisional is True
        assert trace.effort_transmitted is False
        assert "effort" not in client.messages.calls[0]


def test_text_only_direct_reply_is_empty_and_a_format_failure():
    agent, client = make_agent(
        "haiku-direct",
        [response([text_block("It is sunny.")], stop_reason="end_turn")],
    )

    trace = agent.run(weather_task())

    assert len(client.messages.calls) == 1
    assert trace.final_attempt_calls == []
    assert trace.format_failure is True
    assert trace.failure_reason == "text_only"
    assert trace.retry_count == 0
    assert trace.schema_validation_failed is False


def test_agent_repairs_one_text_only_attempt_and_succeeds():
    agent, client = make_agent(
        "haiku-agent",
        [
            response([text_block("It is sunny.")], stop_reason="end_turn"),
            response([tool_block("get_weather", {"city": "Paris"})]),
        ],
    )

    trace = agent.run(weather_task())

    assert len(client.messages.calls) == 2
    assert trace.retry_count == 1
    assert trace.format_failure is False
    assert trace.schema_validation_failed is False
    assert trace.validation_errors[0]["kind"] == "no_tool_use"
    repair_feedback = client.messages.calls[1]["messages"][-1]["content"]
    assert "Validation errors" in repair_feedback
    assert "get_weather" in repair_feedback
    assert "sunny" not in repair_feedback.lower()


def test_non_object_tool_input_is_handled_per_spec():
    # Direct arms do not repair schema errors: one request, malformed block
    # preserved for the scorer.
    agent, client = make_agent(
        "haiku-direct",
        [response([tool_block("get_weather", "not-an-object")])],
    )

    trace = agent.run(weather_task())

    assert len(client.messages.calls) == 1
    assert trace.retry_count == 0
    assert trace.schema_validation_failed is False
    assert trace.final_attempt_calls[0]["input"] == "not-an-object"
    assert trace.validation_errors[0]["kind"] == "malformed_block"


def test_agent_repairs_one_schema_invalid_attempt_and_succeeds():
    agent, client = make_agent(
        "haiku-agent",
        [
            response([tool_block("get_weather", {"city": 123})]),
            response([tool_block("get_weather", {"city": "Paris"})]),
        ],
    )

    trace = agent.run(weather_task())

    assert len(client.messages.calls) == 2
    assert trace.retry_count == 1
    assert trace.schema_validation_failed is False
    assert trace.final_schema_valid is True
    assert trace.final_attempt_calls[0]["input"] == {"city": "Paris"}
    assert trace.validation_errors[0]["kind"] == "schema"
    assert trace.validation_errors[0]["path"] == ["city"]
    repair_turn = client.messages.calls[1]["messages"][-1]["content"]
    assert isinstance(repair_turn, list)
    assert repair_turn[0]["type"] == "tool_result"
    assert repair_turn[0]["tool_use_id"] == "toolu_test"
    assert repair_turn[0]["is_error"] is True
    assert "Validation errors" in repair_turn[0]["content"]
    assert "get_weather" in repair_turn[0]["content"]


def test_repair_exhaustion_preserves_schema_failure_status():
    invalid = response([tool_block("get_weather", {"city": 123})])
    agent, client = make_agent(
        "haiku-agent",
        [invalid, invalid, invalid],
        max_schema_repair_retries=MAX_SCHEMA_REPAIR_RETRIES,
    )

    trace = agent.run(weather_task())

    assert MAX_SCHEMA_REPAIR_RETRIES == 2
    assert len(client.messages.calls) == 3  # initial attempt + 2 retries
    assert trace.retry_count == 2
    assert trace.schema_validation_failed is True
    assert trace.format_failure is True
    assert trace.failure_reason == "schema_validation_after_retries"
    # The final emitted call is retained, but the harness precedence rule
    # still scores it 0.
    assert trace.final_attempt_calls[0]["input"] == {"city": 123}


def test_direct_arms_do_not_perform_schema_repair_retries():
    agent, client = make_agent(
        "haiku-direct",
        [
            response([tool_block("get_weather", {"city": 123})]),
            response([tool_block("get_weather", {"city": "Paris"})]),
        ],
    )

    trace = agent.run(weather_task())

    assert len(client.messages.calls) == 1
    assert trace.retry_count == 0
    assert trace.schema_validation_failed is False
    assert trace.final_attempt_calls[0]["input"] == {"city": 123}


@pytest.mark.parametrize("arm", ["haiku-direct", "haiku-agent"])
def test_infrastructure_retries_are_identical_across_arms(arm):
    agent, client = make_agent(
        arm,
        [
            TimeoutError("timed out"),
            ConnectionError("connection reset"),
            response([tool_block("get_weather", {"city": "Paris"})]),
        ],
    )

    trace = agent.run(weather_task())

    assert len(client.messages.calls) == 3
    assert trace.infrastructure_retry_count == 2
    assert trace.request_count == 1
    assert trace.final_attempt_calls[0]["name"] == "get_weather"


def test_infrastructure_retry_exhaustion_is_a_terminal_failure():
    agent, client = make_agent(
        "haiku-direct",
        [TimeoutError("one"), TimeoutError("two"), TimeoutError("three")],
    )

    trace = agent.run(weather_task())

    assert len(client.messages.calls) == 3
    assert trace.infrastructure_retry_count == 2
    assert trace.stopped_early is True
    assert trace.format_failure is True
    assert trace.failure_reason == "infrastructure_exhausted"
    assert trace.final_attempt_calls == []


def test_non_infrastructure_api_error_gets_no_retry_and_is_not_labelled_infra():
    agent, client = make_agent(
        "haiku-direct",
        [ValueError("bad request")],
    )

    trace = agent.run(weather_task())

    assert len(client.messages.calls) == 1
    assert trace.infrastructure_retry_count == 0
    assert trace.stopped_early is True
    assert trace.failure_reason == "api_error"
    assert trace.final_attempt_calls == []


def test_refusal_preserves_the_actual_final_message_but_scores_as_failure():
    agent, client = make_agent(
        "haiku-direct",
        [response([text_block("I cannot do that.")], stop_reason="refusal")],
    )

    trace = agent.run(weather_task())

    assert len(client.messages.calls) == 1
    assert trace.failure_reason == "refusal"
    assert trace.format_failure is True
    assert trace.stopped_early is True
    # The raw final message is retained for the trace even though the harness
    # will score the event 0.
    assert trace.final_attempt_blocks == [text_block("I cannot do that.")]


def test_direct_arms_record_schema_diagnostics_without_repairing():
    agent, client = make_agent(
        "haiku-direct",
        [response([tool_block("get_weather", {"city": 123})])],
    )

    trace = agent.run(weather_task())

    assert len(client.messages.calls) == 1
    assert trace.retry_count == 0
    assert trace.schema_validation_failed is False
    assert trace.validation_errors[0]["kind"] == "schema"
    assert trace.final_schema_valid is False


def test_parallel_attempt_repair_returns_one_rejected_result_per_tool_use_id():
    agent, client = make_agent(
        "haiku-agent",
        [
            response(
                [
                    tool_block("get_weather", {"city": "Paris"}, "toolu_valid"),
                    tool_block("get_weather", {"city": 123}, "toolu_invalid"),
                ]
            ),
            response([tool_block("get_weather", {"city": "Paris"}, "toolu_final")]),
        ],
    )

    trace = agent.run(weather_task())

    assert trace.retry_count == 1
    assert trace.final_attempt_calls[0]["id"] == "toolu_final"
    repair = client.messages.calls[1]["messages"][-1]["content"]
    assert isinstance(repair, list)
    assert len(repair) == 2
    assert {block["tool_use_id"] for block in repair} == {
        "toolu_valid",
        "toolu_invalid",
    }
    assert all(block["type"] == "tool_result" for block in repair)
    assert all(block["is_error"] is True for block in repair)

    by_id = {block["tool_use_id"]: block["content"] for block in repair}
    assert "not executed" in by_id["toolu_valid"].lower()
    assert "no tool output" in by_id["toolu_valid"].lower()
    assert "Validation errors" in by_id["toolu_invalid"]
    assert "city" in by_id["toolu_invalid"]


def test_multiple_calls_and_final_attempt_rule_are_preserved():
    agent, client = make_agent(
        "haiku-agent",
        [
            response(
                [
                    tool_block("get_weather", {"city": 123}, "first"),
                    tool_block("get_weather", {"city": "Rome"}, "first-2"),
                ]
            ),
            response(
                [
                    tool_block("get_weather", {"city": "Paris"}, "second"),
                    tool_block("get_weather", {"city": "Berlin"}, "second-2"),
                ]
            ),
        ],
    )

    trace = agent.run(weather_task())

    assert [call["id"] for call in trace.tool_calls] == [
        "first",
        "first-2",
        "second",
        "second-2",
    ]
    assert [call["id"] for call in trace.final_attempt_calls] == [
        "second",
        "second-2",
    ]
    assert len(client.messages.calls) == 2


def test_cost_and_raw_usage_come_from_logged_usage_and_pinned_price():
    usage = {
        "input_tokens": 1000,
        "output_tokens": 500,
        "cache_read_input_tokens": 100,
        "cache_creation_input_tokens": 50,
        "thinking_tokens": 300,
        "provider_future_field": "kept",
    }
    agent, _client = make_agent(
        "haiku-direct",
        [response([tool_block("get_weather", {"city": "Paris"})], usage=usage)],
    )

    trace = agent.run(weather_task())

    # 1000*1.0 + 500*2.0 + 100*0.1 + 50*1.5 = 2085 / 1e6
    assert trace.cost_usd == pytest.approx(0.002085)
    assert trace.input_tokens == 1000
    assert trace.output_tokens == 500
    assert trace.raw_usage["thinking_tokens"] == 300
    assert trace.raw_usage["provider_future_field"] == "kept"
    assert trace.price_configured is True


def test_validate_tool_calls_reports_unknown_and_malformed_calls():
    schemas = [
        {
            "name": "get_weather",
            "description": "",
            "input_schema": {
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
        }
    ]

    errors = validate_tool_calls(
        [
            tool_block("not_offered", {}),
            tool_block("get_weather", []),
            "not-a-block",
        ],
        schemas,
    )

    kinds = [error["kind"] for error in errors]
    assert kinds.count("unknown_tool") == 1
    assert kinds.count("malformed_block") == 2


def test_trace_serializes_to_json_safe_dict():
    agent, _client = make_agent(
        "haiku-direct",
        [response([tool_block("get_weather", {"city": "Paris"})])],
    )

    trace = agent.run(weather_task())
    payload = trace.to_dict()

    assert isinstance(trace, AgentTrace)
    assert payload["task_id"] == "case-weather"
    assert json.loads(json.dumps(payload))["final_attempt_calls"][0]["name"] == "get_weather"
