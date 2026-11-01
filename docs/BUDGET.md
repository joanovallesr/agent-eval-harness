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
| judge_spend (GPT-6 Luna) | — | — | not run yet; Part 2; must fit inside the Phase 5 ceiling |
| multistep_traces (haiku-agent + sonnet-agent) | $1.50 (Phase 5 total) | $1.287014 | list_sync; 60 final traces plus one smoke and two superseded runs; see `docs/PHASE5_MULTISTEP.md` |
| opus_spot (~50 cases, optional) | — | — | only if budget allows |
| ci_spend | — | — | ~$0.05 per 50-case Haiku gate run |
| reserve (1 aborted+resumed test run) | — | — | most expensive arm |
| **test_spend** | — | — | `C · n_t · k · measured_cost/case` |

Phase 3 offline tooling (Task 0 resume fix + Task A sampling/analysis/effort
plumbing) completed with **$0.000000** model spend. The Stage 1 pilot then
completed under its **$1.00** ceiling with **$0.095948** actual list-price
spend. Stage 2 ceiling: **$1.03**; final spend: **$1.023421**. Stage 3 dev
sample: final spend **$1.264953** against a **$1.50** ceiling. The ledger
total is **$2.458041**. Chosen efforts: Haiku shared = **`low`**;
Sonnet = **`api_default`**. Stage 4 is complete.

The Phase 4 pre-registration is tagged `pre-registration-v1`; the test sample
is materialized. It closes the inequality at C = 8, `n_t = 117`, k = 1: test
spend **$3.288878**, reserve
**$5.972325**, later-phase allowance **$2.000000**, and **$6.280757** of slack
against the $20 hard cap. These are projections, not spend; the ledger above
stays on actuals.

Phase 5 Part 1 trace generation is complete. The final 60-trace run cost
**$0.414715**; a single-task smoke run and two superseded full runs cost
**$0.872299** more because I fixed fixture-matching defects and regenerated the
set. Phase 5 trace spend is **$1.287014** against the **$1.50** Phase 5
ceiling, leaving **$0.212986** for judge calls. No judge spend has occurred.
With Phase 5 traces included, the ledger total is **$3.745055**. On actuals so
far the test inequality is

```text
20.00 − 2.458041 (through Phase 3) − 1.287014 (Phase 5 traces)
      − 0.000000 (judge) − 5.972325 (reserve) = 10.282620 ≥ 3.288878  ✓
```

The pre-registration's $2.000000 later-phase allowance is still the planning
figure; this ledger records the $1.287014 actually spent so far against it.

Phase 2 mini-set smoke spend is pipeline validation, not a headline result. It
counts against the $20 hard cap; subtract it alongside the inequality terms
when checking remaining budget.

Second line of defense: a provider-console spend limit (set up in Phase 2).
The harness per-run ceiling is the third.
