# Eval Design

The eval plan for the Agent + Eval Harness. Locked by a grill session on
2026-10-23 (decisions recorded in `PROJECT_PLAN.md`). The code implements this
document, not the other way around. Anything that changes a locked decision
re-opens the grill for that branch.

## 0. Pre-registration — read first

Nothing is run on the held-out set until `docs/PRE_REGISTRATION.md` is
committed **and git-tagged**. "Dated and committed" must be verifiable; the tag
is the proof. Required contents (see section 13):

- the hypothesis, the non-inferiority margin, the assumed true gap
- cost definition (what counts, pricing basis)
- model IDs, effort values, prompt versions, tool schemas, `max_tokens`
- match-spec version
- k (repeats) per arm, with the budget arithmetic closed
- analysis plan: estimator, bootstrap resamples, cost-ratio definition
- which comparison is primary, which are secondary
- **abort rule:** resuming from a checkpoint is allowed; restarting after
  seeing partial results is forbidden
- **analysis pipeline dry run:** the full pipeline (estimator, both intervals,
  cost ratio, results-JSON writer) runs on dev outputs and produces a dev
  RESULTS draft **before** the tag — the held-out run happens once

## 1. The claim

**Non-inferiority on cost parity.** The Haiku agent loop matches a
Sonnet-5.5 direct-prompt baseline on single-turn tool-calling accuracy, at no
more than one-tenth the token cost.

Formally: let `d_t` = mean paired difference (`haiku-agent` −
`sonnet-direct`) in held-out tool `t`, averaged over `n_t` cases with
the **same `n_t` in every tool** (repeats averaged within a case first).
The estimand is the **equal-weighted average over held-out tools**,
`d̄ = (1/C)·Σ_t d_t` — which, with equal `n_t`, coincides with the pooled
per-case mean. Non-inferiority is declared if the lower bound of the 95%
t-interval `d̄ ± t(0.975, C−1)·s_tool/√C` stays above **−5 points**
(section 7), and the cost ratio (Haiku-agent list-price token cost per task
÷ Sonnet-direct list-price token cost per task) clears its pre-registered
rule: the **upper bound of the 95% t-interval on per-tool log cost
ratios ≤ 0.10** (exponentiated back to ratio scale), interval reported
alongside, with a case-level bootstrap as sensitivity check. (A tool
bootstrap is avoided here for the same few-clusters reason as in §7.)

Language rule: Sonnet 5.5 is "a mid-tier model, ~20x pricier per token." It is
never called "frontier" in the README, the resume, or the report. Anthropic has
tiers above it; the claim must not overstate the baseline.

## 2. The arms

| Arm | Model | What it does |
|---|---|---|
| `sonnet-direct` | Sonnet 5.5 (pinned ID) | Single direct prompt, no loop. The comparison arm. |
| `haiku-direct` | Haiku 5.5 (pinned ID) | Single direct prompt, no loop. Isolates whether parity comes from Haiku being good at the task vs from the loop. |
| `haiku-agent` | Haiku 5.5 (pinned ID) | The agent loop (section 3). |

The router arm was cut from v1 in the 2026-10-23 review: on xLAM the only
non-ground-truth gate is schema validation, which makes a router nearly
identical to `haiku-agent` with a fallback. It remains a v2 candidate.

- **Model IDs are pinned at first use and recorded in every results file.**
  Starting points (verify, then pin): `claude-haiku-5-5`, `claude-sonnet-5-5`,
  judge `gpt-6-luna` (ID as listed on OpenAI's pricing page 2026-10-23; pricing
  $0.10/$0.50 per MTok verified same day).
- **Effort is fixed per arm and recorded.** Allowed values run low to max.
  The selection rule is pre-specified — choosing after seeing dev slices is
  fine; the rule itself is not:
  - `sonnet-direct`: the API default effort, or its strongest dev-slice
    configuration — whichever scores higher on the slice. The baseline is
    never weakened to make non-inferiority easier.
  - Haiku arms (one effort for both, so the loop comparison holds effort
    fixed): the lowest effort whose dev-slice accuracy is within a
    pre-registered tolerance (e.g. 2 points) of Haiku's best effort on the
    slice. Lower effort protects the cost claim; the tolerance protects
    accuracy.
  - The rule and tolerance are recorded in pre-registration. Verify logged
    tokens include thinking tokens — effort spends through them.
