# Agent + Eval Harness

A tool-using agent built on the **raw Anthropic API** (no agent framework), scored on a
**public agent-benchmark subset**, with an **LLM judge whose calibration is measured**
and an **advisory CI eval gate** that reports score changes without ever failing a
build.

## Why this exists

Shipping an agent is easy. Proving it works is the hard part — and it's the part most
portfolios skip. This project proves it:

1. **Raw-API agent** — tool-use loop, schema-validation retries, budgets. No LangChain/LangGraph magic.
2. **Honest benchmark** — public tool-calling dataset (xLAM-60k, revision pinned),
   held-out tools (the cluster and split unit; the paper's "categories" are
   descriptive only), and a Sonnet-5.5 direct-prompt comparison arm (a
   mid-tier model ~20x pricier per token — never called "frontier").
   The locked claim reports the paired accuracy difference as an estimate,
   with cost parity as the only binary rule (95% upper bound ≤ 0.10).
3. **Judge calibration, scoped** — an LLM judge from a **different model family**
   (GPT-6 Luna) serves the secondary multi-step question only: blind labels,
   a tune/report split, κ with a confidence interval. A miss is a published
   negative result. Phase 5's report-half κ is undefined because both label
   sets used only `correct`; that miss is stated in
   `docs/PHASE5_CALIBRATION.md`.
4. **CI gate, advisory by design** — Ruff and the offline pytest suite are
   required. The Phase-7 eval gate is scorer-based and mini-set only, never uses
   a judge verdict, and reports its result in a PR comment instead of failing the
   build.

## Status: Phase 7 advisory gate implemented; Phase 6 held-out run and report complete; Phase 5 judge miss reported

The mini-set, tool schemas, agent loop, direct arms, and harness are
implemented and pass offline tests. The three-arm live smoke run completed on
the 20-case mini-set at a total list-price cost of **$0.073719**; those numbers
are `mini/smoke` only and are excluded from every headline result. Model IDs
are verified/pinned in `src/eval/config.py` and list prices are pinned in
`config/pricing.list_sync.2026-10-31.json`.

Phase 3's offline half is implemented: deterministic pilot/dev sampling over
the pinned revision, aborted-case resume semantics, paired analysis with
variance components and §7 sizing, and effort plumbing. The Stage 1 pilot ran
on 30 shared non-candidate-tool cases at k=1 for **$0.095948** total; those
numbers are `pilot` only and excluded from headline results. The aggregate
pilot list-price cost ratio was **0.0532**, clearing the pre-set 0.10 gate.
Stage 2 ceiling: **$1.03**; final spend: **$1.023421**. The §2 selection
functions chose Haiku shared = **`low`** and Sonnet = **`api_default`**. The
Sonnet choice rests on a one-case margin over `max` and is recorded as
noise-level evidence. I ran the Stage 3 dev sample — 30 tools × 12 cases,
k=1 — for **$1.264953** under its **$1.50** ceiling. Dev exact match was 0.825
for both Haiku arms and 0.8194 for Sonnet; these are dev-only numbers. I ran
the Stage 4 analysis dry run; the aggregate report is
`docs/PHASE3_DEV_ANALYSIS.md`. The §7 power check does not pass at C in
{6,7,8}; the design is estimation-first per the 2026-10-31 amendment in
`docs/EVAL_DESIGN.md`. Phase 4 is complete: `docs/PRE_REGISTRATION.md` is
tagged `pre-registration-v1`, and the test sample is materialized as 936
cases (8 tools × 117). Phase 6 is complete: the held-out run finished in one
pass with no aborted cases, and the generated results table below and
`docs/RESULTS.md` carry the estimate, intervals, cost verdict, per-tool
table, and failure taxonomy.

Phase 5 Part 1 is complete: the 30-task hand-written multi-step set with
scripted mock results is tracked in `data/multistep.jsonl` and the final
60-trace run (two loop arms, no direct arm) completed with no truncations,
format failures, or fixture misses. Trace spend is **$1.287014**. Phase 5
Part 2 is complete: I labeled all 60 traces blind, the judge agreed 30/30 on
the tune half (no revision justified, prompt v1 frozen) and 30/30 on the
report half. Both label sets used only `correct`, so Cohen's κ is undefined
rather than large; the ship rule is a published negative result, with raw
agreement 30/30 and n = 30 report traces over 15 tasks. Judge spend is
**$0.020836**; Phase 5 total is **$1.307851** against the $1.50 ceiling. See
`docs/PHASE5_CALIBRATION.md`.

