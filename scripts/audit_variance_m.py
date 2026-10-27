#!/usr/bin/env python3
"""Compute the EVAL_DESIGN §7 usable-cases sensitivity for ``m`` and ``u``.

The §7 variance-components formula is fixed::

    u = sqrt((m - 1) / chi2.ppf(0.20, m - 1))

where ``m`` is the number of dev tools in the leave-one-tool-out estimate.
The final rule (owner decision 2, 2026-10-26) is **≥10 usable cases in the
actual Phase 3 dev sample**; this script reports how ``m`` and ``u`` move as
the threshold rises, from non-candidate dev tools only. The printed numbers
are full-dataset illustrations, not Phase-3 values. It makes no model call and
runs no tool.

Run::

    python scripts/audit_variance_m.py \
        --revision 26d14ebfe18b1f7b524bd39b404b50af5dc97866
"""

from __future__ import annotations

import argparse
import math
from dataclasses import dataclass

from scipy.stats import chi2

from select_candidates import FROZEN_CANDIDATES, PINNED_REVISION, analyze

THRESHOLDS = (2, 5, 10, 20, 50, 100)
CHOSEN_THRESHOLD = 10
EXPECTED_EXACTLY_ONE = 340
EXPECTED_TOTAL_DEV_TOOLS = 2_848


def pessimistic_u(m: int) -> float:
    """EVAL_DESIGN §7 upper-limit multiplier for an m-tool LOTO estimate."""
    if m <= 1:
        raise ValueError(f"m must be >= 2 to estimate variance, got {m}")
    return math.sqrt((m - 1) / chi2.ppf(0.20, m - 1))


def _u_or_none(m: int) -> float | None:
    """u needs at least two tools; a smaller pool cannot estimate variance."""
    return pessimistic_u(m) if m >= 2 else None


@dataclass(frozen=True)
class SensitivityRow:
    threshold: int
    structural_m: int
    structural_u: float | None
    filtered_m: int
    filtered_u: float | None


@dataclass(frozen=True)
class Sensitivity:
    rows: tuple[SensitivityRow, ...]
    structural_exactly_one: int
    filtered_exactly_one: int
    structural_total: int
    filtered_total: int


def build_sensitivity(report) -> Sensitivity:
    """Build the table from non-candidate dev tools only."""
    candidates = set(FROZEN_CANDIDATES)
    structural = {
        tool: count
        for tool, count in report.single_tool_counts.items()
        if tool not in candidates
    }
    filtered = {
        tool: count
        for tool, count in report.required_filtered_single_tool_counts.items()
        if tool not in candidates
    }

    rows = []
    for threshold in THRESHOLDS:
        structural_m = sum(1 for count in structural.values() if count >= threshold)
        filtered_m = sum(1 for count in filtered.values() if count >= threshold)
        rows.append(
            SensitivityRow(
                threshold=threshold,
                structural_m=structural_m,
                structural_u=_u_or_none(structural_m),
                filtered_m=filtered_m,
                filtered_u=_u_or_none(filtered_m),
            )
        )

    return Sensitivity(
        rows=tuple(rows),
        structural_exactly_one=sum(1 for c in structural.values() if c == 1),
        filtered_exactly_one=sum(1 for c in filtered.values() if c == 1),
        structural_total=len(structural),
        filtered_total=len(filtered),
    )


def _format_u(value: float | None) -> str:
    return f"{value:.3f}" if value is not None else "—"


def render_table(sensitivity: Sensitivity) -> str:
    lines = [
        "| Threshold: usable single-tool cases per dev tool "
        "| m (structural) | u | m (all filters) | u |",
        "|---:|---:|---:|---:|---:|",
        (
            f"| exactly 1 | {sensitivity.structural_exactly_one:,} (excluded) "
            f"| — | {sensitivity.filtered_exactly_one:,} (excluded) | — |"
        ),
    ]
    for row in sensitivity.rows:
        if row.threshold == CHOSEN_THRESHOLD:
            structural = f"**{row.structural_m:,} (chosen rule; illustration)**"
            filtered = f"**{row.filtered_m:,} (illustration)**"
        else:
            structural = f"{row.structural_m:,} (sensitivity only)"
            filtered = f"{row.filtered_m:,}"
        lines.append(
            f"| ≥{row.threshold} | {structural} | {_format_u(row.structural_u)} "
            f"| {filtered} | {_format_u(row.filtered_u)} |"
        )
    lines.append("")
    lines.append(
        f"Exactly one usable case (excluded from m): "
        f"{sensitivity.structural_exactly_one:,} structural / "
        f"{sensitivity.filtered_exactly_one:,} all-filter dev tools. "
        f"Total non-candidate dev tools: {sensitivity.structural_total:,} "
        f"structural / {sensitivity.filtered_total:,} all-filter."
    )
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--revision",
        default=PINNED_REVISION,
        help=f"pinned xLAM revision (default: {PINNED_REVISION})",
    )
    parser.add_argument("--split", default="train", help="dataset split")
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
    sensitivity = build_sensitivity(report)

    failures = []
    if args.revision != PINNED_REVISION:
        failures.append(f"revision {args.revision} != pinned {PINNED_REVISION}")
    if sensitivity.structural_exactly_one != EXPECTED_EXACTLY_ONE:
        failures.append(
            "exactly-one dev tools "
            f"{sensitivity.structural_exactly_one} != {EXPECTED_EXACTLY_ONE}"
        )
    if sensitivity.structural_total != EXPECTED_TOTAL_DEV_TOOLS:
        failures.append(
            "dev tools "
            f"{sensitivity.structural_total} != {EXPECTED_TOTAL_DEV_TOOLS}"
        )
    if failures:
        print("SENSITIVITY: FAIL")
        for failure in failures:
            print(f"  - {failure}")
        return 1

    print(f"revision: {args.revision}")
    print(render_table(sensitivity))
    print(
        f"chosen rule: >= {CHOSEN_THRESHOLD} usable Phase-3 dev cases per "
        "non-candidate dev tool; m and u are recomputed in Phase 3"
    )
    print("SENSITIVITY: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
