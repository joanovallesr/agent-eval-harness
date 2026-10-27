# Build Plan — Agent + Eval Harness

Locked by the grill session of 2026-10-23. Work the phases in order; each
phase ends with something merged and measurable. The full locked design lives
in `docs/EVAL_DESIGN.md` — this file is the sequence, that file is the law.

## Phase 0 — Data access + revision pin
- [x] Access granted 2026-10-23 (click-through; gated: auto).
- [ ] Download `xlam_function_calling_60k.json` and **pin the exact revision
      hash** in `data/README.md` (reference: `26d14ebf...`, 2025-01-24 — pin
      whatever you actually download).
- [ ] Read the dataset-card license AND the accepted use terms; record the
      verdict on trace republication in `data/README.md` before any dataset
      row appears on a public site or repo. (Hub tag reads `cc-by-4.0` —
      unverified for our use until you read the terms.)
- [ ] Check the dataset viewer for category labels; derive categories if
      absent (document the rule before any results). Freeze the ordered list
      of 8 candidate held-out categories — seeded, sha256-hashed, committed.
- [ ] Compute usable-row counts per category (post-filter) and record them —
      filters depend only on dataset structure, so this needs no tag.
- [ ] Run the tool-name leak check across the candidate split (dataset
      structure only); apply the pre-registered replacement rule.

## Phase 1 — Match spec + scorer (the checker is the single point of failure)
- [ ] Write `docs/MATCH_SPEC.md`: argument-type coercion, parallel-call
      ordering, optional args, string case, numeric equivalence — every rule
      explicit, no silent leniency.
- [ ] Implement the scorer + deterministic partial credit (right tool,
      per-field argument score; combination formula in the spec).
- [ ] Hand-made right/wrong pair tests covering every spec rule, including
      adversarial near-misses. These are required CI from day one.
- [ ] Measurable: `pytest` green on the pair tests before any real scoring.

## Phase 2 — Mini-set + agent skeleton + harness skeleton
- [ ] ~20 hand-written tasks: pipeline smoke test and the seed of the
      multi-step set. Excluded from headline numbers.
- [ ] `src/agent/agent.py`: raw Anthropic API loop. On xLAM the loop is
      **schema-validation retries only** (cap 2) — it never consults ground
      truth or the scorer for retry decisions (EVAL_DESIGN §3).
- [ ] `src/agent/tools.py`: tool schemas for the mini-set.
- [ ] `src/eval/harness.py`: run cases → traces → scores; cost from **logged
      API usage**; per-run cost ceiling with loud abort; checkpoints; aborted
      runs recorded, not deleted.
- [ ] `haiku-direct` and `sonnet-direct`: single-prompt, no-loop arms sharing
      the pinned tool-use interface (EVAL_DESIGN §2). Batch path for
      direct-prompt passes (list-price accounting kept separate, §8).
- [ ] Set the provider-console spend limit (second line of defense) and
      create `docs/BUDGET.md` actuals table.
- [ ] Measurable: all three arms run end-to-end on the mini-set from one
      command, with cost/task printed.

## Phase 3 — Pilot, dev runs, measurement
- [ ] Pilot: ~30 cases per arm at k=1 (ceiling $1). Measure real tokens and
      cost per case per arm, failure modes, retry rate.
- [ ] From the pilot: choose k; define the dev sample (seeded, stratified by
      category, fixed size) with its own spend ceiling.
- [ ] Cost feasibility gate: pilot cost ratio (haiku-agent ÷ sonnet-direct,
      list price) must clear 0.10 or the cost claim is redesigned/dropped now.
- [ ] Effort selection: small slices at candidate efforts per arm under the
      pre-specified rule (EVAL_DESIGN §2); verify logged tokens include
      thinking tokens.
- [ ] Run all three arms on the dev sample at the chosen k and efforts.
      (Variance is measured at the final k — averaging repeats changes
      per-case variance, so sizing from a different k would mislead.)
- [ ] Measure: within-category variance, discordant rate,
      leave-one-category-out between-category spread, real token counts per
      arm (verify logged tokens include thinking tokens).
- [ ] Effort selection: run a small dev slice at candidate effort values per
      arm (verify allowed values in the docs); fix per-arm effort for
      pre-registration.
