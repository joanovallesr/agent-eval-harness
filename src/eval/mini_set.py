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
    # xLAM cases carry their own Anthropic tool definitions. The hand-written
    # mini-set leaves this empty and resolves names against ``agent.tools``.
    tool_definitions: tuple[dict[str, Any], ...] = ()
    tool_name: str = ""

    def to_task(self) -> dict[str, Any]:
        """Return the model-facing task.

        Gold ``answers`` are deliberately omitted: the agent loop must never
        see them, and nothing in the loop needs them for repair decisions.
        """

        task: dict[str, Any] = {
            "id": self.id,
            "query": self.query,
            "tools": list(self.tools),
            "split": self.split,
            "label": self.label,
            "notes": self.notes,
        }
        if self.tool_definitions:
            task["tool_definitions"] = [
                dict(definition) for definition in self.tool_definitions
            ]
        return task


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
        if not isinstance(tools, list) or not tools:
            raise MiniSetError(f"{task_id}: 'tools' must be a non-empty list")

        tool_definitions: tuple[dict[str, Any], ...] = ()
        if all(isinstance(name, str) and name for name in tools):
            names = tuple(tools)
        elif all(isinstance(tool, dict) for tool in tools):
            tool_definitions = tuple(dict(tool) for tool in tools)
            raw_names = [tool.get("name") for tool in tool_definitions]
            if not all(isinstance(name, str) and name for name in raw_names):
                raise MiniSetError(
                    f"{task_id}: every tool definition needs a non-empty name"
                )
            names = tuple(str(name) for name in raw_names)
        else:
            raise MiniSetError(
                f"{task_id}: 'tools' must be tool names or tool definition objects"
            )

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
                tools=names,
                answers=answers_text,
                split=str(row.get("split", "mini")),
                label=str(row.get("label", "smoke")),
                notes=str(row.get("notes", "")),
                tool_definitions=tool_definitions,
                tool_name=str(row.get("tool_name", "")),
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


def _schema_for(case: MiniCase, name: str) -> dict[str, Any]:
    if case.tool_definitions:
        for tool in case.tool_definitions:
            if tool.get("name") == name:
                schema = tool.get("input_schema")
                if not isinstance(schema, dict):
                    raise MiniSetError(
                        f"{case.id}: tool {name!r} has no input_schema object"
                    )
                return schema
    return TOOLS_BY_NAME[name]["input_schema"]


def validate_case(case: MiniCase) -> None:
    """Raise :class:`MiniSetError` if one case is not pipeline-consistent."""

    if not case.tool_definitions:
        if case.split != "mini":
            raise MiniSetError(f"{case.id}: split must be 'mini', got {case.split!r}")
        if case.label != "smoke":
            raise MiniSetError(f"{case.id}: label must be 'smoke', got {case.label!r}")
    if len(set(case.tools)) != len(case.tools):
        raise MiniSetError(f"{case.id}: duplicate names in offered tools")

    held_out = CANDIDATE_TOOLS.intersection(case.tools)
    if held_out and not case.tool_definitions:
        # The Phase-2 mini-set is stricter than the dev distractor policy: it
        # forbids candidate tools entirely. Phase-3 xLAM cases allow a
        # candidate only as a distractor; a candidate gold answer is always a
        # leak.
        raise MiniSetError(
            f"{case.id}: uses frozen held-out candidate tool(s): "
            f"{', '.join(sorted(held_out))}"
        )

    if not case.tool_definitions:
        unknown = [name for name in case.tools if name not in TOOLS_BY_NAME]
        if unknown:
            raise MiniSetError(
                f"{case.id}: unknown offered tool(s): {', '.join(unknown)}"
            )

    try:
        gold = parse_gold(case.answers)
    except GoldFormatError as exc:
        raise MiniSetError(f"{case.id}: gold does not parse: {exc}") from exc

    if not gold:
        raise MiniSetError(f"{case.id}: gold call list is empty")

    gold_names = {call.name for call in gold}
    leaked = CANDIDATE_TOOLS.intersection(gold_names)
    if leaked:
        raise MiniSetError(
            f"{case.id}: gold answers name frozen held-out candidate tool(s): "
            f"{', '.join(sorted(leaked))}"
        )

    for call in gold:
        if call.name not in case.tools:
            raise MiniSetError(
                f"{case.id}: gold call names {call.name!r}, which is not offered"
            )
        schema = _schema_for(case, call.name)
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
