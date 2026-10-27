"""Eval harness: run cases -> agent -> trace -> score.

One command, one JSONL of results. Dev runs use
``--subset data/xlam60k_dev.jsonl``; the held-out run is manual and one-shot
(EVAL_DESIGN §5–§7). The harness never tunes on held-out tools.

Results contract (versioned, EVAL_DESIGN §12). The harness writes one record
per case per repeat and a per-arm summary:

Per-case record
    ``task_id``, ``arm``, ``model_id``, ``effort``, ``prompt_version``,
    ``repeat``, ``exact``, ``partial``, ``name_only``, ``format_failure``,
    ``schema_validation_failed``, ``retry_count``, ``validation_errors``,
    ``final_attempt_calls``, ``input_tokens``, ``output_tokens``,
    ``cost_usd`` (from logged API usage, never an estimate), ``latency_s``.

Summary (per arm)
    ``n_cases``, ``n_repeats``, ``exact_match_mean`` with its 95% interval,
    ``partial_mean``, ``name_only_rate``, ``cost_usd_total``,
    ``cost_per_task``, latency p50/p95, and the two rate diagnostics below.
    Repeats are averaged within a case before any paired difference is
    computed (EVAL_DESIGN §1); the summary never pools repeats as independent
    cases.

Rate diagnostics
    ``retry_rate`` = cases with ``retry_count > 0`` / scored cases, per arm.
    ``mean_retries_per_case`` = total schema-repair retries / scored cases.
    ``format_failure_rate`` = cases with ``format_failure`` / scored cases,
    with a breakdown of ``no_tool_use``, ``malformed_block``, and
    ``schema_validation_after_retries``. These are reported **alongside** the
    score, never instead of it (EVAL_DESIGN §4).

``retry_count`` counts **schema-repair** retries only. Infrastructure retries
(API 5xx/timeout) are recorded separately by the harness and are not part of
the agent-loop retry diagnostic.

TODO(phase-2): implement run() — load JSONL cases, Agent.run each, score with
the MATCH_SPEC scorer, write the per-case JSONL + summary JSON described
above (accuracy with 95% CIs, pass@k, cost/task, latency p50/p95, retry and
format-failure rates).
TODO(phase-2): --baseline mode: direct prompting with no tool loop, so the
results table always has the "dumb version" row beside the agent row.
TODO(phase-7): --gate mode: compare summary against baselines/main.json and
exit non-zero on regression (used by CI).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

RETRY_RATE_DEFINITION = (
    "cases with retry_count > 0 / scored cases, per arm; "
    "mean_retries_per_case = total schema-repair retries / scored cases"
)
FORMAT_FAILURE_RATE_DEFINITION = (
    "cases with format_failure true / scored cases, per arm, broken down by "
    "no_tool_use, malformed_block, and schema_validation_after_retries"
)

RESULTS_CONTRACT = {
    "per_case": [
        "task_id",
        "arm",
        "model_id",
        "effort",
        "prompt_version",
        "repeat",
        "exact",
        "partial",
        "name_only",
        "format_failure",
        "schema_validation_failed",
        "retry_count",
        "validation_errors",
        "final_attempt_calls",
        "input_tokens",
        "output_tokens",
        "cost_usd",
        "latency_s",
    ],
    "summary": [
        "n_cases",
        "n_repeats",
        "exact_match_mean",
        "exact_match_ci_low",
        "exact_match_ci_high",
        "partial_mean",
        "name_only_rate",
        "retry_rate",
        "mean_retries_per_case",
        "format_failure_rate",
        "format_failure_breakdown",
        "cost_usd_total",
        "cost_per_task",
        "latency_p50_s",
        "latency_p95_s",
    ],
    "retry_rate": RETRY_RATE_DEFINITION,
    "format_failure_rate": FORMAT_FAILURE_RATE_DEFINITION,
}


def run_subset(cases_path: Path, out_dir: Path, baseline: bool = False) -> dict:
    """Run one arm over one JSONL subset. Returns the summary dict."""
    raise NotImplementedError("Phase 2: implement the harness run loop")


def main() -> None:
    parser = argparse.ArgumentParser(description="Agent eval harness")
    parser.add_argument("--subset", required=True, help="JSONL case file")
    parser.add_argument("--out", default="results", help="output directory")
    parser.add_argument(
        "--baseline",
        action="store_true",
        help="Sonnet-5.5 direct-prompt baseline, no agent loop",
    )
    parser.add_argument(
        "--gate",
        action="store_true",
        help="fail (exit 2) if below baselines/main.json",
    )
    args = parser.parse_args()

    summary = run_subset(Path(args.subset), Path(args.out), baseline=args.baseline)
    print(json.dumps(summary, indent=2))
    if args.gate:
        raise NotImplementedError("Phase 7: implement the CI gate comparison")


if __name__ == "__main__":
    main()
