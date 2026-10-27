#!/usr/bin/env python3
"""Audit the interim required-arguments filter on the pinned xLAM revision.

The Phase-0 verifier treats a parameter without a literal ``default`` key as
required. The pinned revision has **zero** ``required`` keys, while the public
dataset card describes a required flag. This script measures what that
assumption removes and exposes the evidence needed for the owner's final rule.

It is offline: it reads the pinned dataset only. It makes no model call, runs
no candidate tool, and writes no raw query, gold answer, row id, or full tool
schema. It emits aggregate counts, tool/parameter names, parameter
descriptions, and yes/no flags.

Run::

    python scripts/audit_required_filter.py \\
        --revision 26d14ebfe18b1f7b524bd39b404b50af5dc97866 \\
        --out docs/REQUIRED_FILTER_AUDIT.md
"""

from __future__ import annotations

import argparse
import json
import random
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from select_candidates import (
    FROZEN_CANDIDATES,
    MIN_SINGLE_TOOL_CASES,
    PINNED_REVISION,
    normalize_parameters,
)

SAMPLE_SIZE = 50
SAMPLE_SEED = 42

EXPECTED_EXCLUDED_ROWS = 1_316
EXPECTED_MISSING_INSTANCES = 1_432
EXPECTED_REFINED_ROWS = 38
EXPECTED_CORE_INSTANCES = 43
EXPECTED_STRUCTURAL_POOL = 71
EXPECTED_INTERIM_POOL = 63
EXPECTED_REFINED_POOL = 70

CATEGORY_OPTIONAL = "optional-by-description"
CATEGORY_FILTER = "filter/pagination/sort"
CATEGORY_CORE = "core-input"
CATEGORY_ORDER = (CATEGORY_OPTIONAL, CATEGORY_FILTER, CATEGORY_CORE)

# Lexical signals only. They organise the evidence; they are not a verdict.
OPTIONAL_LANGUAGE = re.compile(
    r"\boptional\b"
    r"|\bdefaults?\b"
    r"|\bdefault\s+(?:is|to|values?)\b"
    r"|\bif\s+(?:provided|specified|given|omitted|not\s+provided|none)\b"
    r"|\bnot\s+required\b"
    r"|\bcan\s+be\b",
    re.IGNORECASE,
)
FILTER_OR_SORT_NAME = re.compile(
    r"(?:^|_)(?:filter|filters|limit|offset|page|per_page|sort|sort_by|"
    r"order|order_by|descending|ascending|skip|cursor|top)(?:$|_)",
    re.IGNORECASE,
)


def categorize_parameter(name: str, description: str) -> str:
    """Return a lexical category for a missing parameter.

    Precedence: a filter/pagination/sort name first, then description text
    that documents a default or optional behavior, then everything else. The
    category is a reproducible reading of names and descriptions, not the
    final required-arguments rule.
    """
    if FILTER_OR_SORT_NAME.search(name):
        return CATEGORY_FILTER
    if OPTIONAL_LANGUAGE.search(description):
        return CATEGORY_OPTIONAL
    return CATEGORY_CORE


def sample_indices(
    indices: list[int], *, seed: int = SAMPLE_SEED, size: int = SAMPLE_SIZE
) -> list[int]:
    """Draw the reproducible row sample from sorted excluded-row indices."""
    ordered = sorted(indices)
    if size > len(ordered):
        raise ValueError(f"sample size {size} exceeds population {len(ordered)}")
    return random.Random(seed).sample(ordered, size)


@dataclass(frozen=True)
class MissingParameter:
    """One omitted parameter in one gold call (no raw arguments stored)."""

    dataset_index: int
    tool: str
    parameter: str
    description: str
    has_other_arguments: bool
    category: str


