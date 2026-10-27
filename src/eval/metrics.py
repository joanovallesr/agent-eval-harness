"""Metrics: accuracy, pass@k, cost/task, latency — with uncertainty attached.

Rule: no bare percentages from small n. Every reported number carries a 95%
confidence interval (case-level bootstrap) or comes from the paired t-interval
on per-tool mean differences. See docs/EVAL_DESIGN.md §7 (estimator) and §10
(CI policy).

McNemar is intentionally **not** used: the design's interval is the t-interval
on per-tool mean paired differences, with a case-level bootstrap as a
sensitivity check (EVAL_DESIGN §7), not a per-case McNemar test.

TODO(phase-2): implement
- accuracy(results) -> (rate, ci_low, ci_high)
- pass_at_k(results, k) -> (rate, ci_low, ci_high)
- paired_tool_interval(results_a, results_b) -> per-tool mean differences,
  t-interval, and the case-level bootstrap sensitivity check
- cost_per_task(traces, pricing) -> (mean, ci_low, ci_high)
- latency_stats(traces) -> {"p50": ..., "p95": ...}
- CostTracker: accumulate input/output tokens per task in agent.py
"""

from __future__ import annotations

# Pricing table: update when provider pricing changes. Cost per 1M tokens.
PRICING_PER_MTOK = {
    # TODO(phase-2): fill in for the agent model and the Sonnet-5.5
    # direct-prompt baseline model.
    # Example: "claude-...": {"input": 3.00, "output": 15.00},
}
