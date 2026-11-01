# Held-Out Test Results

Status: I ran the single pre-registered pass over the held-out test split and scored it with MATCH_SPEC v1.1 exactly as implemented. I report accuracy as an estimate, not as a pass/fail verdict. Cost parity is the one binary rule I apply.

## Run contract

- I ran `phase6-heldout-20261031` at commit `e22f678500482af4e43c9c3cc36e81a0e15a8ae8` (phase6/heldout / heldout_test).
- 936 cases, 8 tools, 117 cases per tool, k = 1, 3 arms.
- `haiku-agent` and `haiku-direct`: `claude-haiku-5-5` at `low`; `sonnet-direct`: `claude-sonnet-5-5` at `api_default`.
- Prompt `phase2-mini-v1`, `max_tokens` 1024, pricing basis `list_sync` from `config/pricing.list_sync.2026-10-31.json`.
- I recorded `haiku-agent` 936, `haiku-direct` 936, `sonnet-direct` 936; aborted records 0. Resume events: 0; the resume-equality path was not exercised because none was needed.
- I recorded the run contract field `dev_vs_final` as `heldout_test`. The harness's generic non-mini `note` string is not part of the contract and is not used here.
- I measured actual list-price spend for this run at **$3.050689**.
- A separate real-time monitor watched every checkpoint during the run for nonzero cache tokens; it never fired.

## Pre-run verification

- I verified that tag `pre-registration-v1` dereferences to `cc8b79cf1bf5ca6f28b613957baaec50d54512fe`.
- I checked `docs/PRE_REGISTRATION.md` sha256 at the tag and at analysis time: `49b42de8d682f890d9991ff97172a348e32f72d0687d557d03ddf503f5e8bc9c` / `49b42de8d682f890d9991ff97172a348e32f72d0687d557d03ddf503f5e8bc9c`; identical: **True**.
- I checked the test sample sha256 `ddd23366eabb1f9bf148d7c129c789073c54caa6d0a6521b50cba3d51cb5dc06` (recorded match: **True**); metadata sha256 `b64a5f99a8a54645aad2671a748c9047ac58fa892bf0b4e0d7c484c1689de7ae` (recorded match: **True**).
- I found 0 duplicate case IDs and 0 gold tools outside the pre-registered set.
- I recomputed the ledger before the run: **$3.765892** of the **$20.00** hard cap. Pre-registered test projection **$3.288878**; reserve **$5.972325**; available with the reserve intact **$10.261783**; slack after the projection **$6.972905**.
- I confirmed `ANTHROPIC_API_KEY` was present in the repo-root `.env` before the run; I never read the value into a document or log.
- I ran the offline suite, including the MATCH_SPEC §9 pair tests, and it was green before the run.
- I scoped two Phase-2 loader guards to mini/dev before the run: test-stage candidate gold and test-stage gold-schema conformance. The scorer, MATCH_SPEC, pre-registration, and test sample were unchanged; the checksums above are the pre-registered ones.

## Primary: haiku-agent minus sonnet-direct

I estimate the equal-weighted mean of the 8 per-tool paired mean exact-match differences. The interval is a t-interval clustered on tool.

| Estimate | 95% clustered t-interval | df | Tool SD | Discordant rate | Case-bootstrap sensitivity |
|---:|---:|---:|---:|---:|---:|
| **-4.06 pts** | **[-13.10, +4.98] pts** | 7 | 0.108184 | 0.042735 | [-5.45, -2.78] pts |

I report this as an estimate. I give no non-inferiority, superiority, or equivalence verdict, and I do not convert the interval into one.

The case-level bootstrap is narrower ([-5.45, -2.78] pts) and excludes zero, while the clustered t-interval includes zero. The two disagree; the pre-registered clustered interval governs, and I do not read the bootstrap as a verdict.

### Per-tool mean exact match, primary pair

