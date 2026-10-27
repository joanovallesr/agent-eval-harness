"""Eval harness: run cases -> agent -> trace -> score.

One command, one JSONL of results. The harness never tunes on the held-out
set — dev runs use --subset data/xlam60k_dev.jsonl, held-out runs are manual
and one-shot (see docs/EVAL_DESIGN.md section 2).

TODO(phase-2): implement run() — load JSONL cases, Agent.run each, score with
metrics, write results JSONL + summary JSON (accuracy, pass@k, cost/task,
latency p50/p95, all with 95% CIs).
TODO(phase-2): --baseline mode: direct prompting with no tool loop, so the
results table always has the "dumb version" row beside the agent row.
TODO(phase-7): --gate mode: compare summary against baselines/main.json and
exit non-zero on regression (used by CI).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def run_subset(cases_path: Path, out_dir: Path, baseline: bool = False) -> dict:
    """Run the agent over one JSONL subset. Returns the summary dict."""
    raise NotImplementedError("Phase 2: implement the harness run loop")


def main() -> None:
    parser = argparse.ArgumentParser(description="Agent eval harness")
    parser.add_argument("--subset", required=True, help="JSONL case file")
    parser.add_argument("--out", default="results", help="output directory")
    parser.add_argument("--baseline", action="store_true",
                        help="frontier-LLM direct-prompt baseline, no agent loop")
    parser.add_argument("--gate", action="store_true",
                        help="fail (exit 2) if below baselines/main.json")
    args = parser.parse_args()

    summary = run_subset(Path(args.subset), Path(args.out), baseline=args.baseline)
    print(json.dumps(summary, indent=2))
    if args.gate:
        raise NotImplementedError("Phase 5: implement the CI gate comparison")


if __name__ == "__main__":
    main()
