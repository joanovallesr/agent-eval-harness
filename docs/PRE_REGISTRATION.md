# Pre-Registration — Agent + Eval Harness

Status: pre-registration draft, 2026-10-31. The tag applied to the commit
carrying this file is the proof of "dated and committed"; no test row is
materialized before that tag. The tagged commit governs if this file and the
tag ever disagree.

This document supersedes the binary non-inferiority framing in
`docs/EVAL_DESIGN.md` per the 2026-10-31 amendment recorded there. Once
tagged, this file governs over `EVAL_DESIGN.md` where they differ.

## 1. Primary claim and estimand

The study is **estimation-first**. The primary output is the paired mean
difference (`haiku-agent` minus `sonnet-direct`), equal-weighted over the C
test tools, reported as an estimate with its clustered t-interval. There is
**no pass/fail verdict on accuracy** and no non-inferiority margin.

For tool `t`, let `d_t` be the mean paired difference in exact-match accuracy
over the `n_t` cases in that tool. Repeats are averaged within a case before
the difference is taken; k = 1, so this is a single score per case per arm. The
estimand is

```
d̄ = (1/C) · Σ_t d_t
```

with C = 8. No assumed true gap Δ enters the analysis. The reported interval
is

```
d̄ ± t(0.975, C−1) · s_tool / √C
```

where `s_tool` is the sample standard deviation of the eight per-tool mean
differences. With C = 8 the interval has 7 degrees of freedom.

I will report the estimate, the interval, the eight per-tool means, the
discordant rate, and a case-level bootstrap interval as a sensitivity check
(2,000 resamples, seed 42). The clustered t-interval governs. The interval is
an estimate, not a decision rule: I will not convert it into a
non-inferiority, superiority, or equivalence verdict.

The secondary comparison is `haiku-direct` vs `sonnet-direct`, reported with
intervals only and no pass/fail framing.

## 2. Cost claim — the binary rule

The cost claim stays binary. **Cost parity is declared if and only if the upper
bound of the 95% cost-ratio interval is ≤ 0.10.**

Cost is list-price token cost per task from logged API usage, covering every
agent API call including retries. Thinking tokens are already included in
`output_tokens` and are not added again. No prompt caching is sent; batch rates
are excluded. Actual spend is reported separately from the list-price cost
ratio.

For each tool, the cost ratio is

```
r_t = mean haiku-agent cost per case / mean sonnet-direct cost per case
```

The interval is a t-interval on the per-tool log ratios
`log(r_t)`, with the endpoints exponentiated back to ratio scale. The
case-level bootstrap interval is a sensitivity check. The aggregate cost ratio
is reported alongside but does not decide the cost claim; the interval does.

## 3. Arms, models, efforts, and interface

| Arm | Model ID | Effort | Behavior |
|---|---|---|---|
| `haiku-agent` | `claude-haiku-5-5` | `low` | Agent loop with schema-validation repair retries, cap 2. |
| `haiku-direct` | `claude-haiku-5-5` | `low` | Single direct prompt, no loop. |
| `sonnet-direct` | `claude-sonnet-5-5` | `api_default` | Single direct prompt, no loop. |

- A concrete effort is transmitted as `output_config={"effort": ...}`.
  `api_default` sends no effort field.
- Haiku shared effort is `low`: the lowest effort whose worst dev-slice score
  across the two Haiku arms was within the pre-registered tolerance of 0.02 of
  the best such score.
- Sonnet effort is `api_default`: no measured slice was strictly stronger.
  The one-case margin over `max` is recorded as noise-level evidence, not as a
  reason to weaken the baseline.
- Prompt version: `phase2-mini-v1` for the frozen system and repair prompts.
- Tool-use interface: the standard Anthropic `tool_use` interface, with **no
  server-side schema enforcement**. The harness validates each emitted call
  against the tool's `input_schema`; validation failures feed the schema-repair
  retry. Direct arms get no schema-repair retries.
- Tool schemas come from each case's row `tools` in the pinned dataset
  revision. There is no mock tool execution in this run.
- `max_tokens = 1024`.
- The judge model for the secondary multi-step question is `gpt-6-luna`. It is
  outside the headline claim and outside the pinned Haiku/Sonnet pricing file;
  its spend is covered by the later-phase allowance in section 10.

