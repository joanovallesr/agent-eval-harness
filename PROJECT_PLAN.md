# Build Plan — Agent + Eval Harness

Locked by the grill session of 2026-10-23. Work the phases in order; each
phase ends with something merged and measurable. The full locked design lives
in `docs/EVAL_DESIGN.md` — this file is the sequence, that file is the law.

## Phase 0 — Data access + revision pin + held-out-tool freeze
- [x] Access granted 2026-10-23 (click-through; gated: auto).
- [x] Pin the exact revision in `data/README.md`:
      `26d14ebfe18b1f7b524bd39b404b50af5dc97866` (2025-01-24).
- [ ] Read the dataset-card license AND the accepted use terms; record the
      verdict on trace republication in `data/README.md` before any dataset
      row appears on a public site or repo. (Phase F drafts the verdict from
      the public card; the click-through terms still need confirmation.)
- [x] Pivot the split unit from category to tool. Freeze the ordered list of
      12 candidate held-out tools — seed 42, sha256-hashed, committed in
      `docs/CANDIDATES.md`.
- [x] Compute post-filter single-tool counts per tool and the 71-tool
      structural eligible pool before the required-arguments rule — filters
      depend only on dataset structure, so this needs no tag. Verify the 12
      candidates against the 100-case floor.
- [x] Run the leak check and distractor-exposure count across the candidates
      (dataset structure only); document the 12-candidate replacement rule.
- [x] Commit the reproducible verifier `scripts/select_candidates.py`; the
      command in `docs/CANDIDATES.md` regenerates the frozen order and
      sha256 and asserts both.

## Phase 1 — Match spec + scorer (the checker is the single point of failure)
- [x] Write `docs/MATCH_SPEC.md` v1.1: attempts, final-attempt selection,
      pinned `tool_use` interface, gold normalization/exclusion, numeric
      conversion, malformed-block precedence, exact-optimal assignment,
      partial credit, lenient-string scope — every rule explicit, no silent
      leniency. (Final for this revision, not pre-registered.)
- [x] Implement the scorer + deterministic partial credit in
      `src/eval/scorer.py` (per-case only; repeat averaging stays in the
      harness).
- [x] Hand-made right/wrong pair tests covering every spec rule, including
      adversarial near-misses (`tests/test_scorer.py`). Required CI from day
      one.
- [x] Measurable: `pytest tests/test_scorer.py` green before any real
      scoring.

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
      the pinned tool-use interface (EVAL_DESIGN §2). **Batch path deferred on
      2026-10-31 and out of scope for Phase 2; Phase 2 uses
      synchronous list-price accounting only (`pricing_basis: list_sync`).**
      (The EVAL_DESIGN §8 batch path remains a later-phase item.)
- [ ] Set the provider-console spend limit (second line of defense) and
      create `docs/BUDGET.md` actuals table.
- [ ] Measurable: all three arms run end-to-end on the mini-set from one
      command, with cost/task printed.

## Phase 3 — Pilot, dev runs, measurement
- [ ] Pilot: ~30 cases per arm at k=1 (ceiling $1). Measure real tokens and
      cost per case per arm, failure modes, retry rate.
- [ ] From the pilot: choose k; define the dev sample (seeded, stratified by
      tool, fixed size, non-candidate tools only) with its own spend ceiling.
- [ ] Cost feasibility gate: pilot cost ratio (haiku-agent ÷ sonnet-direct,
      list price) must clear 0.10 or the cost claim is redesigned/dropped now.
- [ ] Effort selection: small slices at candidate efforts per arm under the
      pre-specified rule (EVAL_DESIGN §2); verify logged tokens include
      thinking tokens.
- [ ] Run all three arms on the dev sample at the chosen k and efforts.
      (Variance is measured at the final k — averaging repeats changes
      per-case variance, so sizing from a different k would mislead.)
- [ ] Measure: within-tool variance, discordant rate, leave-one-tool-out
      between-tool spread, real token counts per arm (verify logged tokens
      include thinking tokens).
- [ ] Fix the failure-policy table against observed failures (EVAL_DESIGN §4).
- [ ] Dry-run the full analysis pipeline on dev outputs (estimator, both
      intervals, cost ratio, results JSON).
- [ ] **Feasibility check, before pre-registration:** leave-one-tool-out
      s_pess and the pilot cost ratio against the §7 power inequality and the
      0.10 cost bar. A doomed design is caught here, not after the tag.
- [ ] Measurable: a dev results JSON with the sizing inputs (s_within²,
      leave-one-tool-out s_tool spread, m = dev tools, u).

## Phase 4 — Pre-registration + split freeze
- [x] Write `docs/PRE_REGISTRATION.md` (contents per EVAL_DESIGN §13, read
      through the 2026-10-31 estimation-first amendment): the estimation-first
      claim and estimand, the binary cost rule, cost definition, model IDs,
      effort values, prompt versions, tool schemas, `max_tokens`, match-spec
      version (v1.1), k per arm with the **closed budget arithmetic**,
      analysis plan, primary vs secondary comparisons, failure policy, sampling
      seed, the 12 ordered candidates, distractor exposure, and the leak-check
      result.