- **Primary comparison:** `haiku-agent` vs `sonnet-direct`. Everything else is
  secondary: reported with intervals, **no pass/fail framing** (this avoids
  multiple-comparison fishing).
- **Tool-use interface (pinned):** the standard `tool_use` interface, with
  **no server-side schema enforcement** — the harness validates each emitted
  call against the tool's `input_schema` itself, and validation failures feed
  the repair retry (section 3). If the API enforced schemas, the loop would
  have nothing to repair.
- **Pre-registered expectation:** `haiku-agent` and `haiku-direct` may differ
  little — most single-turn calls are well-formed on the first try. The
  **retry rate is a reported metric**: a near-zero retry rate makes the loop
  comparison vacuous, which is itself a finding, not a failure.
- Optional, budget permitting: a small Opus spot-check (~50 cases) to show
  whether Sonnet is near the ceiling.

## 3. What the agent loop does on single-turn data

This was the biggest hole in the original plan, and it is now closed by
definition. xLAM rows carry no executable tools, so a naive "loop" would be
identical to direct prompting and the `haiku-direct` comparison would be
vacuous. The loop's added value on xLAM is **mechanical repair**:

1. Generate the tool call(s) for the query.
2. Validate every call against the tool's JSON schema (types, required fields,
   enums).
3. On validation failure, feed the validation error back to the model and
   retry — capped at 2 retries, then the last attempt stands.
4. Score what came out. Done.

**Hard constraint:** the loop NEVER consults ground truth or the exact-match
scorer to decide whether to retry. The retry signal is schema validation only —
feedback the agent would actually have in deployment. (The old router lab's rule
"the gate equals the final criterion" is the opposite situation; it does not
carry over here.)

Out of scope for v1: mock tool execution. 3,673 synthetic APIs cannot be
faithfully mocked, and an LLM-as-mock would be circular. The rich loop
behaviors (chaining, context use, recovery from observations) are exercised on
the hand-written multi-step set — the secondary question (section 9).

## 4. Failure policy — symmetric across arms, decided in advance

Two retry kinds are distinct and must not be conflated:

- **Infrastructure retries** (API 5xx, timeout) apply identically to every
  arm, cap 2. After the cap the event counts as a failure.
- **Schema-repair retries** apply to `haiku-agent` only, cap 2. The retry
  signal is schema validation of the emitted call — never gold and never the
  scorer (section 3). Direct arms get no schema-repair retries.

| Event | Treatment |
|---|---|
| API error (5xx), timeout | Infrastructure retry identically on every arm, cap 2. Then count as failure. |
| Refusal | Count as failure. |
| Text-only reply | `haiku-agent`: schema-validation failure, schema-repair retry. Direct arms: terminal 0. |
| Malformed output (unparseable tool call) | Count as failure. `haiku-agent` may repair if schema validation identifies it; direct arms get no repair. |
| `max_tokens` truncation | Count as failure. |
| Schema-validation failure after retries exhausted | Scores 0. |
| Schema-validation failure after retries exhausted, but the emitted call equals gold | Scores 0. This combination signals a schema/gold inconsistency and must be investigated. |

Default: everything above counts as a failure. **Any exclusion must be
pre-registered** (section 13). No post-hoc reclassification. The policy table
is drafted against observed dev failures in Phase 3 and **frozen in
pre-registration** — dev failures may be viewed freely once the Phase-0
held-out tool list is frozen (section 5).

**Precedence rule.** A schema-validation failure after retries are exhausted
scores 0 even if the emitted call happens to equal gold. Scoring it as a win
would hide a contradiction between the schema and the gold label; such a case
must be investigated instead. The **format-failure rate per arm** (no
`tool_use` blocks, malformed blocks, schema-validation failures after
retries) is reported as a diagnostic **alongside** that score, not instead of
it.

## 5. Benchmark, splits, and filters

- **Dataset:** `Salesforce/xlam-function-calling-60k`. **Pin the exact revision
  at download** (reference: `26d14ebfe18b1f7b524bd39b404b50af5dc97866`,
  2025-01-24 — the July 2024 regeneration means bare "xLAM-60k" is not a fixed
  artifact). Record the pinned hash in `data/README.md`.
