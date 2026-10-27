#!/usr/bin/env python3
"""Audit the FINAL description-aware required-arguments rule on the pinned xLAM revision.

The Phase-0 verifier used to treat a parameter without a literal ``default``
key as required. The pinned revision has **zero** ``required`` keys, while the
public dataset card describes a required flag. This script measures what that
interim assumption removes and reproduces the FINAL deterministic rule chosen
by the owner: only a missing core input excludes a row.

It is offline: it reads the pinned dataset only. It makes no model call, runs
no candidate tool, and writes no raw query, gold answer, or full tool schema.
It emits aggregate counts, tool/parameter names, parameter descriptions, and
yes/no flags.

Run::

    python scripts/audit_required_filter.py \\
        --revision 26d14ebfe18b1f7b524bd39b404b50af5dc97866 \\
        --out docs/REQUIRED_FILTER_AUDIT.md
"""

from __future__ import annotations

import argparse
import json
import random
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from select_candidates import (
    CATEGORY_CORE,
    CATEGORY_FILTER,
    CATEGORY_OPTIONAL,
    CATEGORY_ORDER,
    FROZEN_CANDIDATES,
    MIN_SINGLE_TOOL_CASES,
    OPTIONAL_LANGUAGE,
    PINNED_REVISION,
    categorize_parameter,
    normalize_parameters,
)

SAMPLE_SIZE = 50
SAMPLE_SEED = 42

# Re-exported for the audit tests; the canonical definitions and the final
# rule live in select_candidates.py.
__all__ = [
    "CATEGORY_CORE",
    "CATEGORY_FILTER",
    "CATEGORY_OPTIONAL",
    "CATEGORY_ORDER",
    "categorize_parameter",
]

EXPECTED_EXCLUDED_ROWS = 1_316
EXPECTED_MISSING_INSTANCES = 1_432
EXPECTED_REFINED_ROWS = 38
EXPECTED_CORE_INSTANCES = 43
EXPECTED_STRUCTURAL_POOL = 71
EXPECTED_INTERIM_POOL = 63
EXPECTED_REFINED_POOL = 70


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


def _descriptor(tool: dict, parameter: str) -> dict | None:
    parameters = tool.get("parameters")
    if not isinstance(parameters, dict):
        return None
    descriptor = parameters.get(parameter)
    return descriptor if isinstance(descriptor, dict) else None


def _tool_description(tool: dict, parameter: str) -> str:
    descriptor = _descriptor(tool, parameter)
    if descriptor is None:
        return ""
    description = descriptor.get("description")
    return description if isinstance(description, str) else ""


def _has_literal_default(tool: dict, parameter: str) -> bool:
    descriptor = _descriptor(tool, parameter)
    return descriptor is not None and "default" in descriptor


