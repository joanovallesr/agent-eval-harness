"""Schema-free per-case scorer implementing ``docs/MATCH_SPEC.md`` v1.1.

The scorer is deliberately small and inert:

- it makes no API calls;
- it never reads a tool ``input_schema``;
- it never consults gold labels beyond the gold calls for the one case;
- it does not average repeats or compare arms.

**Repeat averaging belongs to the harness, not the scorer.** ``score_case``
scores one final attempt and returns a single result. The harness runs the
scorer once per repeat, averages repeats within a case first, and only then
computes any paired arm difference (EVAL_DESIGN §1).

Public surface:

``parse_gold(raw)``
    Parse and validate a case's ``answers`` field. Raises
    :class:`GoldFormatError` for a row that MATCH_SPEC §1.3 says to exclude
    before the split.

``compare_values(predicted, gold, *, lenient=False)``
    Deep, JSON-type-strict value comparison (MATCH_SPEC §4).

``score_case(predicted_blocks, gold, *, lenient=False)``
    Score one final attempt. Returns ``(exact, partial, format_failure,
    name_only)`` as a :class:`ScoreResult` named tuple.
"""

from __future__ import annotations

import json
import math
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal
from functools import lru_cache
from typing import Any, NamedTuple


class GoldFormatError(ValueError):
    """A gold row must be excluded (MATCH_SPEC §1.3), not scored."""


@dataclass(frozen=True)
class GoldCall:
    """A normalized gold call: a name and the arguments object as given."""

    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class _PredCall:
    """An internally normalized predicted call."""

    name: str | None
    arguments: dict[str, Any] | None = None
    malformed: bool = False


_MALFORMED = _PredCall(name=None, arguments=None, malformed=True)


class ScoreResult(NamedTuple):
    """One attempt's per-case result.

    ``exact`` is the primary 0/1 result. ``partial`` is the secondary
    Dice/F1-style score. ``format_failure`` is a single diagnostic flag; the
    harness derives the ``no_tool_use`` / ``malformed_block`` /
    ``schema_validation_after_retries`` breakdown, because the scorer never
    reads schemas and does not know the validator's verdict. ``name_only`` is
    the name-multiset diagnostic.
    """

    exact: int
    partial: float
    format_failure: bool
    name_only: bool


# ---------------------------------------------------------------------------
# Gold parsing
# ---------------------------------------------------------------------------


def _reject_non_finite(value: str) -> None:
    raise ValueError(f"non-finite JSON number is not allowed: {value}")


def _normalize_gold_value(value: Any, path: str) -> Any:
    """Normalize a gold value as if it had come from strict JSON parsing.

    Floats become ``Decimal(str(x))``, non-finite numbers are rejected, and
    containers are rebuilt recursively. This closes the gap where a caller
    passes an already-parsed list and bypasses ``parse_constant``.
    """

    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise GoldFormatError(f"non-finite gold number at {path}")
        return Decimal(str(value))
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise GoldFormatError(f"non-finite gold number at {path}")
        return value
    if isinstance(value, list):
        return [
            _normalize_gold_value(item, f"{path}[{index}]")
            for index, item in enumerate(value)
        ]
    if isinstance(value, dict):
        return {
            key: _normalize_gold_value(item, f"{path}.{key}")
            for key, item in value.items()
        }
    raise GoldFormatError(f"non-JSON value at {path}: {type(value).__name__}")