| Tool | n | haiku-agent | sonnet-direct | Difference (pts) |
|---|---:|---:|---:|---:|
| `average` | 117 | 99.15% | 99.15% | +0.00 |
| `calculate_investment_return` | 117 | 1.71% | 32.48% | -30.77 |
| `calculate_quarterly_dividend` | 117 | 98.29% | 99.15% | -0.85 |
| `calculate_standard_deviation` | 117 | 95.73% | 94.87% | +0.85 |
| `chi_square_independence_test` | 117 | 82.05% | 83.76% | -1.71 |
| `find_majority_element` | 117 | 100.00% | 100.00% | +0.00 |
| `find_pairs_with_sum` | 117 | 100.00% | 100.00% | +0.00 |
| `get_ip_location` | 117 | 98.29% | 98.29% | +0.00 |

The largest per-tool difference is `calculate_investment_return` (1.71% for haiku-agent vs 32.48% for sonnet-direct, -30.77 pts). The other 7 tools differ by at most 1.71 points in absolute value. These are descriptive per-tool means; the clustered interval above governs.

## Secondary: haiku-direct minus sonnet-direct

I report intervals only, with no pass/fail framing.

| Estimate | 95% clustered t-interval | df | Discordant rate | Case-bootstrap sensitivity |
|---:|---:|---:|---:|---:|
| **-4.27 pts** | **[-12.97, +4.43] pts** | 7 | 0.042735 | [-5.56, -2.99] pts |

## Cost claim: the one binary verdict

I declare cost parity if and only if the upper bound of the 95% cost-ratio interval is at most 0.10. The interval is a t-interval on the 8 per-tool log ratios, exponentiated back to ratio scale.

| Estimate (geometric mean) | 95% cost-ratio interval | Aggregate ratio | Threshold | Verdict |
|---:|---:|---:|---:|---|
| 0.051669 | **[0.048392, 0.055169]** | 0.051733 | 0.10 | **parity holds** |

I report the case-bootstrap sensitivity for the aggregate ratio: [0.050592, 0.053096].

I compute cost from logged API usage at the pinned list prices. Thinking tokens are already included in `output_tokens` and are not added again. No batch rates and no prompt caching were used, so the logged list-price total is also the actual spend for this run.

### Cost per arm

| Arm | Total list-price cost | Cost per case |
|---|---:|---:|
| `haiku-agent` | $0.143190 | $0.000153 |
| `haiku-direct` | $0.139627 | $0.000149 |
| `sonnet-direct` | $2.767872 | $0.002957 |

### Per-tool cost ratio, haiku-agent / sonnet-direct

| Tool | Ratio |
|---|---:|
| `average` | 0.050297 |
| `calculate_investment_return` | 0.049183 |
| `calculate_quarterly_dividend` | 0.048671 |
| `calculate_standard_deviation` | 0.054863 |
| `chi_square_independence_test` | 0.061367 |
| `find_majority_element` | 0.050019 |
| `find_pairs_with_sum` | 0.049868 |
| `get_ip_location` | 0.050240 |

## Per-tool exact and partial, all 3 arms

I report exact as the primary metric and partial as the MATCH_SPEC diagnostic. Every cell is an aggregate over that tool's cases.

| Tool | n | Agent exact | Direct exact | Sonnet exact | Agent partial | Direct partial | Sonnet partial |
|---|---:|---:|---:|---:|---:|---:|---:|
| `average` | 117 | 99.15% | 99.15% | 99.15% | 99.15% | 99.15% | 99.15% |
| `calculate_investment_return` | 117 | 1.71% | 2.56% | 32.48% | 66.95% | 67.24% | 77.21% |
| `calculate_quarterly_dividend` | 117 | 98.29% | 97.44% | 99.15% | 99.15% | 98.72% | 99.57% |
| `calculate_standard_deviation` | 117 | 95.73% | 94.87% | 94.87% | 95.73% | 94.87% | 94.87% |
| `chi_square_independence_test` | 117 | 82.05% | 81.20% | 83.76% | 88.89% | 87.18% | 90.60% |
| `find_majority_element` | 117 | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% |
| `find_pairs_with_sum` | 117 | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% |
| `get_ip_location` | 117 | 98.29% | 98.29% | 98.29% | 98.29% | 98.29% | 98.29% |

## Secondary diagnostics (MATCH_SPEC §7)

