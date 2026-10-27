"""Judge calibration: is the LLM judge trustworthy?

Protocol (docs/EVAL_DESIGN.md section 9) — the judge serves the secondary
multi-step question only:
1. Human-label ~60 multi-step traces (correct / wrong / partial) against a
   written rubric, blind to the producing arm. Split 30 tune / 30 report.
   ~15 traces relabeled a week later for the intra-rater ceiling.
2. One calibration round (one prompt revision, defined up front) against the
   tune set. The report set stays untouched until final scoring.
3. Compute Cohen's kappa WITH its 95% CI, plus the confusion matrix.
4. Ship rule: point-estimate kappa >= 0.70, CI reported alongside. A miss is
   a published negative result — the resume then states measured kappa and n.
5. Re-run this after ANY judge-prompt change — same human labels, new kappa.

TODO(phase-5): implement cohens_kappa(y_human, y_judge), kappa CI, confusion
matrix, and a CLI: python -m src.eval.calibration --labels data/multistep_labels_report.jsonl
"""

from __future__ import annotations

KAPPA_SHIP_THRESHOLD = 0.70


def cohens_kappa(y_human: list[str], y_judge: list[str]) -> float:
    """Cohen's kappa for the three labels correct/wrong/partial."""
    raise NotImplementedError(
        "Phase 5: implement kappa (no sklearn dependency needed)"
    )


def confusion_matrix(y_human: list[str], y_judge: list[str]) -> dict:
    """3x3 confusion matrix, human rows x judge cols."""
    raise NotImplementedError("Phase 5: implement the confusion matrix")
