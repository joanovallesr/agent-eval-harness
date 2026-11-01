# Multi-Step Labeling Rubric

Version: `phase5-rubric-v1` (written before any label). This rubric is used
by me for the human labels and by the judge in calibration. The judge sees the
same task, mock results, success criteria, and trace, with no model identity.

## What a label means

Each trace gets exactly one label: `correct`, `partial`, or `wrong`.

- **correct** — The trace achieves every required outcome in the task's
  success criteria. The values it acts on and reports match the scripted mock
  results. Paraphrasing is fine; extra harmless calls are fine.
- **partial** — The trace achieves at least one material required outcome but
  misses or gets wrong another material element. Examples: it finds the right
  record but never performs the requested action; it performs the right action
  but reports the wrong confirmation code or amount; it completes two of three
  required steps.
- **wrong** — The trace achieves no required outcome, acts on the wrong
  record or amount, invents a result that was never returned, ignores a tool
  error and proceeds on a guess, or ends without completing the task.

## Decision order

1. **Read the task and the success criteria first.** The mock results are the
   world; they define what is true.
2. **Check the final answer.** If there is no final answer and the trace ended
   early (truncated, refusal, iteration cap, schema failure), the label is
   `wrong` unless the trace had already completed every required outcome before
   stopping.
3. **Check each required action and value.** An action counts only if the tool
   call was actually made and matched the scripted world. A correct statement
   in prose without the action does not count.
4. **Apply the label.** All material elements right: `correct`. Some right and
   some missing or wrong: `partial`. None right, or the central action is
   wrong: `wrong`.
5. **Do not reward intent.** A plausible plan with no executed calls is
   `wrong`. A correct first step that stops is `partial` only if that first
   step is itself a required outcome; otherwise it is `wrong`.

## Edge cases

- **Extra calls.** Extra reads or harmless lookups do not reduce a label.
  Extra writes that change state inconsistently with the task make the trace
  `wrong`.
- **Repeated calls.** A retry after a mock result error is fine if the final
  action and answer are right. A loop that never recovers is `wrong`.
- **Paraphrased final answer.** Correct values in different wording are
  `correct`. Missing required values are at most `partial`.
- **Partial refunds and filtered choices.** The task names the item, date,
  amount, or attribute that decides the choice. The wrong choice is `wrong`
  even if the downstream action is well formed.
- **Conditional tasks.** If the condition is met, the conditional action is
  required. If the condition is not met, not acting is correct. The mock
  results decide whether the condition is met.
- **Mock errors.** A `fixture_miss` or tool error is observable in the trace.
  The model may recover by calling differently; if it does not, the missing
  action determines the label.
- **No credit for hidden knowledge.** A value that appears nowhere in the
  mock results and was not computed from them is invented, even if it happens
  to look plausible.

## Blindness and independence

I label each trace on its own. I do not infer which model or arm produced it,
and I do not compare paired traces of the same task. The packet is randomized
and carries no model identity, cost, token count, or judge output.

## Judge use

The judge receives the same rubric text and the same trace contents. Its
labels are calibration data, not the reference. The human labels are the
reference. The judge prompt gets one revision round driven only by tune-half
disagreements, then freezes for the report half.
