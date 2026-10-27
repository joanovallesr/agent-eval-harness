#!/usr/bin/env python3
"""Compute the EVAL_DESIGN §7 usable-cases sensitivity for ``m`` and ``u``.

The §7 variance-components formula is fixed::

    u = sqrt((m - 1) / chi2.ppf(0.20, m - 1))

where ``m`` is the number of dev tools in the leave-one-tool-out estimate.
This script does not pick a final threshold. It reports, from non-candidate
dev tools only, how ``m`` and ``u`` move as the minimum usable single-tool
cases per dev tool rises. It makes no model call and runs no tool.

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
PLACEHOLDER_M = 59


def pessimistic_u(m: int) -> float:
    """EVAL_DESIGN §7 upper-limit multiplier for an m-tool LOTO estimate."""
    if m <= 1:
        raise ValueError(f"m must be >= 2 to estimate variance, got {m}")
    return math.sqrt((m - 1) / chi2.ppf(0.20, m - 1))


@dataclass(frozen=True)
class SensitivityRow:
    threshold: int
    structural_m: int
    structural_u: float
    filtered_m: int
    filtered_u: float


@dataclass(frozen=True)
class Sensitivity:
    rows: tuple[SensitivityRow, ...]
    structural_exactly_one: int
    filtered_exactly_one: int
    structural_total: int
    filtered_total: int
    placeholder_u: float


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
                structural_u=pessimistic_u(structural_m),
                filtered_m=filtered_m,
                filtered_u=pessimistic_u(filtered_m),
            )
        )

    return Sensitivity(
        rows=tuple(rows),
        structural_exactly_one=sum(1 for c in structural.values() if c == 1),
        filtered_exactly_one=sum(1 for c in filtered.values() if c == 1),
        structural_total=len(structural),
        filtered_total=len(filtered),
        placeholder_u=pessimistic_u(PLACEHOLDER_M),
    )


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
        structural = (
            f"**{row.structural_m:,} (placeholder)**"
            if row.threshold == 100 and row.structural_m == PLACEHOLDER_M
            else f"{row.structural_m:,}"
        )
        lines.append(
            f"| ≥{row.threshold} | {structural} | {row.structural_u:.3f} "
            f"| {row.filtered_m:,} | {row.filtered_u:.3f} |"
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

    print(f"revision: {args.revision}")
    print(render_table(sensitivity))
    print(f"placeholder u at m = {PLACEHOLDER_M}: {sensitivity.placeholder_u:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