@dataclass
class AuditReport:
    rows: int = 0
    empty_answer_rows: int = 0
    malformed_name_rows: int = 0
    unknown_name_rows: int = 0
    malformed_argument_rows: int = 0
    malformed_gold_rows: int = 0
    excluded_rows: int = 0
    refined_excluded_rows: int = 0
    missing_instances: int = 0
    rows_with_other_arguments: int = 0
    rows_with_empty_arguments: int = 0
    optional_language_instances: int = 0
    literal_default_instances: int = 0
    rows_by_instance_count: Counter[int] = field(default_factory=Counter)
    rows_by_distinct_missing: Counter[int] = field(default_factory=Counter)
    category_instances: Counter[str] = field(default_factory=Counter)
    category_rows: Counter[str] = field(default_factory=Counter)
    parameter_instances: Counter[tuple[str, str]] = field(default_factory=Counter)
    parameter_categories: dict[tuple[str, str], str] = field(default_factory=dict)
    records: dict[int, list[MissingParameter]] = field(default_factory=dict)
    single_tool_counts: Counter[str] = field(default_factory=Counter)
    interim_filtered_single_tool_counts: Counter[str] = field(
        default_factory=Counter
    )
    refined_filtered_single_tool_counts: Counter[str] = field(
        default_factory=Counter
    )
    candidate_interim_gaps: Counter[str] = field(default_factory=Counter)
    candidate_refined_gaps: Counter[str] = field(default_factory=Counter)

    @staticmethod
    def _eligible(counts: Counter[str]) -> list[str]:
        return sorted(
            tool for tool, count in counts.items()
            if count >= MIN_SINGLE_TOOL_CASES
        )

    def eligible_structural(self) -> list[str]:
        return self._eligible(self.single_tool_counts)

    def eligible_interim(self) -> list[str]:
        return self._eligible(self.interim_filtered_single_tool_counts)

    def eligible_refined(self) -> list[str]:
        return self._eligible(self.refined_filtered_single_tool_counts)

    def distinct_missing_names(self) -> set[str]:
        return {parameter for _, parameter in self.parameter_instances}


def _tool_description(tool: dict, parameter: str) -> str:
    parameters = tool.get("parameters")
    if not isinstance(parameters, dict):
        return ""
    descriptor = parameters.get(parameter)
    if not isinstance(descriptor, dict):
        return ""
    description = descriptor.get("description")
    return description if isinstance(description, str) else ""


def _has_literal_default(tool: dict, parameter: str) -> bool:
    parameters = tool.get("parameters")
    if not isinstance(parameters, dict):
        return False
    descriptor = parameters.get(parameter)
    return isinstance(descriptor, dict) and "default" in descriptor


