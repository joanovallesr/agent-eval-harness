# Match Spec — Agent + Eval Harness

Version: **1.1 — final for this revision**. This document is the
implementable specification the scorer is built from. It is **not
pre-registered**: pre-registration is a separate, later step
(`docs/PRE_REGISTRATION.md`, committed and git-tagged) and this file makes no
pre-registration claim. Changes after pre-registration require a new version
and do not apply retroactively to test results.

Scope: this spec defines how a model's predicted tool calls are scored
against gold tool calls for xLAM-60k single-turn cases. It is the sole
definition of exact match and partial credit. The scorer implements this
spec and nothing else. It does not consult tool schemas, gold labels beyond
the gold calls for the case being scored, or any LLM judge.

## 1. Inputs, attempts, and gold normalization

### 1.1 Attempts

- An **attempt** is one assistant message.
- The **final attempt** is the last assistant message only. Only that
  message's `tool_use` blocks are scored; `tool_use` blocks from earlier
  attempts are discarded.
- `haiku-agent`: a text-only reply is a validation failure and triggers the
  schema-repair retry (cap 2, EVAL_DESIGN §3). After the cap, the last
  attempt is final.
- Direct arms (`sonnet-direct`, `haiku-direct`): one attempt, no retries.
  A text-only reply is terminal and scores 0.

### 1.2 Pinned tool-use interface

Every arm uses the pinned Anthropic `tool_use` interface (EVAL_DESIGN §2).
Consequences, stated explicitly:

- A predicted call is one `tool_use` block.
- Its **arguments are the block's `input` object**. Other block fields
  (`id`, `type`, `name` handling below) are not arguments and are ignored.
- Text is not a tool call. A reply that encodes calls as text JSON scores
  0 **by construction**, because it contains no `tool_use` block.
- If at least one `tool_use` block exists, any text beside those blocks is
  ignored for scoring. It can carry no credit and no penalty.

### 1.3 Gold normalization

Gold is the case's `answers` field. It is parsed from its JSON string with
strict `json.loads` (`parse_float=Decimal`; see §4), and duplicate JSON keys
follow `json.loads` last-wins semantics.

A gold row is **excluded from both arms, counted, and reported before the
split is materialized** if any of the following holds:

- the `answers` string fails to parse;
- the parsed value is not a non-empty list;
- a gold element is not an object;
- a gold element has a missing `name`, a non-string `name`, or an empty-string
  `name`;
- a gold element's `arguments` is missing, or is not a JSON object, including
  a string-encoded arguments value.

Exclusion is a pre-split row filter, never a per-arm post-hoc exclusion.
There is no scoring fallback for such a row.

**Verified against the pinned revision** (`26d14ebfe18b1f7b524bd39b404b50af5dc97866`,
60,000 rows): zero rows have a missing name, a non-string/empty name, a
non-object `arguments` value, string-encoded arguments, or an empty `answers`
list. The exclusion rule is therefore defensive; it is not expected to fire
on this revision. Even so, it is specified as exclusion rather than silently
scoring 0, so a future revision cannot import malformed gold as a "wrong
prediction".

A normalized gold call is exactly `(name, arguments)`:
`name` is the string as given; `arguments` is the parsed JSON object exactly
as given. No default filling, schema lookup, key removal, or coercion is
applied to gold.

## 2. Format failures

- If the final attempt contains no `tool_use` blocks, the case scores 0 on
  exact match and 0 on partial credit. A text-only reply and a reply whose
  calls are text JSON are both this case.
- A block is **malformed** if it is not an object, or if it has
  `type == "tool_use"` but its `name` is not a non-empty string or its
  `input` is not an object. An empty string is not a valid tool name.
- **Any malformed block forces exact match to 0**, even if another block in
  the same attempt matches gold exactly. A malformed block contributes 0 to
  partial credit and counts toward the predicted-call count in the partial
  denominator (§7).
- Blocks that are not `tool_use` (for example `text` or `thinking` blocks)
  are ignored. They are not calls and do not count in either denominator.
- Format-failure rate per arm is reported as a separate diagnostic, per arm:
  no `tool_use` blocks, malformed blocks, and schema-validation failures
  after retries are exhausted. It is not part of the primary metric and does
  not replace the score (EVAL_DESIGN §4).

## 3. Call-set matching

- Matching is order-insensitive and multiset-based. Two gold calls to the
  same tool with different arguments are two separate calls and must each be
  matched one-to-one.
- Extra predicted calls and missing gold calls both make exact match 0. They
  reduce partial credit through the denominator in §7.