- **License/redistribution:** public card read 2026-10-26: Hub tag
  `cc-by-4.0`, gated click-through, APIGen citation requested, and a
  "research purposes only" note. The drafted verdict and open questions are in
  `data/README.md`. These terms are **not yet confirmed for republication**;
  until the owner confirms the accepted click-through terms, no raw dataset
  row, query, gold answer, full tool schema, or reproducing trace is published.
  Aggregates, counts, tool names, and hand-written tasks are safe.
- **Cluster and split unit is the tool, not the category (pivot
  2026-10-25).** xLAM rows carry only id/query/answers/tools; the "21
  categories" are the paper's API taxonomy, not a split unit. The held-out
  unit is a **tool name**. A case is a **single-tool case** when every gold
  answer calls the same tool; only single-tool cases enter the per-tool
  analysis. A case whose gold calls span more than one tool is not assigned
  to a held-out tool.
- **Invalid-row filters — defined before the split freezes, applied before
  it:** answer-tools only (eligibility is defined by gold `answers`, never by
  distractor `tools`); a gold function name not present in the row's `tools`
  is excluded; an empty `answers` where a call is expected is excluded; a
  malformed gold call is excluded and counted (below); a gold call that omits
  a **core input** is excluded under the **final** required-arguments rule
  (below).
- **Malformed-gold exclusion — FINAL (owner, 2026-10-26).** A row is excluded
  when a gold call has a missing or non-string tool name, non-object
  arguments, or string-encoded arguments. The row is excluded **before** the
  split, its count is reported, and it is a pre-registered exclusion — **not**
  a score of 0. The pinned revision has **zero** such rows (defensive only):
  all 100,011 gold calls carry a string name and a JSON object in
  `arguments`.
- **Required-arguments filter — FINAL: description-aware refinement (owner,
  2026-10-26).** A gold call is excluded only when it omits a **core input**.
  A missing parameter is not core when either condition holds, with the name
  check taking precedence:
  1. its name matches
     `(?:^|_)(?:filter|filters|limit|offset|page|per_page|sort|sort_by|order|order_by|descending|ascending|skip|cursor|top)(?:$|_)`
     (`re.IGNORECASE`); or
  2. its description matches
     `\boptional\b|\bdefaults?\b|\bdefault\s+(?:is|to|values?)\b|\bif\s+(?:provided|specified|given|omitted|not\s+provided|none)\b|\bnot\s+required\b|\bcan\s+be\b`
     (`re.IGNORECASE`).

  Otherwise the parameter is a core input and the row is excluded. A
  parameter with a literal `default` key is optional before this rule runs.
  The rule is implemented once in `scripts/select_candidates.py`
  (`categorize_parameter` / `normalize_parameters`) and imported by
  `scripts/audit_required_filter.py`, so the audit and verifier cannot drift.
  On the pinned revision it excludes **38 rows / 43 missing-parameter
  instances** and yields a **70-tool** eligible pool. The pinned revision has
  168,870 flat tool declarations and zero nested declarations; the public card
  describes a `required` boolean, but the pinned revision has zero `required`
  keys in its 362,482 parameter descriptors. That card-vs-data discrepancy is
  preserved in `docs/REQUIRED_FILTER_AUDIT.md`. Candidate counts are
  unchanged: the final rule removes zero single-tool candidate rows. The
  frozen order is generated from the 71-tool structural pool and is not
  re-rolled; the 70 vs 71 difference is `integrate` falling 101 → 95 usable
  cases, and `integrate` is not a candidate. The interim "no `default` key =
  required" heuristic (1,316 rows, 63 tools) is history only.
- **Mixed cases are excluded and counted.** A case whose gold calls include
  both a candidate held-out tool and a non-candidate tool is excluded from
  the candidate counts; on the pinned revision that is 1,270 cases. A case
  whose gold calls span more than one candidate tool (21 cases) is also not
  a single-tool case and is not used in the v1 per-tool analysis.
- **Leak, operationally (pivot):** a leak is a **held-out candidate tool
  appearing as a gold/answer tool in dev**. Distractor appearances — a
  candidate tool present in a row's `tools` list but not in its `answers` —
  are **allowed, counted per candidate, and reported**. They do **not**
  trigger replacement. The Phase-0 verifier reports distractor exposure for
  every candidate. On the pinned revision the leak check is zero candidate
  answer appearances in dev-eligible rows by construction, because dev is
  defined by answer tools.