- [ ] Fix the failure-policy table against observed failures (EVAL_DESIGN §4).
- [ ] Dry-run the full analysis pipeline on dev outputs (estimator, both
      intervals, cost ratio, results JSON).
- [ ] **Feasibility check, before pre-registration:** LOCO s_pess and the
      pilot cost ratio against the §7 power inequality and the 0.10 cost bar.
      A doomed design is caught here, not after the tag.
- [ ] Measurable: a dev results JSON with the sizing inputs (s_within²,
      LOCO s_cat spread).

## Phase 4 — Pre-registration + split freeze
- [ ] Write `docs/PRE_REGISTRATION.md` (contents per EVAL_DESIGN §13):
      hypothesis, margin, assumed gap, cost definition, model IDs, effort
      values, prompt versions, tool schemas, `max_tokens`, match-spec version,
      k per arm with the **closed budget arithmetic**, analysis plan, primary
      vs secondary comparisons, failure policy, sampling seed.
- [ ] Commit **and git-tag** it. The tag is the proof of "dated and committed."
- [ ] Materialize the test rows deterministically from the Phase-0 seeds
      (C from {6, 7, 8} chosen on the LOCO estimate); verify the sizing
      target on post-filter usable rows; record checksums in `data/README.md`.
      Record C and its reason in `PRE_REGISTRATION.md` **before** the tag —
      it is not chosen at materialization.
- [ ] Measurable: tag exists; test split checksums in `data/README.md`.

## Phase 5 — Judge calibration (secondary question only)
- [ ] Multi-step set: ~30 hand-written tasks with deterministic mock tools.
- [ ] Generate traces: run the loop with both models (`haiku-agent`,
      `sonnet-agent`) on the multi-step set. (No direct arm here — it would
      fail by construction.)
- [ ] Written rubric, blind labeling (arm hidden), split by task (~15 tune
      tasks / ~15 report tasks), ~15 traces relabeled a week later for the
      intra-rater ceiling.
- [ ] One calibration round (one prompt revision, defined up front), time-boxed.
- [ ] Calendar time-box (e.g. 2 weeks), non-blocking: Phase 6 does not wait
      for Phase 5 — the judge is secondary.
- [ ] Report κ with 95% CI. Ship rule: point estimate ≥ 0.70; a miss is a
      published negative result and the resume states measured κ and n.
- [ ] Measurable: calibration report committed; judge prompt versioned.

## Phase 6 — Held-out run + report
- [ ] Run the held-out set **once** (it's the test set — no tuning on it).
- [ ] Fill in `docs/RESULTS.md`: paired interval, cost ratio, CIs, negative
      results at equal prominence, mandatory limitations section.
- [ ] README summary table **generated** from the same results JSON.
- [ ] Update the resume line from "(in progress)" to shipped numbers.
- [ ] Measurable: RESULTS.md committed; resume updated.

## Phase 7 — CI gate
- [ ] The scaffold's `.github/workflows/ci.yml` already runs lint + pytest on
      every push/PR (pair tests are required from Phase 1). This phase adds
      only the LLM gate: rework the scaffold's `eval-gate` job to
      **advisory-first** (non-blocking) until the false-alarm rate is
      measured, then promote to required.
- [ ] LLM gate: 50-case dev subset, Haiku arms, tolerance band from repeated
      unchanged-code runs, rerun-once on failure, everything pinned,
      reference score in repo, **dedicated key with its own low spend limit**
      (untrusted PRs can't read repo secrets).
- [ ] Document the gate's honest sensitivity (smoke detector, not instrument).
- [ ] Measurable: deliberately broken prompt change fails the gate (test the
      test) — after the advisory period.

## Phase 8 — Dashboard (late, time-boxed)
- [ ] After real dev results exist: static Next.js export on Vercel, renders
      only; Python computes once into the versioned results JSON contract
      (run ID, commit, model IDs, effort, prompt versions, dev-vs-final).
- [ ] Sampled, scrubbed traces — only after the §5 license/terms verdict
      clears. Negative results and dev-vs-final labeling at equal prominence.
- [ ] Stretch: replay viewer ("recorded run"), not live.
- [ ] Measurable: site live; resume gains a Demo link next to Code.