Pricing basis: `list_sync`, USD per MTok, from
`config/pricing.list_sync.2026-10-31.json` (verified 2026-10-31). Haiku has a
≤100K-prompt tier at $0.10 input / $0.50 output per MTok and an over-100K tier
at $0.50 / $2.50. Sonnet is $2.00 input / $10.00 output per MTok.

## 4. Test design

- **C = 8.** This is the largest value in the locked set {6, 7, 8}. The power
  rule no longer selects C, so I chose the largest set for interval precision.
- **Tools:** the first 8 tools in the frozen candidate order:
  1. `calculate_standard_deviation`
  2. `average`
  3. `get_ip_location`
  4. `find_pairs_with_sum`
  5. `find_majority_element`
  6. `chi_square_independence_test`
  7. `calculate_quarterly_dividend`
  8. `calculate_investment_return`
- **n_t = 117 cases per tool.** The same `n_t` applies to every tool. Total
  test cases: 8 × 117 = 936. With three arms at k = 1, that is 2,808 arm-case
  runs.
- **k = 1** repeat per arm per case.
- The eight tools are drawn from the frozen 12-candidate order, seed 42,
  sha256 `6b822299090bda4e6dfca52b6db426b7b2b4b5dba14662505962145415623991`.
  The frozen order is not re-rolled.
- Within each selected tool, test cases are a seeded uniform random sample of
  the eligible single-tool rows. The seed is 42 with the stage salt
  `42:test`; eligible cases are sorted by case ID before sampling. The test
  rows are materialized only after this file is tagged, and their checksums
  are recorded in `data/README.md`.
- No replacement is triggered at drafting: all eight tools clear the 100-case
  floor and the 117-case target. If a selected tool leaked or fell below the
  floor after a later filter, the next unused candidate in the frozen order
  would replace it. Distractor exposure never consumes a spare. If
  materialization changes any count, I will record the replacement and any
  change to `n_t` in this file before the tag; I will not lower `n_t` or swap
  a tool after seeing test outputs.

## 5. Usable-case check for the eight test tools

Counts are single-tool usable cases under the final filters: answer-tools only,
structural row filters, malformed-gold exclusion, single-tool only, and the
final description-aware required-arguments rule. I recomputed them from the
pinned revision with `scripts/select_candidates.py`; the candidate counts are
identical under the structural pool and after the required-arguments rule.

| # | Tool | Usable cases | n_t | Cases left unused |
|---:|---|---:|---:|---:|
| 1 | `calculate_standard_deviation` | 175 | 117 | 58 |
| 2 | `average` | **117** | 117 | **0** |
| 3 | `get_ip_location` | 127 | 117 | 10 |
| 4 | `find_pairs_with_sum` | 128 | 117 | 11 |
| 5 | `find_majority_element` | 124 | 117 | 7 |
| 6 | `chi_square_independence_test` | 176 | 117 | 59 |
| 7 | `calculate_quarterly_dividend` | 119 | 117 | 2 |
| 8 | `calculate_investment_return` | 235 | 117 | 118 |

The minimum is 117, at `average`. No selected tool falls below 117, so the
shortfall rule is not triggered. `average` sits exactly on the line and uses
all 117 of its usable cases; it has no spare case within the tool. All eight
tools clear the 100-case floor.

## 6. Scoring

`docs/MATCH_SPEC.md` **v1.1** is the sole authority on exact match and partial
credit. It is final for this revision and is not itself pre-registered.

- Primary metric: mean exact-match accuracy, with repeats averaged within a
  case before any paired difference.
- `k = 1`, so `pass@1` is the same as accuracy; it is reported as a secondary.
- Secondary diagnostics from MATCH_SPEC §7: partial credit, name-only
  accuracy, and the lenient-string variant. They never change the primary
  result.
- Format-failure rate per arm is reported alongside the score: no `tool_use`
  blocks, malformed blocks, and schema-validation failures after retries.
- The scorer must pass the MATCH_SPEC §9 pair tests before it scores the test
  rows.

## 7. Failure policy and pre-registered exclusions

The policy is symmetric across arms except for schema-repair retries, which
apply to `haiku-agent` only.

