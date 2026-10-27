"""Tests for the EVAL_DESIGN §7 m/u usable-cases sensitivity."""

from __future__ import annotations

from collections import Counter
from types import SimpleNamespace

import pytest

from audit_variance_m import (
    build_sensitivity,
    pessimistic_u,
    render_table,
)


def fake_report(structural: dict, filtered: dict) -> SimpleNamespace:
    return SimpleNamespace(
        single_tool_counts=Counter(structural),
        required_filtered_single_tool_counts=Counter(filtered),
    )


def test_pessimistic_u_matches_the_section_7_formula() -> None:
    # Worked value from the pinned §7 variance formula at m = 59.
    assert pessimistic_u(59) == pytest.approx(1.0902, abs=1e-4)
    assert pessimistic_u(13) > pessimistic_u(59) > pessimistic_u(2508) > 1.0
    with pytest.raises(ValueError):
        pessimistic_u(1)


def test_build_sensitivity_excludes_candidates_and_counts_thresholds() -> None:
    # "average" is a frozen candidate, so it is not a dev tool.
    structural = {
        "t1": 1,
        "t2": 2,
        "t3": 5,
        "t4": 10,
        "t5": 20,
        "t6": 50,
        "t7": 100,
        "average": 100,
    }
    filtered = {
        "t1": 1,
        "t2": 2,
        "t3": 5,
        "t4": 10,
        "t5": 19,
        "t6": 49,
        "t7": 99,
        "average": 100,
    }

    sensitivity = build_sensitivity(fake_report(structural, filtered))

    assert sensitivity.structural_total == 7
    assert sensitivity.filtered_total == 7
    assert sensitivity.structural_exactly_one == 1
    assert sensitivity.filtered_exactly_one == 1

    rows = {row.threshold: row for row in sensitivity.rows}
    assert rows[2].structural_m == 6
    assert rows[2].filtered_m == 6
    assert rows[20].structural_m == 3
    assert rows[20].filtered_m == 2
    assert rows[100].structural_m == 1
    # Zero all-filter tools at the floor cannot estimate within-tool variance.
    assert rows[100].filtered_u is None

    table = render_table(sensitivity)
    assert "—" in table
    assert "(chosen rule; illustration)" in table
    assert "(sensitivity only)" in table


def test_build_sensitivity_handles_empty_tool_pools() -> None:
    sensitivity = build_sensitivity(fake_report({}, {}))

    assert sensitivity.structural_exactly_one == 0
    assert sensitivity.structural_total == 0
    assert all(row.structural_u is None for row in sensitivity.rows)
    assert all(row.filtered_u is None for row in sensitivity.rows)
