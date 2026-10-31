"""Mini-set integrity tests: loader, schemas, scorer round trip, coverage."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from eval.mini_set import (
    CANDIDATE_TOOLS,
    MiniCase,
    MiniSetError,
    load_and_validate,
    load_cases,
    validate_case,
    validate_mini_set,
)
from eval.scorer import parse_gold, score_case

MINI_SET = Path(__file__).resolve().parents[1] / "data" / "miniset.jsonl"


def test_tool_schemas_are_valid_json_schema_with_explicit_required():
    from jsonschema import Draft202012Validator

    from agent.tools import TOOLS

    for tool in TOOLS:
        schema = tool["input_schema"]
        Draft202012Validator.check_schema(schema)
        assert "required" in schema
        assert isinstance(schema["required"], list)


def test_mini_set_loads_and_every_gold_call_is_offered_and_schema_valid():
    cases = load_and_validate(MINI_SET)

    assert len(cases) == 20
    assert len({case.id for case in cases}) == 20
    for case in cases:
        assert case.split == "mini"
        assert case.label == "smoke"
        assert CANDIDATE_TOOLS.isdisjoint(case.tools)
        for call in parse_gold(case.answers):
            assert call.name in case.tools


def test_mini_set_gold_can_be_scored_by_phase_one_scorer():
    for case in load_and_validate(MINI_SET):
        gold = parse_gold(case.answers)
        blocks = [
            {
                "type": "tool_use",
                "id": f"gold-{index}",
                "name": call.name,
                "input": call.arguments,
            }
            for index, call in enumerate(gold)
        ]
        result = score_case(blocks, case.answers)
        assert result.exact == 1
        assert result.partial == 1.0


def test_mini_set_covers_required_pipeline_cases():
    cases = load_and_validate(MINI_SET)
    gold_lists = [json.loads(case.answers) for case in cases]

    # Zero-argument call.
    assert any(call["arguments"] == {} for calls in gold_lists for call in calls)
    # Multiple parallel calls.
    assert any(len(calls) > 1 for calls in gold_lists)
    # Repeated identical calls (multiset multiplicity).
    assert any(
        len(calls) == 2 and calls[0] == calls[1] for calls in gold_lists
    )
    # A nested object argument (directly or inside a list).
    def contains_dict(value) -> bool:
        if isinstance(value, dict):
            return True
        if isinstance(value, list):
            return any(contains_dict(item) for item in value)
        return False

    assert any(
        contains_dict(value)
        for calls in gold_lists
        for call in calls
        for value in call["arguments"].values()
    )
    # Null, boolean, list, number, and string argument values.
    values = [
        value
        for calls in gold_lists
        for call in calls
        for value in call["arguments"].values()
    ]
    assert any(value is None for value in values)
    assert any(isinstance(value, bool) for value in values)
    assert any(isinstance(value, list) for value in values)
    assert any(isinstance(value, int) and not isinstance(value, bool) for value in values)
    assert any(isinstance(value, float) for value in values)
    assert any(isinstance(value, str) for value in values)
    # Optional arguments both present and omitted across the set.
    offered_by_case = {case.id: set(case.tools) for case in cases}
    assert all(offered_by_case.values())
    used_tools = {call["name"] for calls in gold_lists for call in calls}
    offered_tools = {tool for case in cases for tool in case.tools}
    assert offered_tools - used_tools  # plausible distractors exist


def test_loader_rejects_held_out_candidate_tool():
    case = MiniCase(
        id="bad-candidate",
        query="Use a held-out tool.",
        tools=("average",),
        answers=json.dumps([{"name": "average", "arguments": {"numbers": [1, 2]}}]),
    )

    with pytest.raises(MiniSetError, match="held-out candidate"):
        validate_case(case)


def test_loader_rejects_gold_tool_that_is_not_offered():
    case = MiniCase(
        id="bad-not-offered",
        query="Weather?",
        tools=("get_weather",),
        answers=json.dumps([{"name": "get_stock_quote", "arguments": {"ticker": "X"}}]),
    )

    with pytest.raises(MiniSetError, match="not offered"):
        validate_case(case)


def test_loader_rejects_gold_that_violates_the_tool_schema():
    case = MiniCase(
        id="bad-schema",
        query="Weather?",
        tools=("get_weather",),
        answers=json.dumps([{"name": "get_weather", "arguments": {"city": 123}}]),
    )

    with pytest.raises(MiniSetError, match="violates its schema"):
        validate_case(case)


def test_loader_rejects_duplicate_case_ids():
    cases = load_and_validate(MINI_SET)
    with pytest.raises(MiniSetError, match="duplicate case id"):
        validate_mini_set([cases[0], cases[0]])


def test_load_cases_does_not_validate_until_asked():
    cases = load_cases(MINI_SET)
    assert len(cases) == 20
    assert isinstance(cases[0], MiniCase)


def test_agent_task_omits_gold_answers():
    case = load_and_validate(MINI_SET)[0]

    task = case.to_task()

    assert "answers" not in task
    assert task["id"] == case.id
    assert task["tools"] == list(case.tools)


def test_candidate_tool_list_matches_the_frozen_verifier():
    # The verifier is the source of truth for the Phase-0 freeze; this catches
    # drift between the mini-set guard and scripts/select_candidates.py.
    from select_candidates import FROZEN_CANDIDATES

    assert CANDIDATE_TOOLS == frozenset(FROZEN_CANDIDATES)