Phase 7's advisory CI gate is implemented. Because the Phase 5 judge is not
calibrated (κ undefined, ship rule not met), the gate uses **no judge verdicts
anywhere**. It runs only the tracked 20-case mini-set through `haiku-agent` at
effort `low`, `max_tokens` 1024, MATCH_SPEC v1.1 scoring, and the pinned
`list_sync` prices. It posts one PR comment with exact and partial scores, a
diff against a committed baseline, per-case changes by id, and the run's
list-price cost. It never fails a build, never runs the held-out test split or
any dev/test sample, and stops at a hard **$0.25** per-run ceiling. The first
real CI run happens when I push and open a pull request myself.

## Layout

```
agent-eval-harness/
├── src/agent/        # raw-API tool-using agent
│   ├── agent.py      # loop: prompt -> tool_use -> schema validation -> repair
│   ├── multistep_agent.py  # Phase-5 loop with scripted mock results
│   ├── multistep_tools.py  # original multi-step tool schemas
│   ├── prompts.py    # frozen Phase-2 system + repair prompts
│   └── tools.py      # mini-set JSON schemas (no tool execution in Phase 2)
├── src/eval/         # evaluation harness
│   ├── harness.py    # run loop: cases -> arm -> trace -> score -> results
│   ├── multistep.py  # multi-step loader, validator, and trace runner
│   ├── labeling.py   # blind human-labeling packet builder
│   ├── mini_set.py   # mini-set loader + schema/scorer validation
│   ├── config.py     # explicit model/effort/prompt configuration
│   ├── scorer.py     # schema-free per-case matcher (MATCH_SPEC v1.1)
│   ├── metrics.py    # cost, paired intervals, variance components, §7 sizing
│   ├── analysis.py   # Phase-3 analysis dry run over a results directory
│   ├── judge.py      # LLM-as-judge (different model family from the agent)
│   ├── calibration.py# judge calibration vs human labels (Cohen's kappa)
│   └── gate.py       # advisory Phase-7 gate: comment + baseline diff
├── tests/            # pytest: scorer pairs, agent loop, harness, mini-set
├── scripts/
│   ├── select_candidates.py  # reproducible Phase-0 candidate verifier
│   ├── audit_required_filter.py  # final required-args rule audit
│   ├── audit_variance_m.py   # §7 m/u usable-cases sensitivity
│   ├── sample_cases.py       # deterministic Phase-3 pilot/dev sampler
│   ├── phase6_report.py      # one-source Phase-6 analysis + doc generator
│   ├── phase6_cache_monitor.py # real-time nonzero-cache stop guard
│   └── eval_gate.py          # Phase-7 gate CLI + local dry-run/baseline writer
├── data/
│   ├── miniset.jsonl # tracked hand-written Phase-2 smoke set (20 cases)
│   ├── multistep.jsonl # tracked hand-written Phase-5 set (30 tasks)
│   └── README.md     # revision pin, license draft, split notes
├── baselines/
│   └── phase7-miniset-haiku-low.json # committed gate baseline (manual)
├── PROJECT_PLAN.md   # phase-by-phase build sequence
├── docs/
│   ├── EVAL_DESIGN.md      # the full eval plan — read this first
│   ├── PRE_REGISTRATION.md # held-out claim, design, and budget (tagged)
│   ├── MATCH_SPEC.md       # sole authority on exact/partial scoring
│   ├── CANDIDATES.md       # frozen held-out-tool candidates (seed + sha256)
│   ├── REQUIRED_FILTER_AUDIT.md  # required-args audit (final rule)
│   ├── BUDGET.md           # $20 ledger and ceilings
│   ├── MULTISTEP_RUBRIC.md # Phase-5 blind labeling rubric (v1)
│   ├── PHASE5_MULTISTEP.md # Phase-5 task set, traces, and packet
│   ├── PHASE5_CALIBRATION.md # Phase-5 judge calibration and κ result
│   ├── RESULTS.md          # Phase-6 held-out results (generated)
│   └── FAILURE_TAXONOMY.md # descriptive diagnostics, not an exclusion list
└── .github/workflows/
    ├── ci.yml               # Ruff + pytest; advisory eval gate on PRs
    └── eval-gate-comment.yml # fixed fork skip notice (read-only PR token)
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
`sonnet-direct`. The advisory Phase-7 gate is a separate command
(`scripts/eval_gate.py`), not a harness flag; it never fails a build.

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
(2026-10-31). All runs use synchronous list-price accounting
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


## Phase 3 offline commands

The sampler writes only gitignored case files and aggregate metadata:

```bash
# Pilot: ~30 distinct non-candidate tools, one case each (seed 42).
PYTHONPATH=src:scripts python scripts/sample_cases.py \
  --stage pilot --n-tools 30 --cases-per-tool 1 --seed 42

