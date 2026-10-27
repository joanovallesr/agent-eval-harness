"""Metrics: accuracy, pass@k, cost/task, latency — with uncertainty attached.

Rule: no bare percentages from small n. Every reported number carries a 95%
confidence interval (bootstrap) or comes from a paired comparison (McNemar).
See docs/EVAL_DESIGN.md section 3.

TODO(phase-2): implement
- accuracy(results) -> (rate, ci_low, ci_high)
- pass_at_k(results, k) -> (rate, ci_low, ci_high)
- cost_per_task(traces, pricing) -> (mean, ci_low, ci_high)
- latency_stats(traces) -> {"p50": ..., "p95": ...}
- mcnemar(a_correct, b_correct) -> p-value, for agent-vs-baseline on the same cases
- CostTracker: accumulate input/output tokens per task in agent.py
"""

from __future__ import annotations

# Pricing table: update when provider pricing changes. Cost per 1M tokens.
PRICING_PER_MTOK = {
    # TODO(phase-2): fill in for the agent model and the frontier baseline model.
    # Example: "claude-...": {"input": 3.00, "output": 15.00},
}
