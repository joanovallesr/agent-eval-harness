# Phase 5 — Judge Calibration

Status: Part 2 complete. This is the secondary multi-step question only. The
judge plays no role in the pre-registered single-turn test.

## Design and inputs

- Judge model: `gpt-6-luna` (GPT-6 Luna), a different model family from the
  trace models.
- Judge prompt: `phase5-judge-v1`, implemented in `src/eval/judge.py`.
- Rubric: `docs/MULTISTEP_RUBRIC.md`, version `phase5-rubric-v1`, written
  before any label.
- Human labels: 60 labels in the blind packet, 15 tune tasks / 15 report tasks
  (30 traces each), split by task.
- Trace packet: `results/phase5-labeling-20261031/packet/` (gitignored).
- Private split/arm mapping: `results/phase5-labeling-20261031/private/`
  (gitignored).
- Judge outputs: `results/phase5-judge-20261031/` (gitignored).

All 60 human labels are `correct`. That is the actual label set; I did not
change it. It makes κ degenerate, and that is the main result below.

## Tune half and the revision round

The tune half was judged first with prompt v1.

| Metric | Value |
|---|---:|
| Traces | 30 |
| Human labels | 30 correct |
| Judge labels | 30 correct |
| Raw agreement | 1.00 (30/30) |
| Disagreements | 0 |
| Cohen's κ | undefined (0/0) |

There were no disagreements to diagnose, so no revision of the prompt or
rubric was justified. Prompt v1 was frozen for the report half. I did not use
the report half to tune anything.

## Report half — frozen judge v1

| Metric | Value |
|---|---:|
| Traces | 30 |
| Human labels | 30 correct |
| Judge labels | 30 correct |
| Raw agreement | **1.00 (30/30)** |
| Raw agreement 95% Wilson interval | [0.886, 1.000] (trace-level description, not a κ interval) |
| Raw agreement task-cluster bootstrap | [1.000, 1.000] (15 tasks; degenerate because every label is correct) |
| Cohen's κ | **undefined (0/0)** |
| 95% CI for κ | **undefined** |
| Confusion matrix | 30 on the correct/correct cell, 0 elsewhere |

κ is undefined because both raters used a single label and expected agreement
is 1. If a convention assigns 0 to the degenerate case, the point estimate is
0.00, which is still below the 0.70 ship rule. Either way, the ship rule is
not met.

**Ship-rule outcome: miss.** I am reporting it as a published negative result,
not as a calibrated judge. The raw agreement is perfect, but a one-class label
set cannot demonstrate discrimination; the effective sample is 15 tasks, not
60 independent traces, because the two traces per task share a task.

## Per-arm judge summaries — secondary only

These are judge rates on the report half, not headline results. No pass/fail
framing.

| Arm | Traces | Judge correct | Judge partial | Judge wrong | Judge correct rate (95% Wilson) |
|---|---:|---:|---:|---:|---|
| `haiku-agent` | 15 | 15 | 0 | 0 | 1.00 [0.796, 1.000] |
| `sonnet-agent` | 15 | 15 | 0 | 0 | 1.00 [0.796, 1.000] |

The human labels are also 15/15 correct in each arm. With no failures in
either class, this comparison carries no signal.

## Blind relabel batch and actual gap

The separate blind batch used 15 report traces (8 `haiku-agent`, 7
`sonnet-agent`), new `relabel-` ids, a new randomized order (seed `20261032`),
and the frozen prompt. The batch contained no original trace ids, model
identity, or earlier judge output.

| Comparison | n | Raw agreement | Raw agreement 95% Wilson | κ |
|---|---:|---:|---|---|
| Judge report labels vs judge relabel labels | 15 | 1.00 (15/15) | [0.796, 1.000] | undefined |
| Human labels vs judge relabel labels | 15 | 1.00 (15/15) | [0.796, 1.000] | undefined |

The 15 relabel traces come from 10 tasks, so the Wilson interval is
trace-level and descriptive; a task-cluster bootstrap is degenerate at
[1.000, 1.000] because every relabel is correct.

Actual gap:

| Clock | Time |
|---|---|
| Human labels file last written | 2026-11-01T02:50:07Z |
| Original report-half judge file last written | 2026-11-01T02:55:58Z |
| Blind relabel judge file last written | 2026-11-01T02:57:57Z |
| Human labels to relabel | **469.7 s (7.8 min)** |
| Report judge to relabel | **119.2 s (2.0 min)** |

The gap is computed from file mtimes; no separate completion timestamps were
supplied. The design called for a one-week gap before relabeling. The actual
gap was minutes, not a week, and I am recording that rather than hiding it.