- **Replacement rule for 12 candidates (not 8).** Phase 0 freezes an ordered
  list of **12 candidate held-out tools** (seed 42, sha256-hashed, committed
  in `docs/CANDIDATES.md`). The selected set is the first C candidates in
  that order, with C ∈ {6, 7, 8} per §7. If a selected candidate leaks (a
  gold answer in dev) or falls below the 100-case floor after a later filter,
  it is replaced by the next unused candidate in the frozen order. With 12
  candidates and C = 8 there are 4 spares; C = 7 leaves 5; C = 6 leaves 6.
  If the spares are exhausted below C, invoke the §7 fallback selection.
  Distractor exposure never consumes a spare.
- **Freeze order (corrected 2026-10-25):** Phase 0 freezes the ordered list
  of **12 candidate held-out tools**. Dev work — including viewing dev
  failures — is free afterward. Phase 4 materializes the test rows
  deterministically from the Phase-0 seeds, verifies the sizing target
  against post-filter usable rows, and records checksums. The failure policy
  freezes in pre-registration, not before dev.
- **Usable rows are counted before the tag.** Filters depend only on dataset
  structure, not model outputs, so Phase 0 computes usable-row counts per
  candidate tool. Pre-registration sets `n_t` at or below the smallest
  available count across the selected tools. If the sizing target needs more
  than the minimum available, pre-reg either raises C or recomputes the
  power check at the achievable `n_t` and picks a §7 fallback if it fails.
- **Within-tool sampling:** seeded uniform random; seeds committed in Phase
  0. The split must yield enough usable test rows per tool to meet the sizing
  target (section 7) *after* filtering.
- **Mini-set:** ~20 hand-written tasks. Pipeline smoke test and the seed of the
  multi-step set. **Excluded from every reported headline number.**
- **Multi-step set:** ~30 hand-written tasks, each shipping with deterministic
  mock tool implementations authored alongside it. This is the secondary
  question's data and the judge's only territory (section 9).

## 6. Scoring — MATCH_SPEC is the sole authority

**`docs/MATCH_SPEC.md` is the sole authority on exact match and partial
credit. This section defines only the metric and the required tests; it does
not restate or override a scoring rule.** If this section and the match spec
ever disagree, the match spec governs.

- Primary metric: **mean exact-match accuracy**, repeats averaged within a
  case first (section 1).
- Partial credit (secondary), name-only accuracy, and the lenient-string
  variant are defined in MATCH_SPEC §7. No other scoring rule is defined
  here.
- `pass@k` comes from the pre-registered repeats and is reported separately
  as a secondary.
- The scorer is **tested against hand-made right and wrong pairs** covering
  every MATCH_SPEC rule — including adversarial near-misses — before it
  scores anything real. These pair tests are required CI from day one
  (MATCH_SPEC §9).

## 7. Sizing — power under clustering by held-out tool

The final interval clusters by **held-out tool**, so sizing is about
**tools**, not cases. (The 2026-10-23 drafts corrected interval-width sizing
and the case-level independence assumption; the 2026-10-25 pivot replaces
"category" with "tool" and re-derives every quantity below rather than
renaming it.)

- Let `d_t` = mean paired difference (agent − sonnet) in held-out tool `t`,
  averaged over cases with repeats averaged within a case first.
- **Primary interval:** t-interval on the `d_t`:
  `d̄ ± t(0.975, C−1) · s_tool / √C`, where `s_tool` = sample SD of the `d_t`
  and `C` = number of held-out tools. A case-level (unclustered) bootstrap CI
  is reported alongside as a sensitivity check; if they disagree
  substantially, say so — the clustered interval governs.
- Non-inferiority: the t-interval's **lower bound above −δ** (δ = 0.05).
- **Sign convention (locked 2026-10-23):** Δ = assumed true mean difference,
  **signed** (agent − sonnet). Δ = 0 is the default; Δ = −0.02 means "we
  assume the agent is 2 points worse." The pre-registration states Δ and
  this convention in words — never as a bare number.