def analyze_required_filter(dataset) -> AuditReport:
    """Apply the Phase-0 structural filters and the interim required filter."""
    report = AuditReport()
    candidates = set(FROZEN_CANDIDATES)

    for index, row in enumerate(dataset):
        report.rows += 1
        answers = json.loads(row["answers"])
        tools_list = json.loads(row["tools"])
        tools = {
            tool["name"]: tool
            for tool in tools_list
            if isinstance(tool, dict) and isinstance(tool.get("name"), str)
        }

        if not answers:
            report.empty_answer_rows += 1
            continue

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
            # Owner decision 3: a counted, pre-registered exclusion, not 0.
            report.malformed_gold_rows += 1
            continue

        names = [answer["name"] for answer in answers]
        if any(name not in tools for name in names):
            report.unknown_name_rows += 1
            continue

        missing: list[MissingParameter] = []
        any_missing = False
        any_core = False
        other_arguments = False
        empty_arguments = False

        for answer in answers:
            tool = tools[answer["name"]]
            spec = normalize_parameters(tool)
            arguments = answer["arguments"]
            missing_names = spec.required - set(arguments)
            if missing_names:
                any_missing = True
                if arguments:
                    other_arguments = True
                else:
                    empty_arguments = True
            for parameter in sorted(missing_names):
                description = _tool_description(tool, parameter)
                category = categorize_parameter(parameter, description)
                if category == CATEGORY_CORE:
                    any_core = True
                missing.append(
                    MissingParameter(
                        dataset_index=index,
                        tool=answer["name"],
                        parameter=parameter,
                        description=description,
                        has_other_arguments=bool(arguments),
                        category=category,
                    )
                )

        distinct_names = set(names)
        if len(distinct_names) == 1:
            tool_name = names[0]
            report.single_tool_counts[tool_name] += 1
            if not any_missing:
                report.interim_filtered_single_tool_counts[tool_name] += 1
            if not any_core:
                report.refined_filtered_single_tool_counts[tool_name] += 1
            if tool_name in candidates and any_missing:
                report.candidate_interim_gaps[tool_name] += 1
            if tool_name in candidates and any_core:
                report.candidate_refined_gaps[tool_name] += 1

        if not any_missing:
            continue

        report.excluded_rows += 1
        if any_core:
            report.refined_excluded_rows += 1
        report.missing_instances += len(missing)
        report.rows_by_instance_count[len(missing)] += 1
        report.rows_by_distinct_missing[len({m.parameter for m in missing})] += 1
        if other_arguments:
            report.rows_with_other_arguments += 1
        if empty_arguments:
            report.rows_with_empty_arguments += 1
        report.records[index] = missing

        touched_categories: set[str] = set()
        for item in missing:
            report.category_instances[item.category] += 1
            touched_categories.add(item.category)
            key = (item.tool, item.parameter)
            report.parameter_instances[key] += 1
            report.parameter_categories[key] = item.category
            if OPTIONAL_LANGUAGE.search(item.description):
                report.optional_language_instances += 1
            if _has_literal_default(tools[item.tool], item.parameter):
                report.literal_default_instances += 1
        for category in touched_categories:
            report.category_rows[category] += 1

    return report


def _fmt(number: int) -> str:
    return f"{number:,}"


def _md(text: str) -> str:
    cleaned = text.strip().replace("|", "\\|")
    return cleaned.replace("\n", " ") if cleaned else "_(no description)_"


def _share(part: int, total: int) -> str:
    if total == 0:
        return "—"
    return f"{part / total:.1%}"