These are diagnostics only; they never change the primary result. The lenient-string variant is reconstructed from the recorded final-attempt calls and keeps every failure-policy zero.

| Arm | Partial | Name-only | Lenient exact |
|---|---:|---:|---:|
| `haiku-agent` | 93.52% | 99.36% | 84.40% |
| `haiku-direct` | 93.18% | 98.93% | 84.19% |
| `sonnet-direct` | 94.96% | 99.47% | 88.46% |

## Failure taxonomy

I assign exactly one descriptive label to every non-exact case. The counts sum to the non-exact count in every arm; `unclassified` is zero.

| Label | haiku-agent | haiku-direct | sonnet-direct |
|---|---:|---:|---:|
| truncated | 1 | 1 | 1 |
| refusal | 0 | 0 | 0 |
| infrastructure | 0 | 0 | 0 |
| missing-call | 0 | 8 | 3 |
| format-error | 5 | 0 | 0 |
| wrong-tool | 1 | 2 | 2 |
| extra-call | 0 | 0 | 0 |
| partial-answer | 0 | 0 | 0 |
| right-tool-wrong-params | 139 | 137 | 102 |
| **Non-exact total** | **146** | **148** | **108** |
| **Label total** | **146** | **148** | **108** |

### Format-failure and truncation diagnostics

| Arm | Format-failure rate | Truncation rate | Schema-failure rate | Cache-nonzero records |
|---|---:|---:|---:|---:|
| `haiku-agent` | 0.53% (5) | 0.11% (1) | 0.53% (5) | 0 |
| `haiku-direct` | 0.85% (8) | 0.11% (1) | 0.00% (0) | 0 |
| `sonnet-direct` | 0.32% (3) | 0.11% (1) | 0.00% (0) | 0 |

The format-failure rate counts `no_tool_use`, `malformed_block`, and `schema_validation_after_retries`; truncation is a separate diagnostic and is not folded into it. The event breakdown below covers only the format-failure kinds, so its events sum to the format-failure count.

| Format-failure event | haiku-agent | haiku-direct | sonnet-direct |
|---|---:|---:|---:|
| no_tool_use | 0 | 8 | 3 |
| schema_validation_after_retries | 5 | 0 | 0 |

## Phase 5 cross-reference

I report the secondary judge question as it actually landed: Cohen's κ is **undefined** because both label sets used one class (`correct`), over n = 30 report traces from 15 tasks, with raw agreement 30/30. The ship rule was **not met**. The human intra-rater ceiling was **not measured**. See `docs/PHASE5_CALIBRATION.md`.

## Limitations

- The primary interval has only 7 degrees of freedom and is clustered on 8 tools. It is wide by construction; the between-tool spread dominates.
- k = 1, so there is no repeat-level variance estimate. pass@1 equals accuracy and is not a separate result.
- 4 of 936 test cases carry at least one gold call whose arguments do not satisfy the case's own tool schema. MATCH_SPEC §1.3 does not classify that as malformed gold, so the cases stay in the pre-registered sample. MATCH_SPEC §7 anticipates the schema/gold inconsistency; I report it here as a limitation and do not remove or re-score it.
- The largest per-tool gap is `calculate_investment_return` (1.71% for haiku-agent vs 32.48% for sonnet-direct). The sampled rows for that tool show a rate-convention mismatch (decimal fraction vs percentage) that the tool description does not disambiguate; the Haiku models mostly emit one convention and Sonnet sometimes the other. MATCH_SPEC's strict value comparison scores those as non-exact. This is a dataset ambiguity, not a scorer defect, and I do not re-score it.
- The held-out tools come from one synthetic generator (xLAM-60k). This measures shift within that generator, not real-world generalization; I do not claim the latter.
- I ran the test split once. I did not restart after partial results, tune on test data, or reclassify post hoc.
- I observed zero cache usage in every response. The pricing basis has no cache rates; any nonzero cache usage would have triggered a re-pin and a stop.
- I publish only aggregates, counts, tool names, and checksums here. I publish no raw rows, queries, gold answers, full schemas, or traces.