# Dev shape is proposed after the pilot; this is the command form.
PYTHONPATH=src:scripts python scripts/sample_cases.py \
  --stage dev --n-tools 12 --cases-per-tool 10 --seed 43
```

A live run takes the key from the repo-root `.env` without printing it:

```bash
set -a; source .env; set +a
PYTHONPATH=src python -m eval.harness \
  --subset data/phase3/pilot.jsonl --out results --arms all \
  --cost-ceiling 1.00 \
  --pricing-file config/pricing.list_sync.2026-10-31.json \
  --label phase3/pilot --dev-vs-final pilot
```

The analysis dry run reads a results directory and writes aggregates only:

```bash
PYTHONPATH=src python -m eval.analysis --run-dir results/<run_id> \
  --cases data/phase3/dev.jsonl --out results/<run_id>/analysis.json
```

**Effort mechanism (verified against anthropic SDK 2.3.0 type definitions).**
A concrete effort is sent as `output_config={"effort": "low"|"medium"|"high"|
"xhigh"|"max"}` (`anthropic/types/output_config_param.py`); there is no
top-level `effort` request field. `api_default` sends no effort field.

**Effort selection rule.** `select_sonnet_effort` never weakens Sonnet: it
picks the API default unless a measured slice is strictly stronger.
`select_shared_haiku_effort` gives both Haiku arms one effort: the lowest
effort whose worst score across the two Haiku arms is within the
pre-registered tolerance (`EFFORT_TOLERANCE = 0.02`) of the best such score.
The pilot cost gate is `pilot_cost_gate(ratio)`: pass only when the aggregate
haiku-agent / sonnet-direct list-price ratio is `<= 0.10`.

**Usage fields.** The SDK's `Usage` object returns `input_tokens`,
`output_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens`,
`output_tokens_details.thinking_tokens`, and optional cache/server-tool
breakdowns. `output_tokens` is the inclusive, authoritative billing total and
already contains thinking tokens; `output_tokens_details.thinking_tokens` is a
read-only decomposition and must not be added again.

**Aborted-case resume.** A cost-ceiling abort is checkpointed with
`status: "aborted"`, not as a completed result. Its 0 may appear only in the
aborted run's own accounting. A resume re-attempts that case; the earlier 0 is
never inherited. Resuming from a checkpoint is allowed; restarting a run after
seeing partial results is forbidden.

## Phase 5 multi-step commands

The multi-step set is separate from the pre-registered test. It uses scripted
mock results and only the two loop arms:

```bash
# Show the plan without making an API call.
PYTHONPATH=src python -m eval.multistep --dry-run \
  --pricing-file config/pricing.list_sync.2026-10-31.json --cost-ceiling 0.90

# Generate traces (resumable; aborted runs are recorded, not deleted).
set -a; source .env; set +a
PYTHONPATH=src python -m eval.multistep \
  --pricing-file config/pricing.list_sync.2026-10-31.json \
  --cost-ceiling 0.90 --run-id <run-id>

# Build the blind human-labeling packet from a completed run.
PYTHONPATH=src python -m eval.labeling \
  --run-dir results/<run-id> --cases data/multistep.jsonl \
  --packet-dir results/phase5-labeling/packet \
  --mapping results/phase5-labeling/private/mapping.json \
  --rubric docs/MULTISTEP_RUBRIC.md
