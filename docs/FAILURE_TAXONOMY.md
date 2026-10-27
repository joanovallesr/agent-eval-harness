# Failure Taxonomy

Every dev-set failure gets one of these labels. The taxonomy is the interesting
artifact — it turns "the score is 78%" into "here's exactly what's wrong and what
to fix." Update the categories as real failures show up; delete ones that never
fire.

## Tool selection
- **wrong-tool** — picked a tool that can't satisfy the request (e.g. weather API
  for a stock price).
- **right-tool-wrong-params** — correct tool, missing or malformed parameters.
- **right-tool-wrong-order** — multi-step task, calls in an order that can't work
  (e.g. booking before checking availability).
- **missing-call** — answered from parametric knowledge instead of calling the tool.
- **extra-call** — called tools the task didn't need (costs money, risks errors).

## Loop behavior
- **early-stop** — declared done with an incomplete answer.
- **loop** — repeated the same failing call instead of adapting.
- **max-iterations** — hit the iteration cap without finishing (may be a cap
  problem, not an agent problem — check before labeling).
- **context-loss** — dropped a tool result from an earlier step.

## Output quality
- **hallucinated-result** — reported a tool output that never happened.
- **format-error** — right content, wrong structure (matters for structured outputs).
- **partial-answer** — correct but incomplete.

## Labeling protocol
1. Label from the trace, not from vibes — link the tool call that went wrong.
2. One primary label per failure; note a secondary if the chain matters.
3. When two labelers disagree, that's data: record the disagreement and tighten
   the category definition.
4. Re-run the taxonomy counts after every agent change. The delta is the story
   ("fixed 6 of 9 wrong-order failures with the planner prompt").
