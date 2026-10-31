"""Loader and validator for the hand-written mini-set.

The mini-set is a Phase-2 pipeline smoke test and the seed of the later
multi-step set. It is excluded from every headline number.

Validation proves three things before any API call:

1. every gold call names a tool offered in that case;
2. every gold call conforms to that tool's own JSON Schema; and
3. the Phase-1 scorer parses the gold row and scores a gold-shaped final
   attempt as an exact match.

It also rejects any frozen held-out candidate tool name.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from jsonschema import Draft202012Validator

from agent.tools import TOOLS_BY_NAME
from eval.config import MINI_SET_LABEL, MINI_SET_PATH
from eval.scorer import GoldFormatError, parse_gold, score_case

# Frozen Phase-0 candidates (docs/CANDIDATES.md). Mini-set tasks must not use
# any of them, as gold or as a distractor.
CANDIDATE_TOOLS = frozenset(
    {
        "calculate_standard_deviation",
        "average",
        "get_ip_location",
        "find_pairs_with_sum",
        "find_majority_element",
        "chi_square_independence_test",
        "calculate_quarterly_dividend",
        "calculate_investment_return",
        "get_order",
        "find_kth_smallest_number",
        "auto_complete",
        "assess_diabetes_risk",
    }
)


class MiniSetError(ValueError):
    """The mini-set is malformed or inconsistent with the tool schemas."""


@dataclass(frozen=True)
class MiniCase:
    id: str
    query: str
    tools: tuple[str, ...]
    answers: str
    split: str = "mini"
    label: str = "smoke"
    notes: str = ""

    def to_task(self) -> dict[str, Any]:
        """Return the model-facing task.

        Gold ``answers`` are deliberately omitted: the agent loop must never
        see them, and nothing in the loop needs them for repair decisions.
        """

        return {
            "id": self.id,
            "query": self.query,
            "tools": list(self.tools),
            "split": self.split,
            "label": self.label,
            "notes": self.notes,
        }


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise MiniSetError(f"{path}:{line_number}: invalid JSON: {exc}") from exc
            if not isinstance(row, dict):
                raise MiniSetError(f"{path}:{line_number}: row must be an object")
            rows.append(row)
    return rows


def load_cases(path: Path | str = MINI_SET_PATH) -> list[MiniCase]:
    """Load JSONL cases without validating them."""

    rows = _read_jsonl(Path(path))
    cases: list[MiniCase] = []
    for index, row in enumerate(rows, start=1):
        task_id = row.get("id")
        query = row.get("query")
        tools = row.get("tools")
        answers = row.get("answers")
        if not isinstance(task_id, str) or not task_id:
            raise MiniSetError(f"row {index}: missing non-empty string 'id'")
        if not isinstance(query, str) or not query.strip():
            raise MiniSetError(f"{task_id}: missing non-empty string 'query'")
        if (
            not isinstance(tools, list)
            or not tools
            or not all(isinstance(name, str) and name for name in tools)
        ):
            raise MiniSetError(f"{task_id}: 'tools' must be a non-empty list of names")
        if isinstance(answers, str):
            answers_text = answers
        elif isinstance(answers, list):
            answers_text = json.dumps(answers)
        else:
            raise MiniSetError(f"{task_id}: 'answers' must be a JSON string or list")
        cases.append(
            MiniCase(
                id=task_id,
                query=query,
                tools=tuple(tools),
                answers=answers_text,
                split=str(row.get("split", "mini")),
                label=str(row.get("label", "smoke")),
                notes=str(row.get("notes", "")),
            )
        )
    return cases


def _gold_blocks(gold: Any) -> list[dict[str, Any]]:
    return [
        {
            "type": "tool_use",
            "id": f"gold-{index}",
            "name": call.name,
            "input": call.arguments,
        }
        for index, call in enumerate(gold)
    ]


def validate_case(case: MiniCase) -> None:
    """Raise :class:`MiniSetError` if one case is not pipeline-consistent."""

    if case.split != "mini":
        raise MiniSetError(f"{case.id}: split must be 'mini', got {case.split!r}")
    if case.label != "smoke":
        raise MiniSetError(f"{case.id}: label must be 'smoke', got {case.label!r}")
    if len(set(case.tools)) != len(case.tools):
        raise MiniSetError(f"{case.id}: duplicate names in offered tools")

    held_out = CANDIDATE_TOOLS.intersection(case.tools)
    if held_out:
        # Stricter than the dev distractor policy on purpose: the user's
        # Phase-2 mini-set instruction says no candidate held-out tool at all,
        # as gold or as a distractor.
        raise MiniSetError(
            f"{case.id}: uses frozen held-out candidate tool(s): "
            f"{', '.join(sorted(held_out))}"
        )

    unknown = [name for name in case.tools if name not in TOOLS_BY_NAME]
    if unknown:
        raise MiniSetError(f"{case.id}: unknown offered tool(s): {', '.join(unknown)}")

    try:
        gold = parse_gold(case.answers)
    except GoldFormatError as exc:
        raise MiniSetError(f"{case.id}: gold does not parse: {exc}") from exc

    if not gold:
        raise MiniSetError(f"{case.id}: gold call list is empty")

    for call in gold:
        if call.name not in case.tools:
            raise MiniSetError(
                f"{case.id}: gold call names {call.name!r}, which is not offered"
            )
        schema = TOOLS_BY_NAME[call.name]["input_schema"]
        validator = Draft202012Validator(schema)
        errors = sorted(validator.iter_errors(call.arguments), key=lambda e: list(e.path))
        if errors:
            detail = "; ".join(error.message for error in errors)
            raise MiniSetError(
                f"{case.id}: gold call to {call.name!r} violates its schema: {detail}"
            )

    scored = score_case(_gold_blocks(gold), case.answers)
    if scored.exact != 1 or scored.partial != 1.0 or scored.format_failure:
        raise MiniSetError(
            f"{case.id}: Phase-1 scorer does not accept a gold-shaped attempt "
            f"(exact={scored.exact}, partial={scored.partial})"
        )


def validate_mini_set(cases: Iterable[MiniCase]) -> None:
    seen: set[str] = set()
    for case in cases:
        if case.id in seen:
            raise MiniSetError(f"duplicate case id: {case.id}")
        seen.add(case.id)
        validate_case(case)
    if not seen:
        raise MiniSetError("mini-set is empty")


def load_and_validate(path: Path | str = MINI_SET_PATH) -> list[MiniCase]:
    cases = load_cases(path)
    validate_mini_set(cases)
    return cases


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate the Phase-2 mini-set")
    parser.add_argument("--path", default=MINI_SET_PATH)
    args = parser.parse_args()
    cases = load_and_validate(Path(args.path))
    print(
        json.dumps(
            {
                "path": str(args.path),
                "cases": len(cases),
                "label": MINI_SET_LABEL,
                "held_out_candidate_tools_used": [],
                "validated": True,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