def parse_gold(raw: str | list[Any] | tuple[Any, ...]) -> tuple[GoldCall, ...]:
    """Parse and validate a gold ``answers`` value.

    ``raw`` is normally the case's JSON string. An already-parsed list is
    accepted for tests and callers that parsed the row themselves. Floats are
    parsed as :class:`~decimal.Decimal`; duplicate JSON keys follow
    ``json.loads`` last-wins semantics; ``NaN``/``Infinity`` are rejected.

    Raises :class:`GoldFormatError` for every exclusion condition in
    MATCH_SPEC §1.3. A malformed row is excluded and counted before the split;
    it is never scored as a wrong prediction.
    """

    if isinstance(raw, str):
        try:
            data = json.loads(
                raw,
                parse_float=Decimal,
                parse_constant=_reject_non_finite,
            )
        except (json.JSONDecodeError, ValueError, TypeError) as exc:
            raise GoldFormatError(f"gold answers failed to parse: {exc}") from exc
    else:
        data = raw

    if not isinstance(data, (list, tuple)) or len(data) == 0:
        raise GoldFormatError("gold answers must be a non-empty list")

    calls: list[GoldCall] = []
    for index, element in enumerate(data):
        if not isinstance(element, dict):
            raise GoldFormatError(f"gold element {index} is not an object")
        name = element.get("name")
        if not isinstance(name, str) or name == "":
            raise GoldFormatError(
                f"gold element {index} has a missing, non-string, or empty name"
            )
        if "arguments" not in element:
            raise GoldFormatError(f"gold element {index} has no arguments")
        arguments = element["arguments"]
        if not isinstance(arguments, dict):
            raise GoldFormatError(
                f"gold element {index} arguments are not an object "
                "(string-encoded arguments are excluded)"
            )
        calls.append(
            GoldCall(
                name=name,
                arguments=_normalize_gold_value(
                    arguments, f"gold element {index} arguments"
                ),
            )
        )
    return tuple(calls)


def _normalize_gold(gold: Any) -> tuple[GoldCall, ...]:
    if isinstance(gold, str):
        return parse_gold(gold)
    if isinstance(gold, (list, tuple)):
        if len(gold) > 0 and all(isinstance(call, GoldCall) for call in gold):
            calls: list[GoldCall] = []
            for index, call in enumerate(gold):
                if not isinstance(call.name, str) or call.name == "":
                    raise GoldFormatError(
                        f"gold element {index} has a missing, non-string, "
                        "or empty name"
                    )
                if not isinstance(call.arguments, dict):
                    raise GoldFormatError(
                        f"gold element {index} arguments are not an object"
                    )
                calls.append(
                    GoldCall(
                        name=call.name,
                        arguments=_normalize_gold_value(
                            call.arguments, f"gold element {index} arguments"
                        ),
                    )
                )
            return tuple(calls)
        return parse_gold(list(gold))
    raise TypeError("gold must be answers JSON, a list of calls, or GoldCall objects")


# ---------------------------------------------------------------------------
# Value comparison
# ---------------------------------------------------------------------------


def _as_decimal(value: Any) -> Decimal | None:
    """Return a finite Decimal for a JSON number, or None for non-numbers."""

    if isinstance(value, bool):
        # bool is a subclass of int; check it first (MATCH_SPEC §4).
        return None
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            return None
        return Decimal(str(value))
    if isinstance(value, Decimal):
        if not value.is_finite():
            return None
        return value
    return None


def compare_values(predicted: Any, gold: Any, *, lenient: bool = False) -> bool:
    """Deep, JSON-type-strict comparison of one predicted value to gold.

    ``lenient=True`` relaxes **argument string values only**: surrounding
    whitespace is stripped and the value is lowercased with ``str.lower()``
    (not ``casefold``). Names and argument keys are never passed through this
    lenient path by :func:`score_case`. No Unicode normalization is applied.
    """

    if isinstance(gold, bool):
        return isinstance(predicted, bool) and predicted is gold

    if gold is None:
        return predicted is None

    if isinstance(gold, str):
        if not isinstance(predicted, str):
            return False
        if lenient:
            return predicted.strip().lower() == gold.strip().lower()
        return predicted == gold

    if isinstance(gold, (int, float, Decimal)) and not isinstance(gold, bool):
        gold_number = _as_decimal(gold)
        predicted_number = _as_decimal(predicted)
        if gold_number is None or predicted_number is None:
            return False
        return gold_number == predicted_number

    if isinstance(gold, list):
        if not isinstance(predicted, list) or len(predicted) != len(gold):
            return False
        return all(
            compare_values(p, g, lenient=lenient)
            for p, g in zip(predicted, gold)
        )

    if isinstance(gold, dict):
        if not isinstance(predicted, dict):
            return False
        # Key sets must be identical at every depth (MATCH_SPEC §4).
        if set(predicted.keys()) != set(gold.keys()):
            return False
        return all(
            compare_values(predicted[key], gold[key], lenient=lenient)
            for key in gold
        )

    return False


