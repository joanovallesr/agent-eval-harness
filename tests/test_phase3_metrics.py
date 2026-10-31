"""Offline Phase-3 analysis tests: intervals, components, sizing, C/n_t."""

from __future__ import annotations

import math

import pytest

from eval.metrics import (
    CasePair,
    aggregate_cost_ratio,
    analyse_paired,
    bootstrap_cost_ratio_ci,
    bootstrap_mean_ci,
    build_case_pairs,
    cost_ratio_interval,
    discordant_rate,
    m_from_tool_counts,
    per_tool_stats,
    pessimistic_u,
    power_multiplier,
    power_passes,
    primary_interval,
    required_n_t,
    s_pess_from_components,
    sample_variance,
    select_c,
    t_interval,
    variance_components,
)


def pair(tool: str, task_id: str, agent: float, baseline: float, *, cost=(1.0, 2.0)):
    return CasePair(
        task_id=task_id,
        tool=tool,
        agent_exact=agent,
        baseline_exact=baseline,
        agent_cost=cost[0],
        baseline_cost=cost[1],
        agent_input_tokens=10.0,
        agent_output_tokens=5.0,
        baseline_input_tokens=10.0,
        baseline_output_tokens=5.0,
    )


def test_t_interval_matches_hand_computed_values():
    interval = t_interval([1, 2, 3, 4, 5])

    assert interval.mean == pytest.approx(3.0)
    assert interval.sd == pytest.approx(math.sqrt(2.5))
    assert interval.se == pytest.approx(math.sqrt(2.5) / math.sqrt(5))
    # t(0.975, 4) = 2.776445105...
    assert interval.low == pytest.approx(3.0 - 2.776445105 * math.sqrt(2.5 / 5))
    assert interval.high == pytest.approx(3.0 + 2.776445105 * math.sqrt(2.5 / 5))


def test_u_matches_known_chi_square_quantiles():
    # chi2.ppf(0.20, 9) = 5.380053211773292
    assert pessimistic_u(10) == pytest.approx(1.2933854444729525)
    # chi2.ppf(0.20, 99) = 87.0052119108777
    assert pessimistic_u(100) == pytest.approx(1.0667065522004338)
    with pytest.raises(ValueError):
        pessimistic_u(1)


def test_power_multiplier_and_inequality_match_the_worked_check():
    # EVAL_DESIGN §7 worked check: at C=6, 80% power at Delta=0 needs
    # s_pess < 0.05 / (3.491/sqrt(6)) = 3.509 points.
    assert power_multiplier(6) == pytest.approx(1.42483781618598)
    assert 0.05 / power_multiplier(6) == pytest.approx(0.03509171319851721)
    assert power_multiplier(8) == pytest.approx(1.152815240448928)
    assert power_passes(0.03, 6)
    assert not power_passes(0.036, 6)
    assert power_passes(0.04, 8)
    assert not power_passes(0.04, 6)


def test_required_n_t_and_s_pess_components():
    assert required_n_t(0.04, 0.01) == 100
    assert required_n_t(0.5, 0.001) == 2000
    assert required_n_t(0.04, 0.0) == 100
    # One n_t is the maximum of the per-tool requirements.
    assert required_n_t([0.04, 0.5, 0.01], 0.001) == 2000

    s_pess = s_pess_from_components(
        tau2=0.02,
        sigma2_within=0.03333333333333333,
        n_t=100,
        u=pessimistic_u(3),
    )
    assert s_pess == pytest.approx(0.299935, rel=1e-5)


def test_variance_components_separate_within_and_between_components():
    pairs = []
    for index in range(10):
        pairs.append(pair("a", f"a{index}", 0.0, 0.0))
    # Tool a differences: nine 0.1 and one 1.0 -> mean 0.1, variance 0.1.
    pairs = [
        pair("a", f"a{index}", 1.0 if index == 0 else 0.0, 0.0)
        for index in range(10)
    ]
    pairs += [pair("b", f"b{index}", 0.2, 0.0) for index in range(10)]
    pairs += [pair("c", f"c{index}", 0.4, 0.0) for index in range(10)]

    stats = per_tool_stats(pairs)
    assert stats["a"].mean_difference == pytest.approx(0.1)
    assert stats["a"].sigma2_within == pytest.approx(0.1)
    assert stats["b"].sigma2_within == pytest.approx(0.0)

    components = variance_components(pairs)
    assert components["observed_loto_var"] == pytest.approx(0.023333333333333334)
    assert components["mean_within_noise"] == pytest.approx(0.0033333333333333335)
    assert components["tau2"] == pytest.approx(0.02)
    assert components["sigma2_within"] == pytest.approx(1.0 / 30.0)
    assert components["m"] == 3
    assert components["u"] == pytest.approx(pessimistic_u(3))


def test_primary_interval_is_over_per_tool_means_not_cases():
    pairs = []
    for index in range(10):
        pairs.append(pair("a", f"a{index}", 1.0, 0.8))
    for index in range(10):
        pairs.append(pair("b", f"b{index}", 0.0, 0.1))
    for index in range(10):
        pairs.append(pair("c", f"c{index}", 0.5, 0.5))

    interval = primary_interval(pairs)

    assert interval.n == 3
    assert interval.mean == pytest.approx((0.2 + -0.1 + 0.0) / 3)
    assert interval.sd == pytest.approx(sample_variance([0.2, -0.1, 0.0]) ** 0.5)


