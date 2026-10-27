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

## Status: in progress

This is a skeleton. The agent, harness, and judge are stubbed with TODOs; the design is
written down in `docs/EVAL_DESIGN.md`. Implementation order is in `PROJECT_PLAN.md`.

## Layout

```
agent-eval-harness/
├── src/agent/        # raw-API tool-using agent
│   ├── agent.py      # main loop: prompt -> tools -> budget -> answer
│   └── tools.py      # tool definitions (JSON schemas + implementations)
├── src/eval/         # evaluation harness
│   ├── harness.py    # run loop: cases -> agent -> trace -> score
│   ├── scorer.py     # schema-free per-case matcher (MATCH_SPEC v1.1)
│   ├── judge.py      # LLM-as-judge (different model family from the agent)
│   ├── metrics.py    # pass@k, cost/task, latency, accuracy
│   └── calibration.py# judge calibration vs human labels (Cohen's kappa)
├── tests/            # pytest: scorer pair tests + harness smoke tests
├── scripts/
│   ├── select_candidates.py  # reproducible Phase-0 candidate verifier
│   ├── audit_required_filter.py  # final required-args rule audit
│   └── audit_variance_m.py   # §7 m/u usable-cases sensitivity
├── PROJECT_PLAN.md   # phase-by-phase build sequence
├── docs/
│   ├── EVAL_DESIGN.md      # the full eval plan — read this first
│   ├── MATCH_SPEC.md       # sole authority on exact/partial scoring
│   ├── CANDIDATES.md       # frozen held-out-tool candidates (seed + sha256)
│   ├── REQUIRED_FILTER_AUDIT.md  # required-args audit for owner sign-off
│   ├── BUDGET.md           # $20 ledger and ceilings
│   └── FAILURE_TAXONOMY.md # descriptive diagnostics, not an exclusion list
├── data/             # benchmark subsets live here (gitignored, see data/README.md)
└── .github/workflows/ci.yml  # offline tests; LLM eval gate added in Phase 7
```

## Quickstart (once implemented)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
# request access to the gated xLAM-60k dataset, then:
python -m src.eval.harness --subset data/xlam60k_dev.jsonl
```

## Ground rules (from the resume quest)

- Nothing is claimed on the resume until it's merged and measured.
- Every before-number must pre-exist (git history, config) or be runnable once, cheaply.
- Small-n claims carry 95% confidence intervals or paired comparisons.
- Held-out tools are never touched as gold/answer tools during development.
  Candidate tools may appear as distractors in dev rows; that exposure is
  counted and disclosed, not banned (`docs/CANDIDATES.md`).
- `docs/FAILURE_TAXONOMY.md` is a descriptive diagnostic vocabulary, not a
  filter or an exclusion list.
