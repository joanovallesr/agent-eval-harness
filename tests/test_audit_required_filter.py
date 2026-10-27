"""Tests for the interim required-arguments audit tooling.

These use tiny in-memory rows; they never touch the xLAM dataset, an API, or a
candidate tool. The seams under test are the public helpers in
``scripts/audit_required_filter.py``.
"""

from __future__ import annotations

import json

import pytest

from audit_required_filter import (
    CATEGORY_CORE,
    CATEGORY_FILTER,
    CATEGORY_OPTIONAL,
    analyze_required_filter,
    categorize_parameter,
    render_markdown,
    sample_indices,
)


def param(description: str) -> dict:
    return {"type": "string", "description": description}


def tool(name: str, parameters: dict) -> dict:
    return {"name": name, "description": "test tool", "parameters": parameters}


def row(tools: list[dict], answers: list[dict], *, query: str = "not-read") -> dict:
    return {
        "id": "row-test",
        "query": query,
        "tools": json.dumps(tools),
        "answers": json.dumps(answers),
    }


def test_categorize_parameter_uses_name_then_description() -> None:
    # A filter/sort name wins over the description's default language.
    assert (
        categorize_parameter("descending", "Defaults to False.")
        == CATEGORY_FILTER
    )
    # A documented default without a literal `default` key is optional by text.
    assert categorize_parameter("alpha", "Defaults to 0.05.") == CATEGORY_OPTIONAL
    assert (
        categorize_parameter(
            "n", "The number of subdivisions. Defaults to 10000."
        )
        == CATEGORY_OPTIONAL
    )
    # No optional/default language and no filter/sort name: core input.
    assert (
        categorize_parameter("function", "The function to differentiate.")
        == CATEGORY_CORE
    )


def test_analysis_reproduces_structure_and_categories() -> None:
    rows = [
        # Optional-by-name/description: interim excludes, refined keeps.
        row(
            [
                tool(
                    "sort_numbers",
                    {
                        "numbers": param("The list of numbers."),
                        "descending": param("Defaults to False."),
                    },
                )
            ],
            [{"name": "sort_numbers", "arguments": {"numbers": [1, 2]}}],
        ),
        # Core input: both the interim and refined rules exclude.
        row(
            [tool("differentiate", {"function": param("The function.")})],
            [{"name": "differentiate", "arguments": {}}],
        ),
        # Complete call: never excluded.
        row(
            [tool("add", {"a": param("First addend.")})],
            [{"name": "add", "arguments": {"a": 1}}],
        ),
        # String-encoded arguments: malformed, excluded before filtering.
        row(
            [tool("add", {"a": param("First addend.")})],
            [{"name": "add", "arguments": '{"a": 1}'}],
        ),
        # Missing tool name: malformed.
        row([tool("add", {"a": param("First addend.")})], [{"arguments": {}}]),
        # Empty answers: excluded before filtering.
        row([tool("add", {"a": param("First addend.")})], []),
        # Name not present in the row's tools: unknown, excluded.
        row(
            [tool("add", {"a": param("First addend.")})],
            [{"name": "ghost", "arguments": {}}],
        ),
    ]

    report = analyze_required_filter(rows)

    assert report.rows == 7
    assert report.empty_answer_rows == 1
    assert report.malformed_gold_rows == 2
    assert report.unknown_name_rows == 1
    assert report.excluded_rows == 2
    assert report.refined_excluded_rows == 1
    assert report.missing_instances == 2
    assert report.category_instances[CATEGORY_FILTER] == 1
    assert report.category_instances[CATEGORY_CORE] == 1
    assert report.category_instances[CATEGORY_OPTIONAL] == 0
    assert report.single_tool_counts["sort_numbers"] == 1
    assert report.interim_filtered_single_tool_counts.get("sort_numbers", 0) == 0
    assert report.refined_filtered_single_tool_counts["sort_numbers"] == 1


def test_analysis_counts_other_arguments_and_empty_calls() -> None:
    rows = [
        row(
            [
                tool(
                    "two_inputs",
                    {"x": param("Core x."), "y": param("Core y.")},
                )
            ],
            [{"name": "two_inputs", "arguments": {"y": 2}}],
        ),
        row(
            [tool("one_input", {"x": param("Core x.")})],
            [{"name": "one_input", "arguments": {}}],
        ),
    ]

    report = analyze_required_filter(rows)

    assert report.excluded_rows == 2
    assert report.missing_instances == 2
    assert report.rows_with_other_arguments == 1
    assert report.rows_with_empty_arguments == 1
    assert report.rows_by_distinct_missing[1] == 2


def test_distinct_missing_parameters_are_counted_per_row() -> None:
    rows = [
        row(
            [tool("two_inputs", {"a": param("Core a."), "b": param("Core b.")})],
            [{"name": "two_inputs", "arguments": {}}],
        )
    ]

    report = analyze_required_filter(rows)

    assert report.missing_instances == 2
    assert report.rows_by_distinct_missing[2] == 1
    assert report.rows_by_instance_count[2] == 1


def test_sample_is_reproducible_and_seeded() -> None:
    indices = list(range(100))

    first = sample_indices(indices, seed=42, size=10)
    second = sample_indices(indices, seed=42, size=10)
    different = sample_indices(indices, seed=7, size=10)

    assert first == second
    assert len(first) == 10
    assert len(set(first)) == 10
    assert first != different
    with pytest.raises(ValueError):
        sample_indices(indices, seed=42, size=101)


def test_report_never_renders_queries_or_gold_arguments() -> None:
    rows = [
        row(
            [tool("one_input", {"x": param("Core x.")})],
            [{"name": "one_input", "arguments": {}}],
            query="SECRET QUERY TEXT",
        )
    ]
    report = analyze_required_filter(rows)

    markdown = render_markdown(report, sample_size=1)

    assert "SECRET QUERY TEXT" not in markdown
    assert "one_input" in markdown
    assert "Core x." in markdown