- Pairing between predicted and gold calls is **any exact optimal one-to-one
  assignment**: the assignment that maximizes the sum of per-call scores in
  §7. Any algorithm that returns an exact optimum is permitted; brute-force
  permutation is not required. Greedy pairing is not permitted, because a
  greedy choice can pair same-name calls incorrectly and under-score a
  correct output. Case sizes are tiny, so an exact optimum is cheap.
- Per-call scores and the partial-credit denominator are defined in §7, not
  in this section.

## 4. Argument value comparison

Values are compared deeply, under JSON-type-strict rules:

- **Strings:** exact, character for character, including case and
  whitespace. No case-insensitive, trimming, punctuation, or Unicode
  normalization rule. Comparison is by Unicode codepoint; canonically
  equivalent spellings are different strings.
- **Numbers:** exact by Decimal value, regardless of int/float origin.
  `5` equals `5.0`. Gold is parsed with `parse_float=Decimal`. Predicted
  numbers arrive from the SDK as Python `int` or `float`; convert them with
  `Decimal(int)` for ints and `Decimal(str(x))` for floats. Converting a
  float through `str` avoids binary-float artifacts, so `0.1` equals `0.1`.
  Check `bool` **before** `int`, because in Python `True == 1` and
  `isinstance(True, int)` is true.
- **Non-finite numbers:** reject `NaN` and `Infinity` in gold at parse time
  with `json.loads(..., parse_constant=<reject>)`. A predicted non-finite
  float never equals any value. It is not a number match.
- **Booleans:** match only booleans. `True` does not equal `1`, and `False`
  does not equal `0`.
- **Null:** matches only null.
- **Lists:** compare element by element, recursively, with list order
  significant. This choice is disclosed as arbitrary; order-insensitive list
  comparison is not used.
- **Objects:** compare recursively, key by key. Key order is irrelevant, but
  **key sets must be identical at every depth**. A nested object with an
  extra or missing key is unequal, not partially equal.
- Strings never equal numbers, numbers never equal booleans, booleans never
  equal numbers, and no other coercion is applied. In particular, `"5"` does
  not equal `5`.

## 5. Missing and extra arguments

- The scorer never consults a tool schema. It does not know which arguments
  are optional or have defaults, and it does not use that information even
  if a schema happens to list it.
- Argument dictionaries are compared exactly as given. If gold contains a
  key and the model omits it, that key is **unequal**. It is not skipped.
  If both omit a key, there is nothing to compare for that key.
- If the model supplies a key gold does not contain, that key is **unequal**,
  including keys whose value equals a schema default — defaults cannot be
  verified without the schema, so they are not treated specially.
- Consequence, disclosed: exact match is stricter than execution success. A
  call that would run fine with a default filled in still fails exact match
  if gold included that argument and the model did not. That strictness is
  intentional and conservative for a non-inferiority claim.

## 6. Exact match

A case scores 1 if and only if all of the following hold:

1. The final attempt contains at least one well-formed `tool_use` block and
   no malformed block (see §2).
2. The number of predicted calls equals the number of gold calls.
3. There is a one-to-one pairing of predicted and gold calls such that, for
   each pair, the names are identical strings (case-sensitive) and the
   argument objects have identical key sets at every depth.
4. For every key in every pair, the values are deeply equal under §4.

Anything else scores 0: no `tool_use` blocks, any malformed block, text JSON,
unmatched or extra calls, missing or extra arguments, a nested key-set
mismatch, or any value mismatch.

Primary metric: mean exact-match accuracy, with repeats scored independently
and averaged within a case before any paired difference is computed.

## 7. Partial credit (secondary only)

Partial credit never rescues a failed primary result. It is supporting
evidence only, and it is specified here so it cannot be redefined after
seeing results.

Per call, with predicted call `p` and gold call `g`:

- If `name(p) != name(g)`, `s(p, g) = 0`.
- Otherwise, let `K` be the union of **top-level** argument keys in `p` and
  `g`. `s(p, g)` = (number of keys in `K` whose values are exactly equal
  under §4) / `|K|`. A key present on only one side is unequal. Nested
  equality is decided by §4, including identical nested key sets. If `K` is
  empty (both have no arguments), `s = 1`.
- Partial credit counts top-level keys only; it never grants credit for
  matching nested sub-keys inside a top-level key that is not deeply equal.

Per case:

- Use the optimal one-to-one assignment from §3, maximizing `Σs`. Unmatched
  predicted or gold calls contribute 0 to `Σs`.
- Case score = `2 · Σs / (|pred| + |gold|)`. This is the Dice/F1-style
  formula; it is not the plain mean of per-call scores when call counts
  differ.
- An empty predicted list scores 0. An empty gold list is excluded under
  §1.3, not scored.
- Malformed blocks contribute 0 to `Σs` and count in `|pred|`.
- The case score equals 1 if and only if the case is an exact match under
  §6. A wrong tool earns no argument credit for that call.
