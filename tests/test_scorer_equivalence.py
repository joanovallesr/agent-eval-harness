"""Equivalence tests for the polynomial-time scorer assignment.

The original subset-DP implementation remains available as
``_optimal_sum_dp``. These tests compare it with the Hungarian implementation
used by :func:`score_case`, on randomized small cases, the tracked mini-set,
and any locally materialized Phase-3 cases/traces.
"""

from __future__ import annotations

import json
import random
import time
from collections import Counter
from pathlib import Path

import pytest

from eval.mini_set import load_cases, validate_case
from eval.scorer import (
    GoldCall,
    ScoreResult,
    _MALFORMED,
    _PredCall,
    _extract_predicted,
    _normalize_gold,
    _optimal_sum,
    _optimal_sum_dp,
    score_case,
)

ROOT = Path(__file__).resolve().parents[1]
MINISET = ROOT / "data" / "miniset.jsonl"
PHASE3 = ROOT / "data" / "phase3"
RESULTS = ROOT / "results"
BLOCKED_30_CALL_CASE = "xlam-543ff57751dc268c"


def _score_with_dp(blocks, gold, *, lenient: bool = False) -> ScoreResult:
    gold_calls = _normalize_gold(gold)
    predicted, format_failure = _extract_predicted(blocks)
    sum_s = _optimal_sum_dp(predicted, gold_calls, lenient=lenient)
    exact = int(
        not format_failure
        and len(predicted) == len(gold_calls)
        and sum_s == len(gold_calls)
    )
    partial = (
        2.0 * sum_s / (len(predicted) + len(gold_calls))
        if predicted
        else 0.0
    )
    if format_failure or not predicted:
        name_only = False
    else:
        name_only = Counter(call.name for call in predicted) == Counter(
            call.name for call in gold_calls
        )
    return ScoreResult(exact, partial, format_failure, name_only)


def _gold_blocks(gold_calls) -> list[dict]:
    return [
        {
            "type": "tool_use",
            "id": f"gold-{index}",
            "name": call.name,
            "input": call.arguments,
        }
        for index, call in enumerate(gold_calls)
    ]


def _assert_equivalent(blocks, gold) -> None:
    gold_calls = _normalize_gold(gold)
    predicted, _ = _extract_predicted(blocks)
    assert _optimal_sum(predicted, gold_calls, lenient=False) == pytest.approx(
        _optimal_sum_dp(predicted, gold_calls, lenient=False)
    )
    assert _optimal_sum(predicted, gold_calls, lenient=True) == pytest.approx(
        _optimal_sum_dp(predicted, gold_calls, lenient=True)
    )
    assert score_case(blocks, gold_calls) == _score_with_dp(blocks, gold_calls)


def _random_call(rng: random.Random) -> GoldCall:
    name = rng.choice(["alpha", "beta", "gamma"])
    arguments: dict = {}
    if rng.random() < 0.85:
        arguments["x"] = rng.choice([1, 2, "1", None, True, 0.1])
    if rng.random() < 0.55:
        arguments["y"] = rng.choice([0, 1, [1, 2], {"k": 1}, "v"])
    if rng.random() < 0.20:
        arguments["only_gold"] = "g"
    return GoldCall(name=name, arguments=arguments)


def _random_predicted(rng: random.Random) -> _PredCall:
    if rng.random() < 0.08:
        return _MALFORMED
    call = _random_call(rng)
    if rng.random() < 0.20:
        call.arguments.pop("only_gold", None)
    return _PredCall(name=call.name, arguments=call.arguments)


def test_hungarian_matches_dp_on_randomized_calls_with_ties_and_missing_calls():
    rng = random.Random(20261031)
    for _ in range(250):
        gold = tuple(_random_call(rng) for _ in range(rng.randint(1, 6)))
        predicted = [_random_predicted(rng) for _ in range(rng.randint(0, 7))]
        assert _optimal_sum(
            predicted, gold, lenient=False
        ) == pytest.approx(_optimal_sum_dp(predicted, gold, lenient=False))
        assert _optimal_sum(
            predicted, gold, lenient=True
        ) == pytest.approx(_optimal_sum_dp(predicted, gold, lenient=True))


