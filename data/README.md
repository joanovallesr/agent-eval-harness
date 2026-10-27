# Data

Benchmark subsets live here. They are **gitignored** — large files and frozen
splits shouldn't churn in the repo. What's committed instead: the revision
pin, the checksums, and the license/terms verdict.

## Sources

1. **xLAM-60k** (Salesforce) — 60,000 tool-calling entries, 3,605 distinct
   tool names in the pinned revision, 21 API categories. The categories are
   the paper's descriptive taxonomy only; the **split and cluster unit is the
   tool**. Load with
   `datasets.load_dataset("Salesforce/xlam-function-calling-60k", revision="<pinned-hash>")`.
   Access granted 2026-10-23 (click-through; gated: auto).
2. **Hand-written mini-set** (`miniset.jsonl`, ~20 tasks) — pipeline smoke
   test and multi-step seed. Excluded from headline numbers.
3. **Hand-written multi-step set** (`multistep.jsonl`, ~30 tasks, each with
   deterministic mock tools) — the judge's territory.

The xLAM-7b-fc-r checkpoint and the cost-aware-router outcomes were dropped
from the design in the 2026-10-23 grill (see `docs/EVAL_DESIGN.md`).

## Revision pin

Pin the exact dataset revision at download. Reference (2026-10-23):
`26d14ebfe18b1f7b524bd39b404b50af5dc97866` (last modified 2025-01-24).
Bare "xLAM-60k" is not a fixed artifact — the July 2024 regeneration changed
rows.

```
PINNED_REVISION=26d14ebfe18b1f7b524bd39b404b50af5dc97866
```

## License / republication verdict

- [ ] Dataset-card license read (Hub tag reads `cc-by-4.0` — unverified).
- [ ] Click-through use terms read.
- [ ] Verdict on publishing sampled traces recorded here before any dataset
      row appears on a public site or repo.

## Splits

Split by **tool**, never randomly. A held-out tool never appears as a
gold/answer tool in dev. Candidate tools **may** appear as distractors in a
dev row's `tools` list; that exposure is counted per candidate and disclosed,
and it does **not** trigger replacement. The frozen candidate order, seed,
sha256, eligibility filters, and per-candidate counts are in
`docs/CANDIDATES.md`; rerun `scripts/select_candidates.py` to reproduce them.

Invalid-row filters (EVAL_DESIGN §5) are applied **before** the split
freezes: answer-tools only, bad/missing answer names, empty answers, missing
required arguments, and mixed candidate+non-candidate cases (excluded and
counted). Phase 0 on the pinned revision found 71 eligible tools with at
least 100 single-tool cases; all 12 frozen candidates clear the floor after
every filter.

| File | Content |
|---|---|
| `xlam60k_dev.jsonl` | dev tools — tune freely here |
| `xlam60k_test_heldout.jsonl` | held-out tools — run once, no tuning |
| `miniset.jsonl` | hand-written smoke tasks |
| `multistep.jsonl` | hand-written multi-step tasks + mock tools |
| `multistep_labels_tune.jsonl` | ~30 blind human labels (judge tuning) |
| `multistep_labels_report.jsonl` | ~30 blind human labels (judge report — untouched until final) |

Record sha256 checksums here once the files are frozen:

```
# TODO(phase-4): paste checksums after freezing the splits
```
