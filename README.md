# Agent + Eval Harness

A tool-using agent built on the **raw Anthropic API** (no agent framework), scored on a
**public agent-benchmark subset**, with a **calibrated LLM judge** and a **CI gate** that
fails when a prompt change hurts the score.

## Why this exists

Shipping an agent is easy. Proving it works is the hard part — and it's the part most
portfolios skip. This project proves it:

1. **Raw-API agent** — tool-use loop, schema-validation retries, budgets. No LangChain/LangGraph magic.
2. **Honest benchmark** — public tool-calling dataset (xLAM-60k, revision pinned),
   held-out tools (the cluster and split unit; the paper's "categories" are
   descriptive only), and a Sonnet-5.5 direct-prompt comparison arm (a
   mid-tier model ~20x pricier per token — never called "frontier").
   The locked claim is non-inferiority on accuracy at ≤ 1/10th the token cost.
3. **Calibrated judge, scoped** — an LLM judge from a **different model family**
   (GPT-6 Luna) serves the secondary multi-step question only: blind labels,
   a tune/report split, κ with a confidence interval. A miss is a published
   negative result.
4. **CI gate** — offline checks required from day one; the LLM-backed gate
   starts advisory and is promoted once its false-alarm rate is measured.

## Status: Phase 2 mini-set smoke complete

The mini-set, tool schemas, agent loop, direct arms, and harness are
implemented and pass offline tests. The three-arm live smoke run completed on
the 20-case mini-set at a total list-price cost of **$0.073719**; those numbers
are `mini/smoke` only and are excluded from every headline result. Model IDs
are verified/pinned in `src/eval/config.py` and list prices are pinned in
`config/pricing.list_sync.2026-10-31.json`. Phase 3 has not started.

## Layout

```
agent-eval-harness/
├── src/agent/        # raw-API tool-using agent
│   ├── agent.py      # loop: prompt -> tool_use -> schema validation -> repair
│   ├── prompts.py    # frozen Phase-2 system + repair prompts
│   └── tools.py      # mini-set JSON schemas (no tool execution in Phase 2)
├── src/eval/         # evaluation harness
│   ├── harness.py    # run loop: cases -> arm -> trace -> score -> results
│   ├── mini_set.py   # mini-set loader + schema/scorer validation
│   ├── config.py     # explicit model/effort/prompt configuration
│   ├── scorer.py     # schema-free per-case matcher (MATCH_SPEC v1.1)
│   ├── metrics.py    # cost from logged usage, CIs, latency
│   ├── judge.py      # LLM-as-judge (different model family from the agent)
│   └── calibration.py# judge calibration vs human labels (Cohen's kappa)
├── tests/            # pytest: scorer pairs, agent loop, harness, mini-set
├── scripts/
│   ├── select_candidates.py  # reproducible Phase-0 candidate verifier
│   ├── audit_required_filter.py  # final required-args rule audit
│   └── audit_variance_m.py   # §7 m/u usable-cases sensitivity
├── data/
│   ├── miniset.jsonl # tracked hand-written Phase-2 smoke set (20 cases)
│   └── README.md     # revision pin, license draft, split notes
├── PROJECT_PLAN.md   # phase-by-phase build sequence
├── docs/
│   ├── EVAL_DESIGN.md      # the full eval plan — read this first
│   ├── MATCH_SPEC.md       # sole authority on exact/partial scoring
│   ├── CANDIDATES.md       # frozen held-out-tool candidates (seed + sha256)
│   ├── REQUIRED_FILTER_AUDIT.md  # required-args audit for owner sign-off
│   ├── BUDGET.md           # $20 ledger and ceilings
│   └── FAILURE_TAXONOMY.md # descriptive diagnostics, not an exclusion list
└── .github/workflows/ci.yml  # offline tests; LLM eval gate added in Phase 7
```

## Quickstart (Phase 2)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Validate the mini-set offline: every gold call is offered, schema-valid,
# and accepted by the Phase-1 scorer.
PYTHONPATH=src python -m eval.mini_set --path data/miniset.jsonl

# Show the run plan without making any API call.
PYTHONPATH=src python -m eval.harness --subset data/miniset.jsonl --dry-run

# Live run:
PYTHONPATH=src python -m eval.harness \
  --subset data/miniset.jsonl \
  --out results \
  --arms all \
  --cost-ceiling 1.00 \
  --pricing-file config/pricing.list_sync.2026-10-31.json
```

The harness also supports one arm and a case subset for debugging:

```bash
PYTHONPATH=src python -m eval.harness --subset data/miniset.jsonl \
  --out results --arms haiku-agent --cases mini-001,mini-002 \
  --cost-ceiling 0.25 \
  --pricing-file config/pricing.list_sync.2026-10-31.json
```

New Phase-2 flags: `--arms` (`all` or a comma-separated list), `--cases`,
`--repeats`, `--cost-ceiling` (required for a live run), `--pricing-file`
(model ID -> per-MTok list prices), `--run-id` (resume an interrupted run),
`--no-resume`, and `--dry-run`. The existing `--baseline` flag selects
`sonnet-direct`; `--gate` remains reserved for Phase 7.

The pinned pricing document is
`config/pricing.list_sync.2026-10-31.json`. It records `pricing_basis:
"list_sync"`, USD per MTok, the verification date, and the two verified model
IDs. Haiku has a ≤100K-prompt tier and an over-100K tier; the over-100K tier
deliberately has **no cache-write rate**. The loader does not invent one: if a
cache-write token is ever billed in that tier, cost calculation raises
`PricingError` instead. No prompt caching is sent in Phase 2. Thinking tokens
are already included in `output_tokens` and are not added again. Batch rates
are excluded. Cost is computed from logged usage, never from token estimates.

Batch direct-prompt submission is **deferred and out of scope for Phase 2**
(owner decision 2026-10-31). All runs use synchronous list-price accounting
(`pricing_basis: "list_sync"`); the design's batch path remains a later-phase
item and is not mixed into the Phase-2 cost basis.

Effort is recorded as `api_default` for all three arms; **no effort parameter
is transmitted** in Phase 2. Final effort selection is Phase 3. The cost
ceiling is checked after every model response and before any additional
schema-repair or infrastructure attempt. Infrastructure retries are per model
request, so the maximum HTTP requests per case/repeat are 9 for `haiku-agent`
and 3 for a direct arm.

Results are written under the gitignored `results/` tree: case checkpoints,
per-arm `results.json` files, a combined `results.json`, and `summary.json`.
Every Phase-2 record is labelled `mini/smoke` / `mini_smoke`; these numbers are
excluded from headline results. Resuming with `--run-id` requires the same
arms and case selection, so completed checkpoints are never reordered or
rescored. The run metadata records the exact list prices used and labels
`max_tokens` as provisional until it is pinned.


## Ground rules (from the resume quest)

- Nothing is claimed on the resume until it's merged and measured.
- Every before-number must pre-exist (git history, config) or be runnable once, cheaply.
- Small-n claims carry 95% confidence intervals or paired comparisons.
- Held-out tools are never touched as gold/answer tools during development.
  Candidate tools may appear as distractors in dev rows; that exposure is
  counted and disclosed, not banned (`docs/CANDIDATES.md`).
- `docs/FAILURE_TAXONOMY.md` is a descriptive diagnostic vocabulary, not a
  filter or an exclusion list.