def analyze_required_filter(dataset) -> AuditReport:
    """Apply the structural filters and measure both required-argument rules.

    ``no_default`` is the evidence basis: every omitted parameter without a
    literal ``default`` key. The final rule then excludes only rows where at
    least one such omission is a core input.
    """
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
            missing_names = spec.no_default - set(arguments)
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
    add("**Status: FINAL (owner decision 1, 2026-10-26).** The required-arguments")
    add("rule is the deterministic description-aware rule implemented in")
    add("`scripts/select_candidates.py` and reproduced here. The report keeps")
    add('the interim "no `default` key = required" heuristic as historical')
    add("evidence only; it is **not** the current rule.")
    add("")
    add(f"- Pinned revision: `{PINNED_REVISION}`.")
    add("- Dataset: `Salesforce/xlam-function-calling-60k`, split `train`.")
    add("- Offline: no model calls, no candidate-tool runs.")
    add(f"- Sample seed: **{seed}**; sample size: **{sample_total} excluded rows**.")
    add("- Contents: aggregate counts, tool names, parameter names, parameter")
    add("  descriptions, and yes/no flags only. No raw query, gold answer, or")
    add("  full tool schema.")
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
    add("filters, reproduces both the historical interim exclusion and the")
    add("FINAL description-aware exclusion, and asserts the pinned counts below.")
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
        "| **Rows removed by the interim heuristic (historical, not the rule)** "
        f"| **{_fmt(report.excluded_rows)}** |"
    )
    add(
        "| **Rows removed by the FINAL rule (core-input only)** "
        f"| **{_fmt(report.refined_excluded_rows)}** |"
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
        "| Rows with at least one missing call that has empty arguments "
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
        "construction). These were the interim heuristic's contested cases: "
        "the descriptor has no `default` key, but its own text says a fallback "
        "exists. The FINAL rule classifies them as optional-by-description and "
        "keeps their rows."
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
        f"{sample_total})`; the sample order is the `random.sample` order. A "
        "sampled row can contribute more than one line when multiple gold "
        "calls omit parameters. The table reports sample positions only."
    )
    add("")
    add(
        "| # | Tool | Missing parameter | Category "
        "| Other gold args in that call? | Parameter description |"
    )
    add("|---:|---|---|---|---|---|")
    if report.records:
        indices = sorted(report.records)
        chosen = sample_indices(indices, seed=seed, size=sample_total)
        for position, index in enumerate(chosen, start=1):
            for item in report.records[index]:
                other = "yes" if item.has_other_arguments else "no"
                add(
                    f"| {position} | `{item.tool}` "
                    f"| `{item.parameter}` | {item.category} | {other} "
                    f"| {_md(item.description)} |"
                )
    else:
        add("| — | — | — | — | — | No excluded rows |")
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
            "| # | Tool | Missing parameter "
            "| Other gold args in that call? | Parameter description |"
        )
        add("|---:|---|---|---|---|")
        for position, item in enumerate(core_records, start=1):
            other = "yes" if item.has_other_arguments else "no"
            add(
                f"| {position} | `{item.tool}` "
                f"| `{item.parameter}` | {other} | {_md(item.description)} |"
            )
        add("")

    structural_pool = len(report.eligible_structural())
    interim_pool = len(report.eligible_interim())
    refined_pool = len(report.eligible_refined())
    interim_gaps = sum(report.candidate_interim_gaps.values())
    refined_gaps = sum(report.candidate_refined_gaps.values())
    dropped = sorted(
        set(report.eligible_structural()) - set(report.eligible_refined())
    )
    dropped_text = ", ".join(f"`{tool}`" for tool in dropped) or "none"

    add("## FINAL rule (owner decision 1, 2026-10-26)")
    add("")
    add(
        "The required-arguments rule is **description-aware refinement**. "
        "A row is excluded only when a gold call omits a **core input**. A "
        "missing parameter is not a core input when either lexical condition "
        "holds; the filter/pagination/sort name check has precedence:"
    )
    add("")
    add("1. **filter/pagination/sort** — the parameter name matches")
    add("")
    add("   ```")
    add(
        "   (?:^|_)(?:filter|filters|limit|offset|page|per_page|sort|sort_by|"
    )
    add("   order|order_by|descending|ascending|skip|cursor|top)(?:$|_)")
    add("   ```")
    add("")
    add("   with `re.IGNORECASE`; **or**")
    add("2. **optional-by-description** — the parameter description matches")
    add("")
    add("   ```")
    add("   \\boptional\\b|\\bdefaults?\\b|\\bdefault\\s+(?:is|to|values?)\\b")
    add(
        "   |\\bif\\s+(?:provided|specified|given|omitted|not\\s+provided|none)\\b"
    )
    add("   |\\bnot\\s+required\\b|\\bcan\\s+be\\b")
    add("   ```")
    add("")
    add("   with `re.IGNORECASE`.")
    add("")
    add(
        "Otherwise the parameter is **core-input** and the row is excluded. "
        "A parameter with a literal `default` key is optional before this "
        "rule runs. The implementation lives in `categorize_parameter` and "
        "`normalize_parameters` in `scripts/select_candidates.py`; this audit "
        "imports it, so the two scripts cannot drift."
    )
    add("")
    add(
        f"**Measured result under the FINAL rule:** "
        f"{_fmt(report.refined_excluded_rows)} rows excluded, "
        f"{_fmt(report.category_instances[CATEGORY_CORE])} core-input "
        f"instances, **{refined_pool} eligible tools**. The "
        f"{interim_pool}-tool, {_fmt(report.excluded_rows)}-row interim "
        "figures are historical only. Tools that lose the 100-case floor "
        f"under the final rule: {dropped_text}."
    )
    add("")
    add("| Rule | Rows excluded | Eligible tools | Candidate rows removed |")
    add("|---|---:|---:|---:|")
    add(
        f"| **FINAL: description-aware refinement** "
        f"| **{_fmt(report.refined_excluded_rows)}** | **{refined_pool}** "
        f"| **{_fmt(refined_gaps)}** |"
    )
    add(
        f"| Historical: interim no-`default` heuristic "
        f"| {_fmt(report.excluded_rows)} | {interim_pool} | "
        f"{_fmt(interim_gaps)} |"
    )
    add(f"| Historical: no filter | 0 | {structural_pool} | 0 |")
    add("")
    add(
        f"**Candidate freeze is untouched.** The final rule removes "
        f"{_fmt(refined_gaps)} candidate single-tool rows: all "
        f"{len(FROZEN_CANDIDATES)} candidate counts, the frozen order, and its "
        "sha256 are unchanged, so no candidate replacement is triggered. The "
        "70 vs 71 difference is `integrate` falling from 101 to 95 usable "
        "cases; `integrate` is not a candidate."
    )
    add("")
    add(
        "The public card describes a `required` boolean; the pinned revision "
        "has zero `required` keys in its 362,482 parameter descriptors. That "
        "card-vs-data discrepancy remains open and must be recorded in "
        "pre-registration."
    )
    add("")

    return "\n".join(lines).rstrip() + "\n"


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

    expectations = [
        ("excluded rows", report.excluded_rows, EXPECTED_EXCLUDED_ROWS),
        (
            "missing-parameter instances",
            report.missing_instances,
            EXPECTED_MISSING_INSTANCES,
        ),
        ("malformed gold rows", report.malformed_gold_rows, 0),
        (
            "core-input excluded rows",
            report.refined_excluded_rows,
            EXPECTED_REFINED_ROWS,
        ),
        (
            "core-input instances",
            report.category_instances[CATEGORY_CORE],
            EXPECTED_CORE_INSTANCES,
        ),
        (
            "structural pool",
            len(report.eligible_structural()),
            EXPECTED_STRUCTURAL_POOL,
        ),
        (
            "interim pool",
            len(report.eligible_interim()),
            EXPECTED_INTERIM_POOL,
        ),
        (
            "refined pool",
            len(report.eligible_refined()),
            EXPECTED_REFINED_POOL,
        ),
    ]
    for label, actual, expected in expectations:
        if actual != expected:
            failures.append(f"{label} {actual} != {expected}")

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
