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

## Status: Phase 2 code-ready, live gate pending

The mini-set, tool schemas, agent loop, direct arms, and harness skeleton are
implemented and pass offline tests. The measurable Phase-2 gate — all three
arms running end-to-end on the mini-set — is **awaiting the owner's explicit
"go"**. Model IDs and Anthropic list prices are not yet verified/pinned in the
repo, so a live run is blocked until explicit configuration is supplied.

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

# Live run (only after the owner pins model IDs/prices and says "go"):
PYTHONPATH=src python -m eval.harness \
  --subset data/miniset.jsonl \
  --out results \
  --arms all \
  --cost-ceiling 1.00 \
  --pricing-file /path/to/pinned-pricing.json
```

The harness also supports one arm and a case subset for debugging:

```bash
PYTHONPATH=src python -m eval.harness --subset data/miniset.jsonl \
  --out results --arms haiku-agent --cases mini-001,mini-002 \
  --cost-ceiling 0.25 --pricing-file /path/to/pinned-pricing.json
```

New Phase-2 flags: `--arms` (`all` or a comma-separated list), `--cases`,
`--repeats`, `--cost-ceiling` (required for a live run), `--pricing-file`
(model ID -> per-MTok list prices), `--run-id` (resume an interrupted run),
`--no-resume`, and `--dry-run`. The existing `--baseline` flag selects
`sonnet-direct`; `--gate` remains reserved for Phase 7.

The pricing file is explicit configuration because the repository pins no
Anthropic list prices:

```json
{
  "claude-haiku-5-5": {"input": null, "output": null,
                        "cache_read": null, "cache_write": null},
  "claude-sonnet-5-5": {"input": null, "output": null,
                         "cache_read": null, "cache_write": null}
}
```

Replace every null with a verified list price. The harness refuses a live run
if any selected model has no price, and refuses a priced token class with no
pinned cache price. Cost is computed from logged usage, never from token
estimates.

Batch direct-prompt submission is **not implemented** in Phase 2. All runs use
synchronous list-price accounting (`pricing_basis: "list_sync"`); the design's
batch path has no pinned batch prices here, so it is left for a later phase
rather than mixed into the synchronous cost basis.

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