def test_both_implementations_agree_on_the_tracked_mini_set():
    if not MINISET.exists():
        pytest.skip("mini-set not present")
    rows = [
        json.loads(line)
        for line in MINISET.read_text().splitlines()
        if line.strip()
    ]
    assert rows
    for row in rows:
        gold = _normalize_gold(row["answers"])
        blocks = _gold_blocks(gold)
        _assert_equivalent(blocks, gold)
        assert score_case(blocks, gold).exact == 1


def _phase3_cases() -> list[dict]:
    rows: list[dict] = []
    if not PHASE3.exists():
        return rows
    for path in sorted(PHASE3.glob("*.jsonl")):
        for line in path.read_text().splitlines():
            if line.strip():
                rows.append(json.loads(line))
    return rows


def test_both_implementations_agree_on_tractable_phase3_cases():
    rows = _phase3_cases()
    if not rows:
        pytest.skip("no Phase-3 case files present")
    checked = 0
    for row in rows:
        gold = _normalize_gold(row["answers"])
        # The 30-call case is intentionally excluded from the exponential DP;
        # it is covered by the dedicated performance test below.
        if len(gold) > 8:
            continue
        blocks = _gold_blocks(gold)
        _assert_equivalent(blocks, gold)
        if len(gold) > 1:
            _assert_equivalent(blocks[:-1], gold)
        _assert_equivalent(blocks + [blocks[0]], gold)
        checked += 1
    assert checked >= 100


def _case_gold_map() -> dict[str, str]:
    return {
        str(row["id"]): str(row["answers"])
        for row in _phase3_cases()
    }


def test_both_implementations_agree_on_actual_phase3_traces():
    if not RESULTS.exists():
        pytest.skip("no local results present")
    gold_by_case = _case_gold_map()
    if not gold_by_case:
        pytest.skip("no Phase-3 case files present")
    checked = 0
    for checkpoint in sorted(RESULTS.glob("phase3-*/**/checkpoints/*.json")):
        payload = json.loads(checkpoint.read_text())
        task_id = str(payload.get("task_id"))
        if task_id not in gold_by_case:
            continue
        gold = _normalize_gold(gold_by_case[task_id])
        if len(gold) > 8:
            continue
        blocks = (payload.get("trace") or {}).get("final_attempt_blocks") or []
        _assert_equivalent(blocks, gold)
        checked += 1
    if checked == 0:
        pytest.skip("no tractable local Phase-3 traces present")
    assert checked >= 100


def test_30_call_case_scores_and_validates_in_reasonable_time():
    rows = {row["id"]: row for row in _phase3_cases()}
    row = rows.get(BLOCKED_30_CALL_CASE)
    if row is None:
        pytest.skip("30-call dev case not present")
    cases = {case.id: case for case in load_cases(PHASE3 / "dev.jsonl")}
    case = cases.get(BLOCKED_30_CALL_CASE)
    if case is None:
        pytest.skip("30-call dev case not present")
    gold = _normalize_gold(row["answers"])
    assert len(gold) == 30
    blocks = _gold_blocks(gold)

    started = time.perf_counter()
    validate_case(case)
    validation_elapsed = time.perf_counter() - started
    started = time.perf_counter()
    result = score_case(blocks, gold)
    scoring_elapsed = time.perf_counter() - started

    assert result.exact == 1
    assert result.partial == 1.0
    assert validation_elapsed < 5.0
    assert scoring_elapsed < 5.0


def test_synthetic_30_call_case_scores_quickly():
    gold = tuple(
        GoldCall(name="fixtures_by_date", arguments={"date": f"2023-04-{day:02d}"})
        for day in range(1, 31)
    )
    blocks = _gold_blocks(gold)

    started = time.perf_counter()
    result = score_case(blocks, gold)
    elapsed = time.perf_counter() - started

    assert result.exact == 1
    assert result.partial == 1.0
    assert elapsed < 2.0