- [x] Commit **and git-tag** it. The tag is the proof of "dated and committed."
      Tag: `pre-registration-v1`.
- [x] Materialize the test rows deterministically from the Phase-0 seeds
      (C = 8, chosen for interval precision and selected from the frozen 12
      candidate tools as the first eight in order); verify the sizing target
      on post-filter usable rows; record checksums in `data/README.md`.
      Record C and its reason in `PRE_REGISTRATION.md` **before** the tag —
      it is not chosen at materialization.
- [x] Measurable: tag exists; test split checksums in `data/README.md`.

## Phase 5 — Judge calibration (secondary question only)
- [x] Multi-step set: ~30 hand-written tasks with deterministic mock tools.
- [x] Generate traces: run the loop with both models (`haiku-agent`,
      `sonnet-agent`) on the multi-step set. (No direct arm here — it would
      fail by construction.)
- [x] Written rubric, blind labeling (arm hidden), split by task (~15 tune
      tasks / ~15 report tasks), ~15 traces relabeled in a separate blind
      batch. (Rubric and 60 labels complete. The relabel batch was run with
      the judge because no second human labels existed; actual gap recorded.
      With one-class human labels, human intra-rater κ is undefined.)
- [x] One calibration round (one prompt revision, defined up front), time-boxed.
      (Zero tune-half disagreements; no revision was justified; v1 frozen.)
- [x] Calendar time-box (e.g. 2 weeks), non-blocking: Phase 6 does not wait
      for Phase 5 — the judge is secondary.
- [x] Report κ with 95% CI. Ship rule: point estimate ≥ 0.70; a miss is a
      published negative result and the resume states measured κ and n.
      (κ undefined from one-class labels; raw agreement 30/30; ship rule
      missed and reported in `docs/PHASE5_CALIBRATION.md`.)
- [x] Measurable: calibration report committed; judge prompt versioned.

## Phase 6 — Held-out run + report
- [x] Run the held-out set **once** (it's the test set — no tuning on it).
      Completed in one pass with no aborted cases and no restarts.
- [x] Fill in `docs/RESULTS.md`: paired interval, cost ratio, CIs, negative
      results at equal prominence, mandatory limitations section.
- [x] README summary table **generated** from the same results JSON.
- [ ] Update the resume line from "(in progress)" to shipped numbers.
      A draft bullet is supplied in the Phase 6 report; I place it myself.
- [x] Measurable: RESULTS.md committed; resume draft supplied.

## Phase 7 — CI gate (advisory in this version)
- [x] I kept `.github/workflows/ci.yml` running Ruff + the full offline pytest
      suite on every push/PR (pair tests are required from Phase 1).
- [x] I added a PR-only `eval-gate` job. It depends on the `test` job, is
      advisory, and never fails the build. Pytest failures still fail the
      separate `test` job normally.
- [x] I changed the gate scope from the original 50-case dev sketch to the
      tracked 20-case mini-set only: `haiku-agent`, effort `low`, `max_tokens`
      1024, MATCH_SPEC v1.1 scoring, and the pinned `list_sync` prices.
- [x] I made the gate use **no judge verdicts**. Phase 5's κ is undefined and
      the ship rule is not met, so the gate is scorer-based.
- [x] I made it create or update one PR comment with exact and partial scores,
      a diff against the committed baseline, per-case changes by id, and the
      list-price cost. A missing key, failed run, or cost-ceiling stop is
      reported in the comment.
- [x] I set a hard **$0.25** per-run ceiling via the existing harness ceiling
      mechanism; actual cost is printed in the comment.
- [x] I committed the baseline at `baselines/phase7-miniset-haiku-low.json`
      with the model id, effort, `max_tokens`, MATCH_SPEC version, pricing
      file, mini-set SHA-256, per-case scores, and cost. Regeneration is a
      manual, documented act; CI never writes it.
- [x] I added a local dry-run that renders the exact comment from a saved
      results file, with offline tests for the formatter and baseline diff.
- [x] I documented the honest scope: advisory, scorer-based, mini-set only, no
      judge, and not a headline result (`README.md`).
- [ ] Promotion beyond advisory is a later decision; it needs a measured
      false-alarm rate and is not part of this version.
- [ ] The deliberately broken prompt test ("test the test") waits for that
      promotion decision.

## Phase 8 — Dashboard (late, time-boxed)
- [ ] After real dev results exist: static Next.js export on Vercel, renders
      only; Python computes once into the versioned results JSON contract
      (run ID, commit, model IDs, effort, prompt versions, dev-vs-final).
- [ ] Sampled, scrubbed traces — only after the §5 license/terms verdict
      clears. Negative results and dev-vs-final labeling at equal prominence.
- [ ] Stretch: replay viewer ("recorded run"), not live.
- [ ] Measurable: site live; resume gains a Demo link next to Code.