- Calls are weighted equally, regardless of how many arguments they have.
  Argument-count weighting is not used.

Secondary diagnostics, reported separately and not combined into partial
credit:

- **Name-only accuracy:** fraction of cases where the multiset of predicted
  tool names equals the multiset of gold tool names. A malformed block or an
  empty predicted list makes it false.
- **Lenient-string variant:** identical to §§4–7, except **argument string
  values only** are compared after stripping surrounding whitespace
  (`str.strip()`) and lowercasing (`str.lower()`, not `str.casefold()`).
  Tool names and argument keys stay strict. No Unicode normalization is
  applied. Example: argument `" beta "` vs gold `"beta"` is 0 under strict
  and 1 under lenient. This variant is secondary only and cannot change the
  primary result.

## 8. Scorer vs loop validator (do not conflate)

- The scorer (this spec) is schema-free. It never reads a tool's
  `input_schema`.
- The agent loop validator (EVAL_DESIGN §3) is schema-aware: it validates
  types, required fields, and enums against the schema to decide retries. It
  never consults gold answers or this scorer.
- A call can therefore pass loop validation (it is executable) and still
  fail exact match (it does not match gold). That distinction is
  intentional: validation measures executability, scoring measures fidelity
  to gold.
- **Precedence (EVAL_DESIGN §4):** a schema-validation failure after retries
  are exhausted scores 0 even if the emitted call happens to equal gold.
  That combination signals a schema/gold inconsistency and must be
  investigated. The format-failure rate per arm is reported as a diagnostic
  alongside that score, not instead of it.
- **Retry kinds are distinct:** infrastructure retries (API 5xx, timeout)
  apply identically to every arm; schema-repair retries apply to the agent
  loop only, and direct arms get none.

## 9. Required pair tests (CI, before any real scoring)

The scorer must pass hand-made right/wrong pairs covering every rule above,
including adversarial near-misses, before it scores any real case. Exact
partial values are asserted, not just "less than 1". Minimum pairs:

- Correct call, exact: exact 1, partial 1.
- Same call, order of calls swapped in a two-call case: exact 1.
- Same tool twice with different arguments, model emits only one: exact 0,
  partial 2/3.
- Extra call added, missing call dropped (two predicted, two gold, one
  match): exact 0, partial 1/2.
- Tool name case change (`Get_Product` vs `get_product`): exact 0,
  per-call s = 0.
- String `"5"` vs integer `5`: exact 0.
- Integer `5` vs float `5.0`: exact 1.
- Float `0.1` vs gold parsed `Decimal("0.1")`: exact 1.
- Boolean `True` vs integer `1`: exact 0.
- Boolean `False` vs integer `0`: exact 0.
- Null vs null: exact 1. Null vs string `"null"`: exact 0.
- String case change (`Beta` vs `beta`): exact 0, lenient variant 1.
- String whitespace change (`" beta "` vs `"beta"`): exact 0, lenient
  variant 1.
- Missing argument gold contains: exact 0; with two top-level gold keys and
  one missing, partial 1/2.
- Extra argument gold does not contain: exact 0.
- Argument value wrong, tool right, two top-level keys one wrong: exact 0,
  partial 1/2.
- List argument with elements reordered: exact 0.
- Nested object with an extra key: exact 0; single top-level key therefore
  partial 0. With two top-level keys and one nested mismatch: partial 1/2.
- Empty predicted list: exact 0, partial 0.
- Malformed / missing tool-use block: exact 0, partial 0.
- Malformed block alongside a valid matching block: exact 0, partial 2/3.
- Zero-argument calls (both sides `{}`): per-call s = 1, exact 1, partial 1.
- Repeated identical gold calls (multiset multiplicity): two gold and two
  identical predictions score exact 1; one prediction scores exact 0,
  partial 2/3.
- F1 with unequal counts: one gold, two predictions with one match scores
  partial 2/3; two gold, one prediction with one match scores partial 2/3.
- Duplicate JSON keys in gold: `json.loads` last-wins, asserted.
- Lenient keeps names and keys strict: a tool-name case change is still 0
  under lenient; an argument-key case change is still 0.
- No Unicode normalization: precomposed `é` vs decomposed `e` + combining
  acute are unequal under strict and under lenient.
- Empty-string tool name in gold is excluded; in a predicted block it is
  malformed and forces exact 0.
- NaN/Infinity in gold is rejected at parse time.

## 10. Versioning

Version 1.1 is final for this revision. It is the version the scorer
implements and the version pre-registration will cite. It is not itself
pre-registered and must not be described as such. Any change after
pre-registration is a new version and does not apply retroactively.
