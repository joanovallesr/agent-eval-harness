"""Hand-made pair tests for the MATCH_SPEC v1.1 scorer.

These are the CI-required pairs from MATCH_SPEC §9 plus the malformed,
nested, numeric, and lenient cases added in v1.1. No real xLAM case is
scored here.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from eval.scorer import (
    GoldFormatError,
    compare_values,
    parse_gold,
    score_case,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def block(name: str, arguments: dict | None = None, **extra) -> dict:
    """Build an Anthropic-shaped tool_use block."""
    payload = {
        "type": "tool_use",
        "id": "toolu_test",
        "name": name,
        "input": {} if arguments is None else arguments,
    }
    payload.update(extra)
    return payload


def gold(name: str, arguments: dict | None = None) -> dict:
    return {"name": name, "arguments": {} if arguments is None else arguments}


def score(predicted, gold_calls, *, lenient: bool = False):
    return score_case(predicted, gold_calls, lenient=lenient)


# ---------------------------------------------------------------------------
# Exact match basics
# ---------------------------------------------------------------------------


def test_correct_call_scores_exact_and_full_partial():
    result = score([block("get_product", {"id": 5})], [gold("get_product", {"id": 5})])

    assert result.exact == 1
    assert result.partial == 1.0
    assert result.format_failure is False
    assert result.name_only is True


def test_call_order_is_irrelevant():
    predicted = [block("b", {"x": 2}), block("a", {"x": 1})]
    gold_calls = [gold("a", {"x": 1}), gold("b", {"x": 2})]

    result = score(predicted, gold_calls)

    assert result.exact == 1
    assert result.partial == 1.0


def test_tool_name_is_case_sensitive():
    result = score([block("Get_Product", {"id": 5})], [gold("get_product", {"id": 5})])

    assert result.exact == 0
    assert result.partial == 0.0
    assert result.name_only is False


def test_extra_and_missing_calls_partial_is_half():
    predicted = [block("a", {"x": 1}), block("c", {"z": 3})]
    gold_calls = [gold("a", {"x": 1}), gold("b", {"y": 2})]

    result = score(predicted, gold_calls)

    assert result.exact == 0
    assert result.partial == pytest.approx(0.5)
    assert result.name_only is False


# ---------------------------------------------------------------------------
# Argument values
# ---------------------------------------------------------------------------


def test_string_five_is_not_integer_five():
    result = score([block("t", {"x": "5"})], [gold("t", {"x": 5})])

    assert result.exact == 0
    assert result.partial == 0.0


def test_integer_five_equals_float_five():
    result = score([block("t", {"x": 5})], [gold("t", {"x": 5.0})])

    assert result.exact == 1
    assert result.partial == 1.0


def test_float_point_one_equals_decimal_point_one():
    result = score([block("t", {"x": 0.1})], [gold("t", {"x": Decimal("0.1")})])

    assert result.exact == 1


def test_parsed_gold_float_is_decimal_and_matches_int():
    gold_calls = parse_gold('[{"name": "t", "arguments": {"x": 5.0}}]')

    assert gold_calls[0].arguments["x"] == Decimal("5.0")
    assert score([block("t", {"x": 5})], gold_calls).exact == 1


def test_true_is_not_one_and_false_is_not_zero():
    assert score([block("t", {"x": True})], [gold("t", {"x": 1})]).exact == 0
    assert score([block("t", {"x": False})], [gold("t", {"x": 0})]).exact == 0


def test_null_matches_only_null():
    assert score([block("t", {"x": None})], [gold("t", {"x": None})]).exact == 1
    assert score([block("t", {"x": "null"})], [gold("t", {"x": None})]).exact == 0


def test_string_case_is_strict_but_lenient_lowercases():
    strict = score([block("t", {"x": "Beta"})], [gold("t", {"x": "beta"})])
    lenient = score(
        [block("t", {"x": "Beta"})], [gold("t", {"x": "beta"})], lenient=True
    )

    assert strict.exact == 0
    assert lenient.exact == 1
    assert lenient.partial == 1.0


def test_surrounding_whitespace_is_strict_but_lenient_strips():
    strict = score([block("t", {"x": " beta "})], [gold("t", {"x": "beta"})])
    lenient = score(
        [block("t", {"x": " beta "})], [gold("t", {"x": "beta"})], lenient=True
    )

    assert strict.exact == 0
    assert lenient.exact == 1


def test_missing_argument_is_unequal_not_skipped():
    result = score([block("t", {"a": 1})], [gold("t", {"a": 1, "b": 2})])

    assert result.exact == 0
    assert result.partial == pytest.approx(0.5)


def test_extra_argument_is_unequal():
    result = score([block("t", {"a": 1, "b": 2})], [gold("t", {"a": 1})])

    assert result.exact == 0
    assert result.partial == pytest.approx(0.5)


def test_one_wrong_argument_of_two_gives_half_partial():
    result = score([block("t", {"a": 1, "b": 3})], [gold("t", {"a": 1, "b": 2})])

    assert result.exact == 0
    assert result.partial == pytest.approx(0.5)


def test_list_order_is_significant():
    result = score([block("t", {"xs": [2, 1]})], [gold("t", {"xs": [1, 2]})])

    assert result.exact == 0
    assert result.partial == 0.0


def test_zero_argument_calls_score_one():
    result = score([block("t", {})], [gold("t", {})])

    assert result.exact == 1
    assert result.partial == 1.0


# ---------------------------------------------------------------------------
# Nested key sets
# ---------------------------------------------------------------------------


def test_nested_extra_key_breaks_exact_and_single_top_level_key_partial():
    result = score(
        [block("t", {"outer": {"x": 1, "y": 2}})],
        [gold("t", {"outer": {"x": 1}})],
    )

    assert result.exact == 0
    assert result.partial == 0.0


def test_nested_mismatch_with_two_top_level_keys_gives_half():
    result = score(
        [block("t", {"outer": {"x": 1, "y": 2}, "b": 2})],
        [gold("t", {"outer": {"x": 1}, "b": 2})],
    )

    assert result.exact == 0
    assert result.partial == pytest.approx(0.5)


def test_nested_missing_key_is_unequal():
    result = score(
        [block("t", {"outer": {"x": 1}})],
        [gold("t", {"outer": {"x": 1, "y": 2}})],
    )

    assert result.exact == 0


# ---------------------------------------------------------------------------
# Counts, multiplicity, and the F1 denominator
# ---------------------------------------------------------------------------


def test_same_tool_twice_different_arguments_one_predicted():
    predicted = [block("t", {"id": 1})]
    gold_calls = [gold("t", {"id": 1}), gold("t", {"id": 2})]

    result = score(predicted, gold_calls)

    assert result.exact == 0
    assert result.partial == pytest.approx(2 / 3)


def test_repeated_identical_calls_use_multiset_multiplicity():
    predicted_two = [block("t", {}), block("t", {})]
    gold_two = [gold("t", {}), gold("t", {})]

    full = score(predicted_two, gold_two)
    one = score([block("t", {})], gold_two)

    assert full.exact == 1
    assert full.partial == 1.0
    assert one.exact == 0
    assert one.partial == pytest.approx(2 / 3)


def test_f1_denominator_with_more_predictions_than_gold():
    predicted = [block("t", {"x": 1}), block("t", {"x": 2})]
    gold_calls = [gold("t", {"x": 1})]

    result = score(predicted, gold_calls)

    assert result.exact == 0
    assert result.partial == pytest.approx(2 / 3)


def test_f1_denominator_with_more_gold_than_predictions():
    predicted = [block("t", {"x": 1})]
    gold_calls = [gold("t", {"x": 1}), gold("t", {"x": 2})]

    result = score(predicted, gold_calls)

    assert result.exact == 0
    assert result.partial == pytest.approx(2 / 3)


def test_exact_partial_value_for_mixed_call_quality():
    predicted = [block("a", {"x": 1}), block("b", {"y": 5})]
    gold_calls = [gold("a", {"x": 1}), gold("b", {"y": 5, "z": 6})]

    result = score(predicted, gold_calls)

    assert result.exact == 0
    assert result.partial == pytest.approx(0.75)


def test_name_only_can_be_true_when_arguments_are_wrong():
    result = score([block("t", {"x": 1})], [gold("t", {"x": 2})])

    assert result.exact == 0
    assert result.name_only is True


# ---------------------------------------------------------------------------
# Format failures and malformed blocks
# ---------------------------------------------------------------------------


def test_empty_prediction_scores_zero():
    result = score([], [gold("t", {"x": 1})])

    assert result.exact == 0
    assert result.partial == 0.0
    assert result.format_failure is True
    assert result.name_only is False


def test_text_only_reply_is_a_format_failure():
    result = score(
        [{"type": "text", "text": '{"name": "t", "arguments": {"x": 1}}'}],
        [gold("t", {"x": 1})],
    )

    assert result.exact == 0
    assert result.partial == 0.0
    assert result.format_failure is True


def test_text_beside_a_matching_tool_use_is_ignored():
    predicted = [
        {"type": "text", "text": "I will call the tool."},
        block("t", {"x": 1}),
    ]

    result = score(predicted, [gold("t", {"x": 1})])

    assert result.exact == 1
    assert result.format_failure is False


@pytest.mark.parametrize(
    "bad_block",
    [
        {"type": "tool_use", "name": "t"},  # missing input
        {"type": "tool_use", "input": {"x": 1}},  # missing name
        {"type": "tool_use", "name": "", "input": {"x": 1}},  # empty name
        {"type": "tool_use", "name": 7, "input": {"x": 1}},  # non-string name
        {"type": "tool_use", "name": "t", "input": "{}"},  # string input
        {"type": "tool_use", "name": "t", "input": []},  # list input
        "not an object",  # non-object block
    ],
)
def test_malformed_block_scores_zero(bad_block):
    result = score([bad_block], [gold("t", {"x": 1})])

    assert result.exact == 0
    assert result.partial == 0.0
    assert result.format_failure is True


def test_malformed_block_forces_exact_zero_even_beside_a_match():
    predicted = [block("t", {"x": 1}), {"type": "tool_use", "name": "t"}]

    result = score(predicted, [gold("t", {"x": 1})])

    assert result.exact == 0
    assert result.partial == pytest.approx(2 / 3)
    assert result.format_failure is True
    assert result.name_only is False


def test_non_finite_predicted_number_never_matches():
    result = score([block("t", {"x": float("nan")})], [gold("t", {"x": 1})])

    assert result.exact == 0
    assert compare_values(float("nan"), 1) is False
    assert compare_values(float("inf"), 1) is False


def test_other_block_fields_are_not_arguments():
    predicted = [block("t", {"x": 1}, id="toolu_other", extra="ignored")]

    result = score(predicted, [gold("t", {"x": 1})])

    assert result.exact == 1


# ---------------------------------------------------------------------------
# Lenient variant scope
# ---------------------------------------------------------------------------


def test_lenient_does_not_relax_tool_names():
    result = score(
        [block("Get_Product", {"x": "BETA"})],
        [gold("get_product", {"x": "beta"})],
        lenient=True,
    )

    assert result.exact == 0
    assert result.partial == 0.0


def test_lenient_does_not_relax_argument_keys():
    result = score([block("t", {"X": "beta"})], [gold("t", {"x": "beta"})], lenient=True)

    assert result.exact == 0
    assert result.partial == 0.0


def test_no_unicode_normalization_strict_or_lenient():
    precomposed = "\u00e9"
    decomposed = "e\u0301"

    assert compare_values(precomposed, decomposed) is False
    assert compare_values(precomposed, decomposed, lenient=True) is False


# ---------------------------------------------------------------------------
# Gold parsing and exclusion
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        "not json",
        "[]",  # empty answers
        '[{"arguments": {}}]',  # missing name
        '[{"name": "", "arguments": {}}]',  # empty name
        '[{"name": 7, "arguments": {}}]',  # non-string name
        '[{"name": "t"}]',  # missing arguments
        '[{"name": "t", "arguments": "{\\"x\\": 1}"}]',  # string-encoded
        '[{"name": "t", "arguments": []}]',  # non-object arguments
        '["not an object"]',
        '[{"name": "t", "arguments": {"x": NaN}}]',
        '[{"name": "t", "arguments": {"x": Infinity}}]',
    ],
)
def test_bad_gold_rows_are_excluded(raw):
    with pytest.raises(GoldFormatError):
        parse_gold(raw)


def test_duplicate_json_keys_follow_last_wins():
    gold_calls = parse_gold('[{"name": "t", "arguments": {"a": 1, "a": 2}}]')

    assert gold_calls[0].arguments == {"a": 2}


def test_parse_gold_accepts_already_parsed_lists():
    gold_calls = parse_gold([{"name": "t", "arguments": {"x": 1}}])

    assert len(gold_calls) == 1
    assert gold_calls[0].name == "t"


def test_score_case_accepts_raw_gold_json():
    result = score_case(
        [block("t", {"x": 1})],
        '[{"name": "t", "arguments": {"x": 1}}]',
    )

    assert result.exact == 1


# ---------------------------------------------------------------------------
# compare_values unit rules
# ---------------------------------------------------------------------------


def test_compare_values_number_and_bool_rules():
    assert compare_values(5, Decimal("5")) is True
    assert compare_values(5, 5.0) is True
    assert compare_values(True, 1) is False
    assert compare_values(1, True) is False
    assert compare_values(False, 0) is False
    assert compare_values("5", 5) is False
    assert compare_values(None, None) is True
    assert compare_values("null", None) is False


def test_compare_values_nested_key_sets():
    assert compare_values({"a": {"x": 1}}, {"a": {"x": 1}}) is True
    assert compare_values({"a": {"x": 1}}, {"a": {"x": 1, "y": 2}}) is False
    assert compare_values({"a": 1, "b": 2}, {"b": 2, "a": 1}) is True


def test_compare_values_list_order():
    assert compare_values([1, 2], [1, 2]) is True
    assert compare_values([2, 1], [1, 2]) is False
    assert compare_values([1, 2], [1, 2, 3]) is False
