"""LLM-as-judge for scoring agent traces.

NON-NEGOTIABLE: the judge model must be from a DIFFERENT model family than the
agent's model. A model grading its own family's traces is a conflict of
interest — see docs/EVAL_DESIGN.md §9.

Labels: correct / wrong / partial. The judge sees the task, the expected
answer, and the agent's trace (tool calls + final answer).

TODO(phase-5): implement judge_trace() — prompt template, strict output
parsing (one of the three labels + one-line rationale), retry on parse
failure.
TODO(phase-5): the judge prompt itself is versioned; changes to it require
re-running calibration (calibration.py) before the judge scores anything.
"""

from __future__ import annotations

from typing import Literal

Verdict = Literal["correct", "wrong", "partial"]

JUDGE_PROMPT_VERSION = "v0-unimplemented"


def judge_trace(task: dict, trace) -> tuple[Verdict, str]:
    """Score one trace. Returns (verdict, one-line rationale)."""
    raise NotImplementedError(
        "Phase 5: implement the judge (different model family!)"
    )