The brief specified the relabel batch be run by the judge; the design text
describes a human relabel as "our own consistency." I ran the judge relabel
batch because no second human labels existed. It measures judge
self-consistency, not human intra-rater consistency. **The human intra-rater
ceiling is not measured.** I generated a blank human relabel packet at
`results/phase5-labeling-20261031/relabel-packet/` with its private mapping at
`results/phase5-labeling-20261031/private/relabel-mapping.json`. With all
human labels in one class, a human relabel κ would also be undefined; filling
that packet is the only way to measure the design's human ceiling.

## Judge cost and Phase 5 budget

| Judge run | Calls | Input tokens | Output tokens | Cost |
|---|---:|---:|---:|---:|
| Tune v1 | 30 | 67,420 | 2,574 | $0.008029 |
| Report v1 frozen | 30 | 68,669 | 2,965 | $0.008349 |
| Blind relabel | 15 | 34,507 | 2,008 | $0.004455 |
| Model availability smoke | 1 | 13 | 4 | $0.000003 |
| **Logged completed calls** | **76** | **170,609** | **7,551** | **$0.020836** |

The smoke call is logged in
`results/phase5-judge-20261031/model-smoke.jsonl`. One additional relabel
request returned an API error at the output cap before a usage record was
written; it is logged in
`results/phase5-judge-20261031/failed-requests.jsonl` with zero recorded cost.
The provider may bill it, and the amount is below one cent. I raised the output
cap from 300 to 1,000 tokens after that error and re-ran the relabel batch. The
prompt and rubric stayed at `phase5-judge-v1`; only the cap changed. The tune
and report labels were unaffected because no completed response hit the cap.

Each judge invocation used a stricter ceiling than the remaining Phase 5
budget: $0.05 for tune, $0.05 for report, and $0.03 for relabel, totaling
$0.13 of authorized ceiling against the $0.212986 remainder. The runner now
accepts `--prior-cost` so the guard can be cumulative across judge runs.

Phase 5 actuals:

| Line | Amount |
|---|---:|
| Multi-step traces (Part 1) | $1.287014 |
| Judge calls (Part 2) | $0.020836 |
| **Phase 5 total** | **$1.307851** |
| Phase 5 ceiling | $1.500000 |
| Remaining under the ceiling | **$0.192149** |

The judge fit inside the $0.212986 that Part 1 left. The ledger now totals
**$3.765892**; the pre-registration later-phase allowance of $2.000000 still
has $0.692149 unused. The total is computed from unrounded run costs; the
displayed terms are rounded to six decimals, so adding them can differ by
$0.000001. The test inequality on actuals so far is

```text
20.00 − 2.458041 (through Phase 3) − 1.307851 (Phase 5)
      − 5.972325 (reserve) = 10.261783 ≥ 3.288878 (test spend)  ✓
```

## What this result says

- The judge agreed with every human label in the tune half, the report half,
  and the blind relabel batch.
- That is perfect raw agreement, but it is not evidence of a calibrated
  judge. With 60/60 human labels `correct`, κ has no class variation to work
  with and is undefined. The ship rule is a miss.
- The honest resume line is: **κ undefined (one-class labels), n = 30 report
  traces over 15 tasks, raw agreement 30/30; ship rule not met.**
- The next useful change is task difficulty: the multi-step set needs traces
  that fail in varied ways before another κ calibration can say anything.
  More judge prompt tuning against an all-correct label set would only fit
  noise.

## Reproduce

```bash
# Judge the tune half.
PYTHONPATH=src python -m eval.judge \
  --packet-dir results/phase5-labeling-20261031/packet \
  --mapping results/phase5-labeling-20261031/private/mapping.json \
  --rubric docs/MULTISTEP_RUBRIC.md \
  --out results/phase5-judge-20261031/tune-v1.jsonl \
  --split tune --pricing-file config/pricing.judge.2026-10-23.json \
  --cost-ceiling 0.05

# Judge the frozen report half.
PYTHONPATH=src python -m eval.judge ... --split report \
  --out results/phase5-judge-20261031/report-v1-frozen.jsonl

# Calibration and relabel analysis.
PYTHONPATH=src python -m eval.calibration \
  --labels results/phase5-labeling-20261031/packet/labels.jsonl \
  --judge results/phase5-judge-20261031/report-v1-frozen.jsonl \
  --mapping results/phase5-labeling-20261031/private/mapping.json \
  --split report --out results/phase5-judge-20261031/report-v1-analysis.json

PYTHONPATH=src:scripts python scripts/analyze_relabel.py \
  --original-judge results/phase5-judge-20261031/report-v1-frozen.jsonl \
  --relabel-judge results/phase5-judge-20261031/relabel-judge-v1.jsonl \
  --relabel-mapping results/phase5-labeling-20261031/private/relabel-mapping.json \
  --labels results/phase5-labeling-20261031/packet/labels.jsonl \
  --out results/phase5-judge-20261031/relabel-analysis.json
```
