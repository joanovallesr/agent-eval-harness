"""Offline tests for the deterministic Phase-3 sampler.

These use synthetic rows shaped like the pinned xLAM columns. No dataset and
no model call is required.
"""

from __future__ import annotations

import json

import pytest

from sample_cases import (
    apply_final_filters,
    convert_tool_definition,
    sample_metadata,
    sample_stage,
)


def tool(name: str, parameters: dict, description: str = "") -> dict:
    return {"name": name, "description": description, "parameters": parameters}


def row(query: str, answers: list, tools: list) -> dict:
    return {
        "query": query,
        "answers": json.dumps(answers),
        "tools": json.dumps(tools),
    }


ALPHA = tool("alpha", {"a": {"type": "integer", "description": "Core value."}})
BETA = tool("beta", {"b": {"type": "integer", "description": "Core value."}})
GAMMA = tool(
    "gamma",
    {
        "q": {"type": "string", "description": "Core query."},
        "limit": {"type": "integer", "description": "Maximum items."},
        "lang": {"type": "string", "description": "Optional language."},
    },
)
CANDIDATE = tool("average", {"xs": {"type": "array", "description": "Numbers."}})


def synthetic_rows() -> list[dict]:
    return [
        row("alpha one", [{"name": "alpha", "arguments": {"a": 1}}], [ALPHA, CANDIDATE]),
        row("alpha two", [{"name": "alpha", "arguments": {"a": 2}}], [ALPHA]),
        row("beta one", [{"name": "beta", "arguments": {"b": 1}}], [BETA]),
        row("beta two", [{"name": "beta", "arguments": {"b": 2}}], [BETA]),
        row("gamma one", [{"name": "gamma", "arguments": {"q": "x"}}], [GAMMA]),
        row("gamma two", [{"name": "gamma", "arguments": {"q": "y"}}], [GAMMA]),
        # Candidate as a gold/answer tool: a leak, excluded.
        row("candidate", [{"name": "average", "arguments": {"xs": [1, 2]}}], [CANDIDATE]),
        # Mixed candidate + non-candidate gold calls: excluded and counted.
        row(
            "mixed",
            [
                {"name": "alpha", "arguments": {"a": 1}},
                {"name": "average", "arguments": {"xs": [1]}},
            ],
            [ALPHA, CANDIDATE],
        ),
        # Malformed gold (string-encoded arguments): excluded and counted.
        row(
            "malformed",
            [{"name": "alpha", "arguments": json.dumps({"a": 1})}],
            [ALPHA],
        ),
        # Missing core input: excluded and counted.
        row("missing core", [{"name": "alpha", "arguments": {}}], [ALPHA]),
        # Multi-tool with no candidate: not a single-tool case.
        row(
            "multi",
            [
                {"name": "alpha", "arguments": {"a": 1}},
                {"name": "beta", "arguments": {"b": 1}},
            ],
            [ALPHA, BETA],
        ),
    ]


def test_filters_exclude_count_and_keep_optional_parameter_omissions():
    report = apply_final_filters(synthetic_rows())
    counts = report["counts"]

    assert counts["candidate_answer_rows"] == 1
    assert counts["mixed_candidate_non_candidate"] == 1
    assert counts["malformed_gold"] == 1
    assert counts["required_core_input_rows"] == 1
    assert counts["required_core_input_missing_single_tool"] == 1
    assert counts["multi_tool"] == 2
    assert counts["eligible_single_tool_non_candidate"] == 6

    # Gamma omits both `limit` (name rule) and `lang` (description rule);
    # both are optional, so those rows stay usable.
    assert len(report["by_tool"]["gamma"]) == 2
    assert set(report["by_tool"]) == {"alpha", "beta", "gamma"}


def test_candidate_distractor_is_allowed_and_counted():
    report = apply_final_filters(synthetic_rows())

    assert report["distractor_rows"]["average"] == 1
    assert report["distractor_cases"]["average"] == 1
    sampled = sample_stage(
        report["by_tool"],
        stage="pilot",
        seed=42,
        n_tools=3,
        cases_per_tool=2,
    )
    metadata = sample_metadata(
        stage="pilot",
        seed=42,
        revision="test-revision",
        filter_report=report,
        sample=sampled,
    )
    assert metadata["distractor_exposure_sample"]["average"] >= 1


def test_sampling_is_deterministic_and_never_uses_a_candidate_answer():
    report = apply_final_filters(synthetic_rows())
    first = sample_stage(
        report["by_tool"],
        stage="pilot",
        seed=42,
        n_tools=3,
        cases_per_tool=1,
    )
    second = sample_stage(
        report["by_tool"],
        stage="pilot",
        seed=42,
        n_tools=3,
        cases_per_tool=1,
    )

    assert [case["id"] for case in first["cases"]] == [
        case["id"] for case in second["cases"]
    ]
    assert first["tool_distribution"] == second["tool_distribution"]
    for case in first["cases"]:
        assert case["tool_name"] not in {"average"}
        gold_names = {call["name"] for call in json.loads(case["answers"])}
        assert "average" not in gold_names


def test_sampling_a_dev_shape_keeps_tools_and_case_counts():
    report = apply_final_filters(synthetic_rows())
    # Two cases per tool is not a real dev size; this only tests shape.
    sampled = sample_stage(
        report["by_tool"],
        stage="dev",
        seed=43,
        n_tools=2,
        cases_per_tool=2,
    )

    assert sampled["n_tools"] == 2
    assert sampled["n_cases"] == 4
    assert set(sampled["tool_distribution"].values()) == {2}

    with pytest.raises(ValueError):
        sample_stage(
            report["by_tool"],
            stage="dev",
            seed=43,
            n_tools=3,
            cases_per_tool=3,
        )


def test_tool_conversion_uses_the_final_core_required_set():
    definition = convert_tool_definition(GAMMA)

    schema = definition["input_schema"]
    assert schema["type"] == "object"
    assert schema["required"] == ["q"]
    assert set(schema["properties"]) == {"q", "limit", "lang"}