def render_markdown(
    report: AuditReport, *, sample_size: int = SAMPLE_SIZE, seed: int = SAMPLE_SEED
) -> str:
    """Render the committed audit report. No raw query or gold answer."""
    lines: list[str] = []
    add = lines.append
    sample_total = min(sample_size, len(report.records))

    add("# Required-Arguments Filter Audit — Pinned xLAM Revision")
    add("")
    add("**Status: evidence for owner sign-off. This report does not choose the")
    add("final required-arguments rule.** It reproduces what the interim")
    add('"no `default` key = required" heuristic removes and surfaces the')
    add("description evidence needed to replace or confirm it before")
    add("pre-registration.")
    add("")
    add(f"- Pinned revision: `{PINNED_REVISION}`.")
    add("- Dataset: `Salesforce/xlam-function-calling-60k`, split `train`.")
    add("- Offline: no model calls, no candidate-tool runs.")
    add(f"- Sample seed: **{seed}**; sample size: **{sample_total} excluded rows**.")
    add("- Contents: aggregate counts, tool names, parameter names, parameter")
    add("  descriptions, and yes/no flags only. No raw query, gold answer,")
    add("  row id, or full tool schema.")
    add("")

    add("## Reproduction")
    add("")
    add("```bash")
    add("python scripts/audit_required_filter.py \\")
    add(f"  --revision {PINNED_REVISION} \\")
    add("  --out docs/REQUIRED_FILTER_AUDIT.md")
    add("```")
    add("")
    add("The script loads the pinned revision, applies the Phase-0 structural")
    add("filters, reproduces the interim required-arguments exclusion, and")
    add("asserts the pinned counts below.")
    add("")

    add("## Totals")
    add("")
    add("| Metric | Count |")
    add("|---|---:|")
    add(f"| Rows scanned | {_fmt(report.rows)} |")
    add(f"| Empty `answers` rows | {_fmt(report.empty_answer_rows)} |")
    add(f"| Malformed gold tool names | {_fmt(report.malformed_name_rows)} |")
    add(
        "| Gold names absent from the row's `tools` "
        f"| {_fmt(report.unknown_name_rows)} |"
    )
    add(
        "| Malformed gold arguments (non-object/string-encoded) "
        f"| {_fmt(report.malformed_argument_rows)} |"
    )
    add(
        "| **Malformed gold rows (final exclusion, decision 3)** "
        f"| **{_fmt(report.malformed_gold_rows)}** |"
    )
    add(
        "| **Rows removed by the interim required-arguments filter** "
        f"| **{_fmt(report.excluded_rows)}** |"
    )
    add(f"| Missing-parameter instances | {_fmt(report.missing_instances)} |")
    add(
        "| Rows with exactly one missing-parameter instance "
        f"| {_fmt(report.rows_by_instance_count.get(1, 0))} |"
    )
    multi_instance = sum(
        count for number, count in report.rows_by_instance_count.items()
        if number > 1
    )
    add(
        "| Rows with more than one missing-parameter instance "
        f"| {_fmt(multi_instance)} |"
    )
    multi_distinct = sum(
        count for number, count in report.rows_by_distinct_missing.items()
        if number > 1
    )
    add(
        "| Rows with more than one distinct missing parameter name "
        f"| {_fmt(multi_distinct)} |"
    )
    add(
        "| Rows where at least one missing call carries other gold arguments "
        f"| {_fmt(report.rows_with_other_arguments)} |"
    )
    add(
        "| Rows where every missing call has empty arguments "
        f"| {_fmt(report.rows_with_empty_arguments)} |"
    )
    add(
        "| Distinct missing parameter names "
        f"| {_fmt(len(report.distinct_missing_names()))} |"
    )
    add(
        "| Single-tool candidate rows removed "
        f"| {_fmt(sum(report.candidate_interim_gaps.values()))} |"
    )
    add("")

    add("## Category breakdown")
    add("")
    add(
        "Categories are lexical and mutually exclusive, with fixed precedence: "
        "(1) filter/pagination/sort parameter name, (2) description documents "
        "a default/optional fallback, (3) core input. Rows can touch more than "
        "one category, so the row column does not sum to the total."
    )
    add("")
    add("| Category | Missing-parameter instances | Share | Excluded rows touched |")
    add("|---|---:|---:|---:|")
    for category in CATEGORY_ORDER:
        instances = report.category_instances[category]
        add(
            f"| {category} | {_fmt(instances)} "
            f"| {_share(instances, report.missing_instances)} "
            f"| {_fmt(report.category_rows[category])} |"
        )
    add(
        f"| **Total** | **{_fmt(report.missing_instances)}** | **100.0%** "
        f"| **{_fmt(report.excluded_rows)}** |"
    )
    add("")
    add(
        "**Description signal (overlapping with the table above).** "
        f"{_fmt(report.optional_language_instances)} of "
        f"{_fmt(report.missing_instances)} missing-parameter instances "
        f"({_share(report.optional_language_instances, report.missing_instances)}) "
        "have a description that documents a default or optional fallback; "
        f"{_fmt(report.missing_instances - report.optional_language_instances)} "
        "have no such language. Literal `default` keys among the missing "
        f"parameters: {_fmt(report.literal_default_instances)} (zero by "
        "construction). These are the heuristic's contested cases: the "
        "descriptor has no `default` key, but its own text says a fallback "
        "exists."
    )
    add("")

    add("## Missing parameters by tool")
    add("")
    add("| Tool | Missing parameter | Instances | Category |")
    add("|---|---|---:|---|")
    for (tool_name, parameter), count in report.parameter_instances.most_common():
        category = report.parameter_categories[(tool_name, parameter)]
        add(f"| `{tool_name}` | `{parameter}` | {_fmt(count)} | {category} |")
    add("")

    add(f"## Reproducible sample of {sample_total} excluded rows (seed {seed})")
    add("")
    add(
        f"Sampling: `random.Random({seed}).sample(sorted(excluded_row_indices), "
        f"{sample_total})`; the sample order is the `random.sample` order. "
        "Dataset rows are 0-based indices. A sampled row can contribute more "
        "than one line when multiple gold calls omit parameters."
    )
    add("")
    add(
        "| # | Dataset row | Tool | Missing parameter | Category "
        "| Other gold args in that call? | Parameter description |"
    )
    add("|---:|---:|---|---|---|---|---|")
    if report.records:
        indices = sorted(report.records)
        chosen = sample_indices(indices, seed=seed, size=sample_total)
        for position, index in enumerate(chosen, start=1):
            for item in report.records[index]:
                other = "yes" if item.has_other_arguments else "no"
                add(
                    f"| {position} | {index} | `{item.tool}` "
                    f"| `{item.parameter}` | {item.category} | {other} "
                    f"| {_md(item.description)} |"
                )
    else:
        add("| — | — | — | — | — | — | No excluded rows |")
    add("")

    core_records = sorted(
        (
            item
            for items in report.records.values()
            for item in items
            if item.category == CATEGORY_CORE
        ),
        key=lambda item: (item.dataset_index, item.tool, item.parameter),
    )
    if core_records:
        add("## Supplementary: every core-input exclusion")
        add("")
        add(
            "These are the only missing parameters without a documented "
            "default/optional fallback or a filter/sort name. They are listed "
            "in full, not sampled, because they drive the description-aware "
            "option below."
        )
        add("")
        add(
            "| Dataset row | Tool | Missing parameter "
            "| Other gold args in that call? | Parameter description |"
        )
        add("|---:|---|---|---|---|")
        for item in core_records:
            other = "yes" if item.has_other_arguments else "no"
            add(
                f"| {item.dataset_index} | `{item.tool}` "
                f"| `{item.parameter}` | {other} | {_md(item.description)} |"
            )
        add("")

    structural_pool = len(report.eligible_structural())
    interim_pool = len(report.eligible_interim())
    refined_pool = len(report.eligible_refined())
    dropped = sorted(
        set(report.eligible_structural()) - set(report.eligible_refined())
    )
    dropped_text = ", ".join(f"`{tool}`" for tool in dropped) or "none"

    add("## What the audit supports (owner decision pending)")
    add("")
    add("Three options, with their measured consequences on this revision:")
    add("")
    add("| Option | Rows excluded | Eligible tools | Candidate rows removed |")
    add("|---|---:|---:|---:|")
    add(
        f"| Keep the interim heuristic | {_fmt(report.excluded_rows)} "
        f"| {interim_pool} "
        "| 0 |"
    )
    add(
        f"| Description-aware refinement | {_fmt(report.refined_excluded_rows)} "
        f"| {refined_pool} | 0 |"
    )
    add(f"| Drop the filter | 0 | {structural_pool} | 0 |")
    add("")
    add(
        "The description-aware refinement treats a parameter as optional when "
        "its description documents a default/optional fallback, or its name "
        "is a filter/pagination/sort parameter, even if the descriptor lacks "
        f"a `default` key; only core inputs remain required. Tools that lose "
        f"the 100-case floor under it: {dropped_text}."
    )
    add("")
    add(
        f"**Supported rule, if the owner accepts description text as the "
        f"signal:** the description-aware refinement. The audit finds that "
        f"{_share(report.optional_language_instances, report.missing_instances)} "
        "of missing-parameter instances are parameters whose own description "
        "documents a default/optional fallback, while only "
        f"{_fmt(report.category_instances[CATEGORY_CORE])} instances "
        f"({_fmt(report.refined_excluded_rows)} rows) are core inputs with no "
        "such language. This is a recommendation from the counts, **not a "
        "decision**: the owner signs the final rule before pre-registration."
    )
    add("")
    add(
        "**If the filter is dropped**, the eligible pool returns to the "
        f"structural {structural_pool} tools. The {interim_pool}-tool figure "
        "is an artifact of the interim heuristic, not a property of the "
        "dataset. **No option removes a candidate single-tool row**: all 12 "
        "candidate counts, the frozen order, and its sha256 are unchanged, so "
        "no candidate replacement is triggered."
    )
    add("")
    add(
        "The public card describes a `required` boolean; the pinned revision "
        "has zero `required` keys in its 362,482 parameter descriptors. That "
        "card-vs-data discrepancy remains open and must be recorded in "
        "pre-registration whichever rule is chosen."
    )
    add("")

    return "\n".join(lines) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--revision",
        default=PINNED_REVISION,
        help=f"pinned xLAM revision (default: {PINNED_REVISION})",
    )
    parser.add_argument("--split", default="train", help="dataset split")
    parser.add_argument("--sample-size", type=int, default=SAMPLE_SIZE)
    parser.add_argument("--seed", type=int, default=SAMPLE_SEED)
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="write the Markdown report here (default: stdout)",
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
    report = analyze_required_filter(dataset)
    markdown = render_markdown(
        report, sample_size=args.sample_size, seed=args.seed
    )

    failures = []
    if args.revision != PINNED_REVISION:
        failures.append(f"revision {args.revision} != pinned {PINNED_REVISION}")
    if report.excluded_rows != EXPECTED_EXCLUDED_ROWS:
        failures.append(
            f"excluded rows {report.excluded_rows} != {EXPECTED_EXCLUDED_ROWS}"
        )
    if report.missing_instances != EXPECTED_MISSING_INSTANCES:
        failures.append(
            "missing-parameter instances "
            f"{report.missing_instances} != {EXPECTED_MISSING_INSTANCES}"
        )
    if report.malformed_gold_rows != 0:
        failures.append(
            f"malformed gold rows {report.malformed_gold_rows} != 0"
        )
    if report.refined_excluded_rows != EXPECTED_REFINED_ROWS:
        failures.append(
            "core-input excluded rows "
            f"{report.refined_excluded_rows} != {EXPECTED_REFINED_ROWS}"
        )
    if report.category_instances[CATEGORY_CORE] != EXPECTED_CORE_INSTANCES:
        failures.append(
            "core-input instances "
            f"{report.category_instances[CATEGORY_CORE]} "
            f"!= {EXPECTED_CORE_INSTANCES}"
        )
    if len(report.eligible_structural()) != EXPECTED_STRUCTURAL_POOL:
        failures.append(
            "structural pool "
            f"{len(report.eligible_structural())} != {EXPECTED_STRUCTURAL_POOL}"
        )
    if len(report.eligible_interim()) != EXPECTED_INTERIM_POOL:
        failures.append(
            "interim pool "
            f"{len(report.eligible_interim())} != {EXPECTED_INTERIM_POOL}"
        )
    if len(report.eligible_refined()) != EXPECTED_REFINED_POOL:
        failures.append(
            "refined pool "
            f"{len(report.eligible_refined())} != {EXPECTED_REFINED_POOL}"
        )
    if report.candidate_interim_gaps:
        failures.append(
            "interim filter removes candidate rows: "
            f"{dict(report.candidate_interim_gaps)}"
        )
    if report.candidate_refined_gaps:
        failures.append(
            "description-aware filter removes candidate rows: "
            f"{dict(report.candidate_refined_gaps)}"
        )

    if failures:
        print("AUDIT: FAIL")
        for failure in failures:
            print(f"  - {failure}")
        return 1

    if args.out is not None:
        args.out.write_text(markdown, encoding="utf-8")
        print(f"wrote {args.out}")
    else:
        print(markdown)
    print("AUDIT: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
