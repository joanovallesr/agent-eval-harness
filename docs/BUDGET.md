# Budget Ledger — Agent + Eval Harness

Hard cap: **$20.00** for the entire project, all phases.

## The inequality (must hold on actuals before the test run starts)

```
20.00 − pilot − effort_slices − dev_spend − judge_spend − multistep_traces
      − opus_spot − ci_spend − reserve ≥ test_spend
```

`reserve` = cost of one full aborted-and-resumed test run on the most
expensive arm. Costs from **logged API usage**, never estimates. Cost ratios
use list-price token costs; actual spend is reported separately.

## Running actuals

| Line | Ceiling | Actual | Notes |
|---|---|---|---|
| phase2_mini_smoke (20 cases × 3 arms) | $1.00 | $0.073719 | list_sync; mini/smoke only; excluded from headline results |
| phase3_pilot (30 cases × 3 arms, k=1) | $1.00 | $0.095948 | list_sync; pilot only; excluded from headline results; 30 non-candidate tools |
| effort_slices | — | — | set from pilot |
| dev_spend | — | — | set from pilot |
| judge_spend (GPT-6 Luna) | — | — | labels + calibration |
| multistep_traces (haiku-agent + sonnet-agent) | — | — | ~30 tasks × 2 models |
| opus_spot (~50 cases, optional) | — | — | only if budget allows |
| ci_spend | — | — | ~$0.05 per 50-case Haiku gate run |
| reserve (1 aborted+resumed test run) | — | — | most expensive arm |
| **test_spend** | — | — | `C · n_t · k · measured_cost/case` |

Phase 3 offline tooling (Task 0 resume fix + Task A sampling/analysis/effort
plumbing) completed with **$0.000000** model spend. The Stage 1 pilot then
completed under its **$1.00** ceiling with **$0.095948** actual list-price
spend. The ledger total now stands at **$0.169667**. Stage 2 has not run.

Phase 2 mini-set smoke spend is pipeline validation, not a headline result. It
counts against the $20 hard cap; subtract it alongside the inequality terms
when checking remaining budget.

Second line of defense: a provider-console spend limit (set up in Phase 2).
The harness per-run ceiling is the third.
