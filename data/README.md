# Data

Benchmark subsets live here. They are **gitignored** — large files and frozen
splits shouldn't churn in the repo. What's committed instead: the revision
pin, the checksums, and the license/terms verdict.

## Sources

1. **xLAM-60k** (Salesforce) — 60,000 tool-calling entries, 3,673 APIs,
   21 tool categories. Load with
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
# paste the pinned revision hash here at download:
PINNED_REVISION=
```

## License / republication verdict

- [ ] Dataset-card license read (Hub tag reads `cc-by-4.0` — unverified).
- [ ] Click-through use terms read.
- [ ] Verdict on publishing sampled traces recorded here before any dataset
      row appears on a public site or repo.

## Splits

Split by **tool category**, never randomly. Categories in the held-out set
never appear in dev. Invalid-row filters (EVAL_DESIGN §5) are applied **before**
the split freezes.

| File | Content |
|---|---|
| `xlam60k_dev.jsonl` | dev categories — tune freely here |
| `xlam60k_test_heldout.jsonl` | held-out categories — run once, no tuning |
| `miniset.jsonl` | hand-written smoke tasks |
| `multistep.jsonl` | hand-written multi-step tasks + mock tools |
| `multistep_labels_tune.jsonl` | ~30 blind human labels (judge tuning) |
| `multistep_labels_report.jsonl` | ~30 blind human labels (judge report — untouched until final) |

Record sha256 checksums here once the files are frozen:

```
# TODO(phase-4): paste checksums after freezing the splits
```
