#!/usr/bin/env python3
"""Reproduce and verify the Phase 0 held-out-tool candidate freeze.

The frozen candidate order is::

    random.Random(42).sample(sorted(eligible_tools), 12)

where ``eligible_tools`` are tools with at least 100 single-tool cases after
the structural filters below. This script re-applies those filters to the
pinned xLAM revision, regenerates the order, and asserts that it equals the
frozen list and sha256. It prints aggregates and tool names only; it never
prints or writes a raw dataset row, query, answer, or full tool schema.

Run::

    python scripts/select_candidates.py \
        --revision 26d14ebfe18b1f7b524bd39b404b50af5dc97866
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
from collections import Counter
from dataclasses import dataclass, field
from typing import Literal

PINNED_REVISION = "26d14ebfe18b1f7b524bd39b404b50af5dc97866"
SEED = 42
MIN_SINGLE_TOOL_CASES = 100

FROZEN_CANDIDATES = [
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
]
FROZEN_SHA256 = (
    "6b822299090bda4e6dfca52b6db426b7b2b4b5dba14662505962145415623991"
)


@dataclass(frozen=True)
class ParameterSpec:
    """Normalized tool parameters.

    xLAM revisions use a flat map ``param_name -> {type, description,
    default?}``. A nested JSON-Schema form (``type: object`` + ``properties``
    + ``required``) is also handled defensively; the pinned revision contains
    zero nested declarations. A parameter without a ``default`` is treated as
    required; a parameter with a ``default`` is optional.
    """

    form: Literal["flat", "nested", "unknown"]
    all_names: frozenset[str]
    required: frozenset[str]


def normalize_parameters(tool: dict) -> ParameterSpec:
    parameters = tool.get("parameters")
    if not isinstance(parameters, dict):
        return ParameterSpec("unknown", frozenset(), frozenset())

    # Nested JSON Schema (not present in the pinned revision).
    if parameters.get("type") == "object" and isinstance(
        parameters.get("properties"), dict
    ):
        properties = parameters["properties"]
        required = parameters.get("required")
        return ParameterSpec(
            "nested",
            frozenset(properties),
            frozenset(required) if isinstance(required, list) else frozenset(),
        )

    # Flat xLAM form: each value is a parameter descriptor.
    if all(
        isinstance(descriptor, dict) and "type" in descriptor
        for descriptor in parameters.values()
    ):
        required = {
            name
            for name, descriptor in parameters.items()
            if "default" not in descriptor
        }
        return ParameterSpec("flat", frozenset(parameters), frozenset(required))

    return ParameterSpec("unknown", frozenset(parameters), frozenset())


@dataclass
class Report:
    rows: int = 0
    distinct_tools: set[str] = field(default_factory=set)
    parameter_forms: Counter = field(default_factory=Counter)
    parameter_descriptors: int = 0
    parameter_descriptor_keys: Counter = field(default_factory=Counter)
    required_key_descriptors: int = 0
    empty_answer_rows: int = 0
    malformed_name_rows: int = 0
    unknown_name_rows: int = 0
    malformed_argument_rows: int = 0
    malformed_gold_rows: int = 0
    single_tool_rows: int = 0
    multi_tool_rows: int = 0
    mixed_candidate_non_candidate: int = 0
    multi_tool_all_candidates: int = 0
    multi_tool_no_candidates: int = 0
    required_argument_rows: int = 0
    empty_arguments_where_required_rows: int = 0
    # Structural pool: single-tool counts before the required-argument filter.
    # The frozen order is generated from this pool, because the candidate list
    # was locked before the required-argument filter was finalized.
    single_tool_counts: Counter = field(default_factory=Counter)
    # Usable pool: single-tool counts after every filter, including required
    # arguments. Candidate counts must be identical in both pools.
    required_filtered_single_tool_counts: Counter = field(default_factory=Counter)
    candidate_single_tool_required_gaps: Counter = field(default_factory=Counter)
    distractor_all: Counter = field(default_factory=Counter)
    distractor_dev: Counter = field(default_factory=Counter)

    def eligible_tools(self) -> list[str]:
        """Structural pool used to generate the frozen candidate order."""
        return sorted(
            tool
            for tool, count in self.single_tool_counts.items()
            if count >= MIN_SINGLE_TOOL_CASES
        )

    def eligible_tools_after_all_filters(self) -> list[str]:
        """Usable pool after every filter, including required arguments."""
        return sorted(
            tool
            for tool, count in self.required_filtered_single_tool_counts.items()
            if count >= MIN_SINGLE_TOOL_CASES
        )


def analyze(dataset) -> Report:
    candidates = set(FROZEN_CANDIDATES)
    report = Report()

    for row in dataset:
        report.rows += 1
        answers = json.loads(row["answers"])
        tools_list = json.loads(row["tools"])
        tools = {tool["name"]: tool for tool in tools_list}
        report.distinct_tools.update(tools)

        for tool in tools_list:
            spec = normalize_parameters(tool)
            report.parameter_forms[spec.form] += 1
            parameters = tool.get("parameters")
            if not isinstance(parameters, dict):
                continue
            if spec.form == "nested":
                descriptors = parameters.get("properties", {}).values()
            elif spec.form == "flat":
                descriptors = parameters.values()
            else:
                descriptors = ()
            for descriptor in descriptors:
                if not isinstance(descriptor, dict):
                    continue
                report.parameter_descriptors += 1
                report.parameter_descriptor_keys.update(descriptor.keys())
                if "required" in descriptor:
                    report.required_key_descriptors += 1

        if not answers:
            report.empty_answer_rows += 1
            continue

        # Owner decision 3 (final): malformed gold calls are excluded and
        # counted before the split, never scored as 0. "Missing" applies to
        # the tool name; arguments must be a JSON object (not a string).
        malformed_name = any(
            not isinstance(answer, dict)
            or not isinstance(answer.get("name"), str)
            or answer.get("name") == ""
            for answer in answers
        )
        malformed_arguments = any(
            not isinstance(answer, dict)
            or not isinstance(answer.get("arguments"), dict)
            for answer in answers
        )
        if malformed_name:
            report.malformed_name_rows += 1
        if malformed_arguments:
            report.malformed_argument_rows += 1
        if malformed_name or malformed_arguments:
            report.malformed_gold_rows += 1
            continue

        names = [answer["name"] for answer in answers]
        if any(name not in tools for name in names):
            report.unknown_name_rows += 1
            continue

        any_required_gap = False
        any_empty_where_required = False
        for answer in answers:
            spec = normalize_parameters(tools[answer["name"]])
            arguments = answer.get("arguments") or {}
            if spec.required - set(arguments):
                any_required_gap = True
            if not arguments and spec.required:
                any_empty_where_required = True
        if any_required_gap:
            report.required_argument_rows += 1
        if any_empty_where_required:
            report.empty_arguments_where_required_rows += 1

        distinct = set(names)
        if len(distinct) == 1:
            tool = names[0]
            report.single_tool_rows += 1
            report.single_tool_counts[tool] += 1
            if not any_required_gap:
                report.required_filtered_single_tool_counts[tool] += 1
            if tool in candidates and any_required_gap:
                report.candidate_single_tool_required_gaps[tool] += 1
        else:
            report.multi_tool_rows += 1
            if distinct & candidates and distinct - candidates:
                report.mixed_candidate_non_candidate += 1
            elif distinct <= candidates:
                report.multi_tool_all_candidates += 1
            else:
                report.multi_tool_no_candidates += 1

        # Distractor exposure: a candidate appears in tools but not as an
        # answer. "dev" here means the row's answer tools are all non-candidate
        # (i.e. the row is dev-eligible). This is a disclosure, not a leak.
        answer_tools = set(names)
        for candidate in candidates:
            if candidate in tools and candidate not in answer_tools:
                report.distractor_all[candidate] += 1
                if not (distinct & candidates):
                    report.distractor_dev[candidate] += 1

    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--revision",
        default=PINNED_REVISION,
        help=f"pinned xLAM revision (default: {PINNED_REVISION})",
    )
    parser.add_argument(
        "--split",
        default="train",
        help="dataset split (default: train)",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()

    from datasets import load_dataset

    dataset = load_dataset(
        "Salesforce/xlam-function-calling-60k",
        revision=args.revision,
        split=args.split,
    )
    report = analyze(dataset)
    eligible_structural = report.eligible_tools()
    eligible_after_all = report.eligible_tools_after_all_filters()
    regenerated = random.Random(SEED).sample(
        eligible_structural, len(FROZEN_CANDIDATES)
    )
    digest = hashlib.sha256("\n".join(regenerated).encode()).hexdigest()

    candidate_counts_structural = {
        tool: report.single_tool_counts[tool] for tool in FROZEN_CANDIDATES
    }
    # Per-candidate counts are recomputed after every filter, including
    # required arguments. They must equal the structural counts; otherwise
    # the candidate would have to be flagged and covered by a spare.
    candidate_counts = {
        tool: report.required_filtered_single_tool_counts[tool]
        for tool in FROZEN_CANDIDATES
    }
    candidate_counts_unchanged = (
        candidate_counts_structural == candidate_counts
    )
    below_floor = [
        tool for tool, count in candidate_counts.items()
        if count < MIN_SINGLE_TOOL_CASES
    ]

    print(f"revision: {args.revision}")
    print(f"rows: {report.rows}")
    print(f"distinct tools: {len(report.distinct_tools)}")
    print(f"parameter forms: {dict(report.parameter_forms)}")
    print(f"parameter descriptors: {report.parameter_descriptors}")
    print(f"parameter descriptor keys: {dict(report.parameter_descriptor_keys)}")
    print(f"descriptors with a required key: {report.required_key_descriptors}")
    print(f"empty answer rows: {report.empty_answer_rows}")
    print(f"malformed gold rows: {report.malformed_gold_rows}")
    print(f"  malformed tool names: {report.malformed_name_rows}")
    print(f"  malformed arguments: {report.malformed_argument_rows}")
    print(f"  names absent from tools: {report.unknown_name_rows}")
    print(f"single-tool rows: {report.single_tool_rows}")
    print(f"multi-tool rows: {report.multi_tool_rows}")
    print(
        "mixed candidate+non-candidate rows: "
        f"{report.mixed_candidate_non_candidate}"
    )
    print(
        "multi-tool all-candidate rows: "
        f"{report.multi_tool_all_candidates}"
    )
    print(f"multi-tool no-candidate rows: {report.multi_tool_no_candidates}")
    print(f"required-argument filter rows: {report.required_argument_rows}")
    print(
        "empty-arguments-where-required rows: "
        f"{report.empty_arguments_where_required_rows}"
    )
    print(
        "eligible tools, structural pool "
        f"(>= {MIN_SINGLE_TOOL_CASES} single-tool cases): "
        f"{len(eligible_structural)}"
    )
    print(
        "eligible tools after all filters, including required arguments: "
        f"{len(eligible_after_all)}"
    )
    print(f"seed: {SEED}")
    print(f"regenerated candidates: {regenerated}")
    print(f"regenerated sha256: {digest}")
    print(f"candidate single-tool counts (structural): {candidate_counts_structural}")
    print(f"candidate single-tool counts (after all filters): {candidate_counts}")
    print(f"candidate counts unchanged by required-argument filter: {candidate_counts_unchanged}")
    print(
        "candidate single-tool rows removed by required-argument filter: "
        f"{dict(report.candidate_single_tool_required_gaps)}"
    )
    print(f"distractor exposure (all rows): {dict(report.distractor_all)}")
    print(f"distractor exposure (dev-eligible rows): {dict(report.distractor_dev)}")

    failures = []
    if args.revision != PINNED_REVISION:
        failures.append(
            f"revision {args.revision} != pinned {PINNED_REVISION}"
        )
    if len(eligible_structural) != 71:
        failures.append(
            f"structural eligible pool {len(eligible_structural)} != 71"
        )
    if report.parameter_forms.get("flat") != 168_870:
        failures.append(
            "flat tool declarations "
            f"{report.parameter_forms.get('flat')} != 168870"
        )
    if report.parameter_forms.get("nested", 0) != 0:
        failures.append(
            f"nested tool declarations {report.parameter_forms.get('nested')} != 0"
        )
    if report.malformed_gold_rows != 0:
        failures.append(
            f"malformed gold rows {report.malformed_gold_rows} != 0"
        )
    if report.required_key_descriptors != 0:
        failures.append(
            "parameter descriptors carry a required key: "
            f"{report.required_key_descriptors}"
        )
    if regenerated != FROZEN_CANDIDATES:
        failures.append("regenerated order != frozen candidate order")
    if digest != FROZEN_SHA256:
        failures.append("regenerated sha256 != frozen sha256")
    if not candidate_counts_unchanged:
        failures.append(
            "required-argument filter changes candidate counts: "
            f"structural={candidate_counts_structural} "
            f"after_all={candidate_counts}"
        )
    if below_floor:
        failures.append(f"candidates below the 100-case floor: {below_floor}")
    if report.candidate_single_tool_required_gaps:
        failures.append(
            "the required-argument filter removes candidate single-tool "
            f"rows: {dict(report.candidate_single_tool_required_gaps)}"
        )

    if failures:
        print("VERIFY: FAIL")
        for failure in failures:
            print(f"  - {failure}")
        return 1

    print("VERIFY: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