def test_cost_ratio_interval_and_aggregate():
    pairs = [
        pair("a", "a1", 1.0, 1.0, cost=(0.05, 1.0)),
        pair("b", "b1", 1.0, 1.0, cost=(0.06, 1.0)),
        pair("c", "c1", 1.0, 1.0, cost=(0.07, 1.0)),
    ]

    cost = cost_ratio_interval(pairs)

    assert cost["ratio"] == pytest.approx(math.exp((math.log(0.05) + math.log(0.06) + math.log(0.07)) / 3))
    assert cost["low"] < cost["ratio"] < cost["high"]
    assert aggregate_cost_ratio(pairs) == pytest.approx(0.18 / 3.0)


def test_discordant_rate_uses_case_level_pairs():
    pairs = [
        pair("a", "a1", 1.0, 1.0),
        pair("a", "a2", 1.0, 0.0),
        pair("b", "b1", 0.0, 1.0),
        pair("b", "b2", 0.0, 0.0),
    ]
    assert discordant_rate(pairs) == pytest.approx(0.5)


def test_m_counts_only_tools_at_or_above_the_threshold():
    assert m_from_tool_counts({"a": 9, "b": 10, "c": 11}) == 2
    assert m_from_tool_counts([1, 5, 10, 20]) == 2


def test_select_c_is_the_smallest_passing_c():
    assert select_c(0.03) == 6
    assert select_c(0.036) == 7
    assert select_c(0.042) == 8
    assert select_c(0.05) is None
    budget = {6: False, 7: True, 8: True}
    assert select_c(0.03, budget_ok=budget) == 7


def test_bootstrap_is_deterministic_and_stable():
    values = [0.0, 0.1, 0.2, 0.3, 0.4]
    first = bootstrap_mean_ci(values, resamples=500, seed=7)
    second = bootstrap_mean_ci(values, resamples=500, seed=7)

    assert first == second
    assert first["low"] <= first["mean"] <= first["high"]

    pairs = [pair("a", f"a{i}", 1.0, 1.0, cost=(0.05, 1.0)) for i in range(5)]
    assert bootstrap_cost_ratio_ci(pairs, resamples=200, seed=3)["ratio"] == pytest.approx(0.05)


def test_build_case_pairs_averages_repeats_within_case_first():
    agent = [
        {"task_id": "a1", "tool_name": "a", "exact": 1, "cost_usd": 0.1, "input_tokens": 10, "output_tokens": 2},
        {"task_id": "a1", "tool_name": "a", "exact": 0, "cost_usd": 0.3, "input_tokens": 30, "output_tokens": 6},
        {"task_id": "b1", "tool_name": "b", "exact": 1, "cost_usd": 0.2, "input_tokens": 20, "output_tokens": 4},
    ]
    baseline = [
        {"task_id": "a1", "tool_name": "a", "exact": 0, "cost_usd": 0.2, "input_tokens": 20, "output_tokens": 4},
        {"task_id": "a1", "tool_name": "a", "exact": 0, "cost_usd": 0.2, "input_tokens": 20, "output_tokens": 4},
        {"task_id": "b1", "tool_name": "b", "exact": 1, "cost_usd": 0.2, "input_tokens": 20, "output_tokens": 4},
    ]

    pairs = build_case_pairs(agent, baseline)

    by_id = {item.task_id: item for item in pairs}
    assert by_id["a1"].agent_exact == pytest.approx(0.5)
    assert by_id["a1"].agent_cost == pytest.approx(0.2)
    assert by_id["a1"].difference == pytest.approx(0.5)

    with pytest.raises(ValueError, match="unequal completed repeat"):
        build_case_pairs(agent, baseline[:1] + baseline[2:])


def synthetic_records(tools=("a", "b", "c"), n=10):
    agent = []
    baseline = []
    for tool_index, tool in enumerate(tools):
        for index in range(n):
            task_id = f"{tool}{index}"
            agent_exact = 1
            baseline_exact = 1 if index < (8 - tool_index) else 0
            agent.append(
                {
                    "task_id": task_id,
                    "tool_name": tool,
                    "exact": agent_exact,
                    "cost_usd": 0.05,
                    "input_tokens": 100,
                    "output_tokens": 20,
                    "raw_usage": {
                        "output_tokens_details": {"thinking_tokens": 5}
                    },
                }
            )
            baseline.append(
                {
                    "task_id": task_id,
                    "tool_name": tool,
                    "exact": baseline_exact,
                    "cost_usd": 1.0,
                    "input_tokens": 300,
                    "output_tokens": 40,
                    "raw_usage": {
                        "output_tokens_details": {"thinking_tokens": 0}
                    },
                }
            )
    return agent, baseline


def test_analyse_paired_reports_primary_secondary_inputs_and_sizing():
    agent, baseline = synthetic_records()

    report = analyse_paired(
        agent,
        baseline,
        label="test",
        resamples=300,
        seed=11,
    )

    assert report["n_cases"] == 30
    assert report["n_tools"] == 3
    assert report["primary"]["n"] == 3
    assert report["primary"]["mean"] == pytest.approx((0.2 + 0.3 + 0.4) / 3)
    assert report["aggregate_cost_ratio"] == pytest.approx(0.05)
    assert report["cost_ratio"]["ratio"] == pytest.approx(0.05)
    assert report["tokens_per_case"]["agent_thinking"] == pytest.approx(5.0)
    sizing = report["sizing"]
    assert sizing["components"]["m"] == 3
    assert sizing["n_t"] >= 100
    assert sizing["proposed_C"] in (6, 7, 8, None)
    assert set(sizing["power"]) == {"6", "7", "8"}