| Event | Treatment |
|---|---|
| API error (5xx), timeout | Infrastructure retry identically on every arm, cap 2. Then count as failure. |
| Refusal | Count as failure. |
| Text-only reply | `haiku-agent`: schema-validation failure, schema-repair retry. Direct arms: terminal 0. |
| Malformed output (unparseable tool call) | Count as failure. `haiku-agent` may repair if schema validation identifies it; direct arms get no repair. |
| `max_tokens` truncation | Count as failure. |
| Schema-validation failure after retries exhausted | Scores 0. |
| Schema-validation failure after retries exhausted, but the emitted call equals gold | Scores 0. Investigate the schema/gold inconsistency. |

- Infrastructure retries cap 2; schema-repair retries cap 2 for
  `haiku-agent` only. Direct arms get no schema-repair retries.
- The **`truncated`** taxonomy label describes the `max_tokens` truncation
  event: the response stopped at the output cap before the call set was
  complete. It is descriptive only and adds no score, count, exclusion, or
  failure-policy rule. On dev the label and the `max_tokens` row described the
  same events (1 / 1 / 2 for `haiku-agent` / `haiku-direct` / `sonnet-direct`),
  not additional failures.
- Default: everything above counts as a failure. There is no post-hoc
  reclassification and no arm-level exclusion.

Pre-registered dataset exclusions, applied before the split is materialized:

| Exclusion | Count on the pinned revision |
|---|---:|
| Malformed gold (missing/non-string name, non-object or string-encoded arguments) | 0 rows |
| Required-arguments rule (core-input omissions only) | 38 rows / 43 instances |
| Single-tool candidate rows removed by the required-arguments rule | 0 |
| Mixed candidate + non-candidate cases | 1,270 |
| Multi-tool rows spanning only candidate tools | 21 (not used in the per-tool analysis) |
| Multi-tool rows with no candidate tool | 19,157 (not used in the per-tool analysis) |

Malformed gold is excluded and counted, never scored 0. Distractor exposure —
a candidate tool present in a row's `tools` list but not in its gold
`answers` — is allowed, counted per candidate, and disclosed; it never
triggers replacement. The leak check is zero candidate answer appearances in
dev-eligible rows by construction.

Distractor exposure for the eight selected tools, in dev-eligible single-tool
rows (candidate present in `tools`, absent from `answers`):

| Tool | Distractor rows |
|---|---:|
| `calculate_standard_deviation` | 295 |
| `average` | 245 |
| `get_ip_location` | 265 |
| `find_pairs_with_sum` | 383 |
| `find_majority_element` | 231 |
| `chi_square_independence_test` | 250 |
| `calculate_quarterly_dividend` | 213 |
| `calculate_investment_return` | 240 |

## 8. Analysis plan and Stage 4 dry run

The analysis pipeline computes, from the same results records:

1. Per-tool mean exact-match accuracy for each arm and per-tool paired mean
   differences.
2. The clustered t-interval on the eight per-tool mean differences
   (`d̄ ± t(0.975, 7) · s_tool / √8`).
3. The case-level bootstrap interval as a sensitivity check (2,000 resamples,
   seed 42).
4. Per-tool cost ratios, the t-interval on per-tool log cost ratios, and the
   exponentiated cost-ratio interval; the aggregate ratio is reported
   separately.
5. The cost-parity rule: upper bound ≤ 0.10.
6. The secondary `haiku-direct` comparison with intervals only.
7. A results JSON carrying run ID, git commit, model IDs, effort values,
   prompt version, `max_tokens`, match-spec version, and a dev-vs-final label.

I ran the full pipeline on dev outputs in Stage 4, before this tag:
`results/phase3-dev-20261031` (30 dev tools × 12 cases × 3 arms, k = 1, seed
45). The report is `docs/PHASE3_DEV_ANALYSIS.md`. That run is dev/dry-run only;
its numbers are not test results. The held-out run happens once, after the tag.

## 9. Variance components and the basis for n_t

The dev variance components are from the same Stage 4 dry run. `m` is the
count of non-candidate dev tools with at least 10 usable cases in the actual
Phase 3 dev sample; every dev tool contributed 12 cases, so `m = 30`. `u` is
recomputed from that `m` with
`u = √((m−1) / χ²_{0.20, m−1})`; it is not inherited from another `m`.