```

The packet contains no model identity, cost, token count, or judge output.
The private mapping stays outside the packet.

## Phase 7 — advisory CI eval gate

The gate is deliberately narrower than the original Phase-7 sketch. Because
the Phase 5 judge is not calibrated, it uses **no judge verdicts**. It is
scorer-based, mini-set only, and advisory: the eval step always exits 0. A
missing key, a failed run, or a cost-ceiling stop is reported in the PR
comment, never as a red build. Ruff and the full pytest suite remain required
in the separate `test` job and still fail normally.

On a pull request, `.github/workflows/ci.yml`:

1. runs Ruff and the full offline pytest suite;
2. runs `data/miniset.jsonl` (20 cases) through `haiku-agent` at effort `low`,
   `max_tokens` 1024, MATCH_SPEC v1.1 scoring, and
   `config/pricing.list_sync.2026-10-31.json`; and
3. posts or updates one PR comment with exact and partial scores, the diff
   against `baselines/phase7-miniset-haiku-low.json`, per-case changes by id,
   and the run's list-price cost.

The gate never runs the held-out test split and never re-runs a dev or test
sample. The scoring path is the existing scorer; the judge is not called.

**Secret and forks.** The workflow reads `ANTHROPIC_API_KEY` from GitHub
Actions secrets. I add that secret in repository settings myself; the code only
checks whether it is present and never prints it. Fork pull requests do not
receive repository secrets, so the eval step skips with a clear notice in the
comment and the workflow still succeeds. Fork `pull_request` tokens are also
read-only, so a companion `workflow_run` job in the base repository posts a
fixed skip notice for forks. It never checks out or runs pull-request code and
never trusts PR-authored comment text.

**Cost guard.** The hard per-run ceiling is **$0.25**, enforced by the
harness's existing ceiling mechanism. The actual list-price cost is printed in
the comment. If the ceiling is hit, the comment says so and reports any partial
results. For scale, the Phase 2 all-arms mini-set run cost $0.073719 and this
single-arm gate baseline cost $0.003351.

**Local dry-run (no API call).** Render the exact comment from a saved results
file:

```bash
PYTHONPATH=src:scripts python scripts/eval_gate.py --dry-run \
  --results results/<run-id>/results.json \
  --baseline baselines/phase7-miniset-haiku-low.json \
  --out-comment results/comment.md
```

**Baseline regeneration is manual.** CI never writes the baseline. To
regenerate it deliberately, run the gate settings from the committed code,
then write the baseline and review the diff before committing:

```bash
set -a; source .env; set +a
PYTHONPATH=src:scripts python scripts/eval_gate.py \
  --out results --run-id phase7-baseline-<date> \
  --out-comment results/phase7-baseline-<date>/comment.md

PYTHONPATH=src:scripts python scripts/eval_gate.py --write-baseline \
  --results results/phase7-baseline-<date>/results.json \
  --baseline baselines/phase7-miniset-haiku-low.json --force

git diff baselines/phase7-miniset-haiku-low.json
```

The baseline records the model id, effort, `max_tokens`, MATCH_SPEC version,
pricing file, mini-set path and SHA-256, per-case exact/partial scores, and
list-price cost. The comment marks a run as not comparable when any of those
differ.

## Ground rules (from the resume quest)

- Nothing is claimed on the resume until it's merged and measured.
- Every before-number must pre-exist (git history, config) or be runnable once, cheaply.
- Small-n claims carry 95% confidence intervals or paired comparisons.
- Held-out tools are never touched as gold/answer tools during development.
  Candidate tools may appear as distractors in dev rows; that exposure is
  counted and disclosed, not banned (`docs/CANDIDATES.md`).
- `docs/FAILURE_TAXONOMY.md` is a descriptive diagnostic vocabulary, not a
  filter or an exclusion list.

<!-- PHASE6-RESULTS:START -->
## Held-out results (Phase 6)

I ran one pre-registered pass over 936 cases (8 tools × 117), k = 1, MATCH_SPEC v1.1. Accuracy is an estimate, not a pass/fail verdict. Cost parity is the one binary rule.

| Question | Result |
|---|---|
| Primary: haiku-agent − sonnet-direct | **-4.06 pts** [-13.10, +4.98] (tool-clustered t, df 7) |
| Secondary: haiku-direct − sonnet-direct | **-4.27 pts** [-12.97, +4.43] (estimate and interval only) |
| Cost ratio, haiku-agent / sonnet-direct | **[0.048392, 0.055169]**, threshold 0.10 → **parity holds** |
| Phase 5 judge calibration | κ **undefined** (one-class labels), raw agreement 30/30, n = 30 traces over 15 tasks, ship rule **not met**; human intra-rater ceiling **not measured** |

Full report: `docs/RESULTS.md`.
<!-- PHASE6-RESULTS:END -->

The held-out run's raw outputs stay gitignored. The committed report, the
table above, and the Phase 6 budget actuals are generated from the run's
`results.json` by:

```bash
PYTHONPATH=src:scripts python scripts/phase6_report.py \
  --run-dir results/phase6-heldout-20261031 \
  --analysis-out docs/RESULTS.json
```

`docs/RESULTS.json` is the committed aggregate contract; the run's raw
`results.json` stays gitignored.

This is a local post-run step; CI does not need the raw results.
