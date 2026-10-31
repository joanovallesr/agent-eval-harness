# Phase 3 Dev Analysis Dry Run

Status: dev/dry-run only. C and n_t are proposals, not locked. Phase 4 has not
started.

## Run

- Run: `results/phase3-dev-20261031`
- 30 dev tools × 12 cases × 3 arms, k = 1, seed 45
- Efforts: `haiku-agent` `low`, `haiku-direct` `low`, `sonnet-direct`
  `api_default`
- 360 cases per arm, no aborts
- Dev spend: **$1.264953** at pinned list prices

## Primary comparison — haiku-agent vs sonnet-direct

| Metric | Value |
|---|---:|
| Paired cases | 360 |
| Held-out dev tools | 30 |
| Mean paired difference | **+0.00556** (+0.56 points) |
| 95% t-interval on per-tool means | **[-0.03989, +0.05101]** points |
| Case-bootstrap sensitivity | [-0.01944, +0.03056] |
| Discordant rate | 0.0556 |
| Aggregate cost ratio | **0.050823** |
| 95% t-interval on per-tool log cost ratios | **[0.049560, 0.051644]** |
| Cost-ratio bootstrap sensitivity | [0.049957, 0.051640] |

The clustered t-interval governs; the case-level bootstrap is narrower and is
reported only as a sensitivity check. The aggregate cost ratio clears the 0.10
bar.

## Secondary comparison — haiku-direct vs sonnet-direct

Intervals only; no pass/fail framing.

| Metric | Value |
|---|---:|
| Mean paired difference | **+0.00556** (+0.56 points) |
| 95% t-interval on per-tool means | **[-0.04543, +0.05654]** points |
| Discordant rate | 0.0611 |
| Aggregate cost ratio | **0.050450** |
| 95% t-interval on per-tool log cost ratios | **[0.049530, 0.051388]** |

## Variance components and sizing inputs

| Component | Value |
|---|---:|
| `tau2` (full-sample) | **0.0110690** |
| `tau2` (pessimistic, LOTO maximum) | **0.0114849** |
| `sigma2_within` | **0.0449495** |
| Observed LOTO SD | 0.121716 |
| LOTO SD, minimum | 0.093582 |
| LOTO SD, median / maximum | 0.123866 / 0.123866 |
| `m` (dev tools with ≥10 usable cases) | **30** |
| `u` | **1.135922** |
| `n_t` rule, pessimistic `tau2` | **117** |
| `n_t` rule, full-sample `tau2` | 121 |
| `s_pess` | **0.123302** (12.33 points) |

`m` is 30 because every dev tool contributes 12 usable cases. `u` is computed
from that m; it is not inherited from another m.

## §7 power / feasibility

| C | Power multiplier | Max `s_pess` at δ=0.05, Δ=0 | Passes |
|---:|---:|---:|---|
| 6 | 1.424838 | 0.035092 | **No** |
| 7 | 1.267169 | 0.039458 | **No** |
| 8 | 1.152815 | 0.043372 | **No** |

The proposed C from `{6,7,8}` is **none**. The binding constraint is the
between-tool spread, not the budget or `n_t`: at `s_pess = 0.123302`, even
C = 8 produces a power product of 0.1421 against the 0.05 margin. The observed
30-tool dev interval happens to clear the −5-point margin, but the §7
pessimistic power check for a 6–8-tool test does not. A C of roughly 50 tools
would be needed at this spread, which is outside the frozen 12-candidate list.

The §7 fallback is therefore required in Phase 4: a wider margin justified a
priori, lower power stated openly, or dropping the binary framing. I have not
chosen a fallback here.

## Budget check at C = 8, n_t = 117, k = 1

Dev-measured cost per case:

| Arm | Cost/case |
|---|---:|
| `haiku-agent` | $0.00016214 |
| `haiku-direct` | $0.00016127 |
| `sonnet-direct` | $0.00319034 |
| All three arms | $0.00351376 |

| C | Test spend | Test reserve | Ledger + later-phase allowance + reserve | Slack after test spend | Power |
|---:|---:|---:|---:|---:|---|
| 6 | $2.466658 | $4.479244 | $8.937285 | $8.596057 | fail |
| 7 | $2.877768 | $5.225784 | $9.683825 | $7.438407 | fail |
| **8** | **$3.288878** | **$5.972325** | **$10.430366** | **$6.280757** | **fail** |

At C = 8:

```text
20.00 − 2.458041 (ledger) − 2.000000 (later-phase allowance)
      − 5.972325 (reserve) = 9.569634 ≥ 3.288878 (test spend)  ✓
```

The test reserve is one aborted-and-resumed test run on the most expensive arm
(`sonnet-direct`): `2 × 8 × 117 × $0.00319034 = $5.972325`. The **$2.000000**
later-phase allowance covers judge, multistep traces, and CI, with the optional
Opus spot check droppable if needed; no later-phase spend has occurred.

Budget is not the binding constraint. The power check fails at every C in
`{6,7,8}`.

## Dev failure policy — observed labels

| Event | `haiku-agent` | `haiku-direct` | `sonnet-direct` |
|---|---:|---:|---:|
| API error / timeout | 0 | 0 | 0 |
| Refusal | 0 | 0 | 0 |
| Text-only reply | 0 | 0 | 0 |
| Malformed output | 0 | 0 | 0 |
| `max_tokens` truncation | 1 | 1 | 2 |
| Schema-validation failure after retries | 0 | 0 | 0 |
| Schema/gold inconsistency | 0 | 0 | 0 |
| `right-tool-wrong-params` | 60 | 60 | 62 |
| `missing-call` | 2 | 2 | 1 |
| `truncated` (new taxonomy label) | 1 | 1 | 2 |

`truncated` is now recorded in `docs/FAILURE_TAXONOMY.md` as an output-limit
label: the response stopped at `max_tokens` before the call set was complete.
The `truncated` label and the `max_tokens` truncation row describe the same
1/1/2 events, not additional failures. It is descriptive only; no score,
count, exclusion, or failure-policy rule changed. No failure-policy exclusion
was added.

## Status

Stage 4 dry run is complete. Phase 4 (pre-registration) has not started.