| Component | Value |
|---|---:|
| `τ̂²` (full sample) | 0.0110690 |
| `τ̂²` (pessimistic, LOTO maximum) | **0.0114849** |
| `σ²_within` (pooled) | 0.0449495 |
| Maximum per-tool `σ²_within` | 0.3333333 |
| Observed LOTO SD | 0.121716 |
| LOTO SD, minimum | 0.093582 |
| LOTO SD, median / maximum | 0.123866 / 0.123866 |
| `m` | **30** |
| `u` | **1.135922** |
| `n_t` | **117** |
| `s_pess` | **0.123302** |

`n_t` uses the locked within-tool rule, applied per tool and then maximized:

```
n_t = max_t ceil(4 · σ²_within,t / τ̂²_pessimistic), floor 100
```

The maximum per-tool within-tool variance is 0.3333333; with the pessimistic
between-tool variance 0.0114849 this gives
`ceil(4 × 0.3333333 / 0.0114849) = ceil(116.09) = 117`. The pooled
`σ²_within` is 0.0449495 and is not the quantity that sets the maximum; using
the full-sample `τ̂²` instead would give an `n_t` of 121, which I record as a
sensitivity only. The pessimistic value 117 is the pre-registered `n_t`.

The Stage 4 power check is recorded here as the reason for the estimation-first
amendment, not as a test-time verdict. At a −5-point margin, Δ = 0, and
`s_pess = 0.123302`, the allowable `s_pess` is 0.035092 at C = 6, 0.039458 at
C = 7, and 0.043372 at C = 8; all three fail. Even C = 12 fails, and roughly
49–50 tools would be needed at this spread, outside the frozen 12-candidate
list. Growing `n_t` does not fix this because the between-tool component
dominates. I therefore dropped the binary non-inferiority framing and kept
`n_t = 117` for precision of the reported interval.

## 10. Budget check

The ledger through Phase 3 dev is **$2.458041**. Test spend and reserve are
recomputed at dev-measured list-price rates per case:

| Arm | Cost per case |
|---|---:|
| `haiku-agent` | $0.00016214 |
| `haiku-direct` | $0.00016127 |
| `sonnet-direct` | $0.00319034 |
| All three arms | $0.00351376 |

At C = 8, `n_t = 117`, k = 1:

- Test spend = 8 × 117 × $0.00351376 = **$3.288878**.
- Test reserve = one aborted-and-resumed run on the most expensive arm,
  `2 × 8 × 117 × $0.00319034` = **$5.972325**.
- Later-phase allowance = **$2.000000** for judge, multi-step traces, and CI;
  the optional Opus spot check is droppable. No later-phase spend has
  occurred.
- Ledger + allowance + reserve + test spend = **$13.719243**, leaving
  **$6.280757** of the $20 hard cap.

The inequality closes on actuals:

```text
20.00 − 2.458041 (ledger) − 2.000000 (later-phase allowance)
      − 5.972325 (reserve) = 9.569634 ≥ 3.288878 (test spend)  ✓
```

Per-arm test spend is $0.151766 for `haiku-agent`, $0.150949 for
`haiku-direct`, and $2.986162 for `sonnet-direct`, totaling $3.288878. Costs
come from logged API usage, never token estimates. The run aborts loudly at
its cost ceiling.

## 11. Run discipline and abort rule

- The held-out run starts only after this file is committed and tagged and
  the budget inequality above holds.
- **Resuming from a checkpoint is allowed. Restarting a run after seeing
  partial results is forbidden.**
- A cost-ceiling abort is checkpointed with `status: "aborted"`, not as a
  completed result. Its 0 appears only in the aborted run's own accounting. A
  resume re-attempts that case; the earlier 0 is never inherited.
- Aborted runs are recorded, not deleted.
- The test split is materialized once, after the tag, from the frozen seeds;
  its checksums go in `data/README.md`. No tuning happens on test data.

## 12. Version and amendments

This pre-registration cites MATCH_SPEC v1.1 and the 2026-10-31
estimation-first amendment in `docs/EVAL_DESIGN.md`. Before the tag, any
change to this file is an explicit amendment in this file. After the tag,
changes require a new version and do not apply retroactively to test results.
