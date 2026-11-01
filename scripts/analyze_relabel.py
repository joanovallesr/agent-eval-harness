"""Analyze the Phase-5 blind relabel batch against the original labels."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from eval.calibration import analyse_relabel


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze a relabel batch")
    parser.add_argument("--original-judge", required=True)
    parser.add_argument("--relabel-judge", required=True)
    parser.add_argument("--relabel-mapping", required=True)
    parser.add_argument("--labels", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--n-resamples", type=int, default=2000)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    result = analyse_relabel(
        args.original_judge,
        args.relabel_judge,
        args.relabel_mapping,
        args.labels,
        n_resamples=args.n_resamples,
        seed=args.seed,
    )
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n"
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