- ~80% power needs

  `(t(0.975, C−1) + t(0.80, C−1)) · s_tool / √C < δ + Δ`

  with Δ signed. Worked meaning: at fixed C, a 2-point assumed shortfall
  (Δ = −0.02) demands (0.05/0.03)^2 ≈ 2.8× the effective sample size —
  equivalently, s_tool must be ~0.6× as large, so the C = 8 bar of 4.3
  points drops to ~2.6 points. That is demanding: run the feasibility check
  against the leave-one-tool-out (LOTO) estimate early (Phase 3), before
  pre-registration. (The previous draft wrote δ + Δ while describing Δ as a
  positive gap — that sign error would have *halved* n exactly when it
  matters most.)
- **If Δ ≤ −δ, no n establishes non-inferiority.** The pre-registration then
  chooses, before any test data: a wider margin justified a priori, lower
  power stated openly, or the binary framing dropped in favor of reporting
  the interval. Never chosen after seeing test results — choosing on dev
  LOTO evidence is intended and fine.
- `s_tool` is estimated by **leave-one-tool-out on dev**: per-tool mean
  differences across dev tools, SD across them. **Size to the pessimistic
  end** of that spread — dev tools differ from test tools, so dev's variance
  may not carry over.
- **Variance components (re-derived 2026-10-25).** The LOTO spread mixes two
  things: true between-tool variance τ² and within-tool sampling noise at
  dev's per-tool n. Separate them:
  - `τ̂² = max(0, observed_LOTO_var − mean_t(σ²_within,t / n_dev,t))`,
    where σ²_within,t is the within-tool variance of per-case paired
    differences (repeats averaged first).
  - Predicted test-time spread: `s_pred² = τ̂² + σ²_within / n_t`.
  - The pessimistic uplift applies to the **τ̂ component only** (the part
    estimated from m dev tools; the within-tool part rests on the eligible
    tools' hundreds of cases): `s_pess² = (u·τ̂)² + σ²_within/n_t`,
    `u = √((m−1)/χ²_{0.20, m−1})`, **m = number of dev tools in the LOTO
    estimate**. χ²_{0.20, m−1} denotes the lower-20% quantile
    (P(X ≤ x) = 0.20), so this is an 80% upper confidence limit.
    **m is no longer 13.** **Final rule (decision 2, owner 2026-10-26):** `m`
    is the count of dev tools (non-candidate tools) with **≥10 usable cases in
    the actual Phase 3 dev sample**, and `u` is recomputed from that `m` with
    the formula above. `m`, `u`, `C`, and `n_t` are **not locked by this
    document**. The structural illustration `m = 1,093`, `u = 1.019` in the
    table below is the full-dataset count at the ≥10 threshold — an
    illustration only, not a Phase-3 value. `u` is never inherited from a
    different `m`. The old `u ≈ 1.240` at `m = 13` must not be carried over.
  - The power inequality uses s_pess. (Plugging the mixed LOTO SD straight
    in inflates s under the n_t rule below — small, but the components
    version is the right derivation.)
  - If τ̂² ≈ 0, use the 100-case floor for n_t and note the interval is
    within-noise-driven.
- **Usable-cases rule for `m` — FINAL rule, numbers pending Phase 3
  (owner, 2026-10-26).** Counts below are from non-candidate dev tools only
  and come from the full pinned revision, **not** from the Phase 3 dev
  sample. "Structural basis" counts single-tool cases after filters 1–3;
  "all-filter basis" applies the final description-aware required-arguments
  rule too. The table is computed by `scripts/audit_variance_m.py`. **The
  `≥10` row is the chosen rule; every other row is sensitivity only.** In
  Phase 3, `m` is counted on the dev sample actually used (not on the full
  dataset), and `u` is recomputed from that `m`.

  | Threshold: usable single-tool cases per dev tool | m (structural) | u | m (all filters) | u |
  |---:|---:|---:|---:|---:|
  | exactly 1 | 340 (excluded) | — | 340 (excluded) | — |
  | ≥2 (sensitivity only) | 2,508 | 1.012 | 2,508 | 1.012 |
  | ≥5 (sensitivity only) | 1,835 | 1.014 | 1,835 | 1.014 |
  | **≥10 (CHOSEN RULE; Phase-3 m recomputed)** | **1,093 (illustration)** | **1.019 (illustration)** | **1,093 (illustration)** | **1.019 (illustration)** |
  | ≥20 (sensitivity only) | 420 | 1.031 | 420 | 1.031 |
  | ≥50 (sensitivity only) | 133 | 1.057 | 133 | 1.057 |
  | ≥100 (sensitivity only) | 59 | 1.090 | 58 | 1.091 |

  All 2,848 non-candidate dev tools have at least one usable case; **340 have
  exactly one**, cannot estimate within-tool variance, and are excluded from
  `m`. The `≥10` row is the **rule**; its printed `1,093` / `1.019` are the
  full-dataset structural illustration, **not locked values**. The numbers in
  the other rows are sensitivity only. `u` falls monotonically as `m` rises:
  a stricter threshold yields a smaller `m` and a larger `u` (the all-filter
  ≥100 row is 58 / 1.091). `u` enters §7 only through
  `s_pess² = (u·τ̂)² + σ²_within/n_t`. The within-tool rule
  `n_t ≥ 4σ²_within/τ̂²` is independent of `u`; the power inequality is not,
  so a larger `u` can only push the smallest passing C upward. Mapping a
  specific `u` to a concrete C or `n_t` needs the Phase-3 `τ̂` and
  `σ²_within`, which do not exist yet, so C and `n_t` stay for
  pre-registration; this table does not choose them.
- **C-selection rule (pre-registered, no discretion):** the smallest C in
  {6, 7, 8} that (a) passes the power inequality at the pessimistic s_pess
  and (b) fits the budget ledger. C is a number of **held-out tools** drawn
  from the frozen ordered **12-candidate** list; the selected tools are the
  first C non-leaking, floor-clearing candidates in that order, with the §5
  replacement rule. A larger C always helps the interval, so the trade-off
  is only dev tools and cost. If even C = 8 fails (a), pre-registration
  names one §7 fallback (wider margin / lower power / drop binary framing),
  chosen on dev evidence. Worked check: at C = 6 the t-quantiles sum to
  2.571 + 0.920 = 3.491, so 80% power at Δ = 0 needs s_pess < 3.5 points; at
  C = 8 the bar is s_pess < 4.3 points. The numeric C and `n_t` are **not
  fixed here**: they are selected in pre-registration after the owner picks
  the usable-cases threshold above.
- **One `n_t` for every held-out tool** — the max of the per-tool
  requirements, floor 100 usable cases — so the pooled per-case mean
  coincides with §1's equal-weighted estimand. Per-tool requirement:
  within-tool SE ≤ τ̂/2 (i.e. `n_t ≥ 4 · σ²_within / τ̂²`), using the
  between-tool component, not the mixed spread. Adding cases within a tool
  does not narrow the clustered interval — only more tools (or less
  between-tool spread) do.
- **Dev variance is measured at the final k.** Averaging repeats changes
  per-case variance, so sizing from a different k would mislead.
- **Report the interval and what it excludes**, not just a binary pass/fail.

## 8. Repeats and the budget

- **Pilot first — Phase 3 opens with this.** ~30 cases per arm at k = 1
  (pilot ceiling $1, pure measurement, no pre-registration needed), drawn
  only from non-candidate tools — the 12 frozen candidates are held out from
  all dev work. Measure real tokens and cost per case per arm, failure
  modes, retry rate. From the pilot: choose k, then define the dev sample
  (seeded, stratified by tool, fixed size, non-candidate tools only) — each
  with its own spend ceiling. The ledger records dev spend after the fact,
  so it cannot prevent an overrun; the **ceilings** prevent it.
- **Cost feasibility gate, right after the pilot.** Compute the pilot cost
  ratio (haiku-agent ÷ sonnet-direct, list price). If it already exceeds
  0.10 at the pilot efforts, the cost claim fails regardless of accuracy —
  redesign (effort, loop budget) or drop the cost claim **before** spending
  the dev budget.
- `k` (repeats per arm) is chosen after the pilot and frozen for the dev
  run, then **pre-registered** unchanged — unless new dev evidence forces a
  change, in which case the variance inputs are re-measured at the new k.
  Token counts come from **real dev measurement** (xLAM prompts may be far
  smaller than 5k tokens — do not size the budget on guesses). The test-spend
  term is `sum_over_arms(C * n_t * k_arm * measured_cost_per_case)`.
- If it doesn't close, pick **one** of these in pre-registration — never after
  seeing results: a wider margin justified a priori, lower power stated openly,
  or a higher cap.
- **$20 hard cap.** Per-arm budget lines set after dev measurement.
- **One ledger across all phases.** The pre-registration shows the inequality
  closed on actuals:

  `20.00 − pilot − effort_slices − dev_spend − judge_spend − multistep_traces
  − opus_spot − ci_spend − reserve ≥ test_spend`

  where `reserve` = the cost of one full aborted-and-resumed test run on the
  most expensive arm. The ledger is updated with actuals after every phase;
  the test run does not start until the inequality holds.
- Cost is computed from **logged API usage, never token estimates**. The
  harness aborts loudly at the ceiling, checkpoints so an aborted run isn't
  wasted, and aborted runs are **recorded, not deleted**.
- **One pricing basis for every arm:** cost ratios from logged tokens at **list
  price**; actual spend reported separately. (Batch is discounted — mixing
  batch spend for Sonnet with list spend for Haiku would distort the 1/10
  comparison.)
- Batch API: **direct-prompt passes only** (the agent loop is sequential and
  can't batch). Batch runs carry no meaningful latency — **latency numbers come
  from synchronous runs only**.
- Second line of defense: a provider-console spend limit, if available.

## 9. The judge — secondary question only

The judge serves the multi-step set, **not the headline claim**. A bad κ cannot
sink the cost-parity result — that is what makes the time-box safe.

- Model: GPT-6 Luna — **different family from the agent** (non-negotiable).
- ~60 hand labels, split **by task: ~15 tune tasks / ~15 report tasks**
  (~2 traces each). Splitting by trace would let the same task land in both
  sets. The report set stays untouched until final scoring. "One calibration
  round" = one judge-prompt revision, defined up front — tuning against the
  report set inflates κ.
- **Trace generation is a phase step, not an afterthought:** the loop runs
  with **both models** on the multi-step set (`haiku-agent`, `sonnet-agent`)
  — same loop code, different model. A direct (no-loop) arm cannot chain
  calls it never sees results for; it would fail by construction and tell
  nothing, so it does not run here. Batch is unusable for loop runs
  (sequential calls). The label sample is drawn stratified across the two
  loop arms and repeats so no task is over-represented.
- **Written rubric before labeling; blind** (arm identity hidden). ~15 traces
  from the report set relabeled a week later: our own consistency is the
  realistic ceiling for judge agreement, and it's cheap to measure.
- Report **κ with its 95% CI**. With ~30 report labels the interval is wide
  — roughly ±0.25 or more around the point estimate — so the threshold is a
  decision rule, not proof. Watch class balance: if 90% of traces pass, κ
  is unstable.
- **Mind clustering:** 60 traces from ~30 tasks are not 60 independent
  samples. Say in the writeup that the effective sample is smaller.
- **Ship rule: point estimate ≥ 0.70, CI reported alongside.** The resume
  bullet states the measured κ and n **whether or not it clears 0.70** — with
  a ~30-label report set the interval is wide either way, and the number must
  not imply a calibrated judge when it isn't one.

## 10. CI gate

**Required from day one:** lint, scorer unit tests (including the hand-made
right/wrong pairs), and all other offline checks.

**The LLM-backed gate starts advisory** and is promoted to required only after
its false-alarm rate is measured:

- Runs a small pinned dev subset (~50 cases) on the Haiku arms only, per PR.
- Fails only when exact-match drops more than a **tolerance band measured from
  repeated runs on unchanged code** (temperature is not adjustable on these
  models, so empirical noise measurement is the only option).
- **Rerun once on failure; fail only if both runs fall below the band.**
- Pin everything: model ID, effort, prompt version, tool schemas. Reference
  score stored in the repo, changed only deliberately.
- **Honest sensitivity, documented:** on 50 cases one case is 2 points. This
  gate is a smoke detector for large regressions; it will miss a 3-point drop.
  Gate runs are **never reported as experimental results**.
- Keep the gate subset **separate from the final test split** (drawn from dev;
  expect to overfit it under heavy iteration).
- **Protect the key:** dedicated CI key with its own low spend limit; untrusted
  PRs cannot read repository secrets. The harness per-PR cost ceiling is a
  second line of defense, not the only one.
- The Sonnet arm stays on **manual milestone runs**. (~$0.05 per 50-case Haiku
  gate run: negligible.)

## 11. Reporting

- `docs/RESULTS.md` is the record. The README summary table is **generated
  from the same results JSON** — the two cannot drift.
- **"Negative result" is defined up front:** the primary comparison missing
  the non-inferiority margin, a κ miss, a budget abort — reported with the
  same prominence as the wins. The margin applies to the primary comparison
  only; secondaries get intervals, never pass/fail.
- **Limitations section, mandatory:** held-out xLAM tools test shift *within
  one synthetic generator*. That is not real-world generalization; say so.
- v2 candidates: BFCL as an external check, a larger multi-step set.

## 12. Dashboard

Static Next.js export on Vercel. **The frontend only renders; the harness
computes.** CIs, p95s, costs, taxonomy counts are calculated once in Python and
written to the committed results JSON, which is a **versioned contract**:
every file carries run ID, git commit, model IDs, effort settings, prompt
versions, and dev-vs-final labeling. Never mix dev and final numbers on one
chart unlabeled.

- Build **late, time-boxed**, after real dev results exist — the UI is designed
  around actual data shapes, not stubs. The dashboard is not the contribution;
  the experiment is.
- Traces: sampled subset only, scrubbed, and only after the license/terms
  verification in section 5 clears. Negative results and the dev-vs-final
  distinction get the same prominence as the wins.
- Stretch goal is **replay, not live**: stream a pre-recorded trace with
  simulated pacing. Costs nothing, needs no backend, labeled honestly as
  "recorded run."

## 13. Pre-registration contents

`docs/PRE_REGISTRATION.md`, committed and git-tagged before the test split is
touched:

1. Hypothesis, margin (δ = 0.05), assumed true gap Δ **with the sign
   convention stated in words** (Δ signed, agent − sonnet; Δ = −0.02 means
   "we assume the agent is 2 points worse")
2. Cost definition: what counts (every agent API call incl. retries), pricing
   basis (list), spend reported separately
3. Model IDs (all arms + judge), effort values **plus the pre-specified
   selection rule and tolerance** (section 2), prompt versions, tool schemas,
   `max_tokens`, **tool-use interface pin** (no server-side schema
   enforcement; harness validates)
4. Match-spec version (v1.1, the final version in `docs/MATCH_SPEC.md`)
5. k per arm + the ledger inequality closed on actuals
   (`20.00 − pilot − effort_slices − dev − judge − multistep_traces
   − opus_spot − CI − reserve ≥ test`), or the chosen fallback:
   wider margin justified a priori / lower power stated openly / higher cap
6. Analysis plan: t-interval estimator on per-tool mean differences,
   case-level bootstrap as sensitivity check, cost-ratio definition (upper
   bound of the 95% t-interval on per-tool log cost ratios ≤ 0.10), sizing
   inputs (variance components: τ̂², σ²_within, leave-one-tool-out spread
   with the pessimistic upper-limit rule; `m` counted from the actual Phase 3
   dev sample as non-candidate dev tools with ≥10 usable cases; the resulting
   `u`; C-selection rule outcome; Δ with sign convention) and the resulting
   C and n_t
7. Primary comparison (`haiku-agent` vs `sonnet-direct`); secondary
   (`haiku-direct`) with intervals only, no pass/fail
8. Failure-policy table (section 4) and any pre-registered exclusions,
   including the final required-arguments rule and the malformed-gold
   exclusion with its counted row total
9. Sampling: the 12 ordered candidate held-out tools (from Phase 0 in
   `docs/CANDIDATES.md`), usable-row counts per candidate, distractor
   exposure per candidate, the leak-check result, the selected C with
   reason, within-tool seeds, and the final filter definitions

## 14. What "done" looks like

- Pre-registration tagged before the test rows are materialized (candidate
  tools frozen since Phase 0).
- Analysis pipeline validated end-to-end on dev outputs before the tag.
- Agent runs end-to-end on dev, test, and multi-step sets from one command.
- Results: the paired interval, the cost ratio, CIs attached, negative results
  reported at equal prominence.
- Judge: κ with CI reported; ship rule applied as defined, miss handled as
  defined.
- CI gate green on main; a deliberately broken prompt change fails the gate
  (test the test) — after the advisory period.
- Dashboard live, rendering the versioned results JSON.
- The one before→after pair the resume bullet claims, measured, not estimated.
