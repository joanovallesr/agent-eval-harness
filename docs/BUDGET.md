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
| phase3_effort_slices | $1.03 | $1.023421 | list_sync; Haiku shared = `low`; Sonnet = `api_default`; 100 cases/config; selected by the §2 pure functions |
| phase3_dev (30 tools × 12 cases × 3 arms, k=1) | $1.50 | $1.264953 | list_sync; dev only; Haiku `low`, Sonnet `api_default`; 360 cases |
| judge_spend (GPT-6 Luna) | — | — | labels + calibration |
| multistep_traces (haiku-agent + sonnet-agent) | — | — | ~30 tasks × 2 models |
| opus_spot (~50 cases, optional) | — | — | only if budget allows |
| ci_spend | — | — | ~$0.05 per 50-case Haiku gate run |
| reserve (1 aborted+resumed test run) | — | — | most expensive arm |
| **test_spend** | — | — | `C · n_t · k · measured_cost/case` |

Phase 3 offline tooling (Task 0 resume fix + Task A sampling/analysis/effort
plumbing) completed with **$0.000000** model spend. The Stage 1 pilot then
completed under its **$1.00** ceiling with **$0.095948** actual list-price
spend. I approved raising the Stage 2 ceiling to **$1.03**; the stage resumed
and completed at **$1.023421** actual list-price spend. I then ran the approved
Stage 3 dev sample for **$1.264953** under its **$1.50** ceiling. The ledger
total now stands at **$2.458041**. Chosen efforts: Haiku shared = **`low`**;
Sonnet = **`api_default`**. I have not started Stage 4.

Phase 2 mini-set smoke spend is pipeline validation, not a headline result. It
counts against the $20 hard cap; subtract it alongside the inequality terms
when checking remaining budget.

Second line of defense: a provider-console spend limit (set up in Phase 2).
The harness per-run ceiling is the third.