# ---------------------------------------------------------------------------
# Predicted-call extraction
# ---------------------------------------------------------------------------


def _extract_predicted(
    blocks: Any,
) -> tuple[list[_PredCall], bool]:
    """Extract calls from one final attempt's blocks.

    Only ``tool_use`` blocks are calls. Non-``tool_use`` blocks (text,
    thinking) are ignored. A non-object block, or a ``tool_use`` block with a
    missing/non-string/empty name or a non-object ``input``, is malformed.
    """

    if blocks is None:
        blocks = []

    calls: list[_PredCall] = []
    malformed = False
    saw_tool_use = False

    for block in blocks:
        if not isinstance(block, dict):
            malformed = True
            calls.append(_MALFORMED)
            continue
        if block.get("type") != "tool_use":
            continue
        saw_tool_use = True
        name = block.get("name")
        arguments = block.get("input")
        if (
            not isinstance(name, str)
            or name == ""
            or not isinstance(arguments, dict)
        ):
            malformed = True
            calls.append(_MALFORMED)
        else:
            calls.append(_PredCall(name=name, arguments=arguments))

    return calls, (malformed or not saw_tool_use)


# ---------------------------------------------------------------------------
# Per-call and per-case scoring
# ---------------------------------------------------------------------------


def _call_score(
    predicted: _PredCall,
    gold: GoldCall,
    *,
    lenient: bool,
) -> float:
    if predicted.malformed:
        return 0.0
    if predicted.name != gold.name:  # tool names are always strict
        return 0.0

    keys = set(predicted.arguments) | set(gold.arguments)
    if not keys:
        return 1.0

    equal = 0
    for key in keys:
        # A key present on only one side is unequal, never skipped
        # (MATCH_SPEC §5, §7).
        if (
            key in predicted.arguments
            and key in gold.arguments
            and compare_values(
                predicted.arguments[key],
                gold.arguments[key],
                lenient=lenient,
            )
        ):
            equal += 1
    return equal / len(keys)


def _optimal_sum(
    predicted: list[_PredCall],
    gold: tuple[GoldCall, ...],
    *,
    lenient: bool,
) -> float:
    """Exact optimal one-to-one assignment (MATCH_SPEC §3).

    Subset DP over gold calls. Any exact optimum is permitted by the spec;
    this implementation is exact and avoids factorial permutation search.
    """

    @lru_cache(maxsize=None)
    def best(index: int, used_mask: int) -> float:
        if index == len(predicted):
            return 0.0

        # Leave this predicted call unmatched.
        best_score = best(index + 1, used_mask)

        for gold_index, gold_call in enumerate(gold):
            if used_mask & (1 << gold_index):
                continue
            candidate = _call_score(
                predicted[index], gold_call, lenient=lenient
            ) + best(index + 1, used_mask | (1 << gold_index))
            if candidate > best_score:
                best_score = candidate
        return best_score

    return best(0, 0)


def score_case(
    predicted_blocks: Any,
    gold: Any,
    *,
    lenient: bool = False,
) -> ScoreResult:
    """Score one final attempt against one case's gold calls.

    ``predicted_blocks`` are the final assistant message's blocks. ``gold``
    is the answers JSON string, an already-parsed list of
    ``{"name", "arguments"}`` objects, or a tuple of :class:`GoldCall`.

    Repeats are **not** averaged here: the harness calls this once per repeat
    and averages within a case first (EVAL_DESIGN §1).
    """

    gold_calls = _normalize_gold(gold)
    predicted, format_failure = _extract_predicted(predicted_blocks)

    sum_s = _optimal_sum(predicted, gold_calls, lenient=lenient)

    exact = int(
        not format_failure
        and len(predicted) == len(gold_calls)
        and sum_s == len(gold_calls)
    )

    if predicted:
        partial = 2.0 * sum_s / (len(predicted) + len(gold_calls))
    else:
        partial = 0.0

    if format_failure or not predicted:
        name_only = False
    else:
        name_only = Counter(call.name for call in predicted) == Counter(
            call.name for call in gold_calls
        )

    return ScoreResult(
        exact=exact,
        partial=partial,
        format_failure=format_failure,
        name_only=name_only,
    )
