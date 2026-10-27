# Match Spec — Agent + Eval Harness

Version: 1 (draft — to be pinned in `docs/PRE_REGISTRATION.md` before the
held-out run; changes after the tag require a new version and do not apply
retroactively to test results)

Scope: this spec defines how a model's predicted tool calls are scored
against gold tool calls for xLAM-60k single-turn cases. It is the sole
definition of exact match and partial credit. The scorer implements this
spec and nothing else. It does not consult tool schemas, gold labels beyond
the gold calls for the case being scored, or any LLM judge.

## 1. Inputs

- Gold: the case's `answers` field, parsed from its JSON string with
  strict `json.loads`, floats parsed as `Decimal`. A gold row whose
  answers fail to parse, or whose answer list is empty, is excluded from
  both arms, counted, and reported. Exclusion happens before the split
  is materialized, never per-arm after the fact.
- Predicted: the model's final attempt only. For `haiku-agent`, that is
  the attempt after schema-validation retries are exhausted (cap 2).
  For the direct arms, that is the single attempt, with no retries.
- A predicted call is one Anthropic `tool_use` block, with a string
  `name` and an object `arguments` (the block's `input`). Text output
  with no `tool_use` block is an empty predicted list.

## 2. Format failures

- If the final attempt contains no `tool_use` blocks, the case scores
  0 on exact match and 0 on partial credit.
- A `tool_use` block whose name is not a string, or whose arguments are
  not an object, counts as a predicted call that matches nothing: it
  contributes 0 to partial credit and counts toward the predicted call
  count in the partial-credit denominator.
- Format-failure rate per arm (no tool-use blocks, malformed blocks,
  schema-validation failures after retries) is reported as a separate
  diagnostic, per arm. It is not part of the primary metric.

## 3. Call-set matching

- Matching is order-insensitive and multiset-based. Two gold calls to
  the same tool with different arguments are two separate calls and
  must each be matched one-to-one.
- Extra predicted calls and missing gold calls both make exact match 0.
  They reduce partial credit through the denominator in §6.
- Pairing between predicted and gold calls is the one-to-one assignment
  that maximizes the sum of per-call scores in §6. Brute-force
  permutation is required; greedy pairing is not allowed, because
  greedy pairing can pair same-name calls wrong and under-score a
  correct output. Case sizes are tiny, so brute force is cheap.

## 4. Argument value comparison

Values are compared deeply, under JSON-type-strict rules:

- Strings: exact, character for character, including case and
  whitespace. No case-insensitive, trimming, or punctuation rule.
- Numbers: exact by Decimal value, regardless of int/float origin.
  `5` equals `5.0`. Parsing with `parse_float=Decimal` avoids
  binary-float artifacts, so `0.1` equals `0.1`.
- Booleans: match only booleans. `True` does not equal `1`, and
  `False` does not equal `0`. Implementations must check for bool
  before int, because in Python `True == 1`.
- Null: matches only null.
- Lists: compare element by element, recursively, with list order
  significant. This choice is pre-registered and disclosed as
  arbitrary; order-insensitive list comparison is not used.
- Objects: compare recursively, key by key. Key order is irrelevant.
- Strings never equal numbers, numbers never equal booleans, and no
  other coercion is applied. In particular, `"5"` does not equal `5`.

## 5. Missing and extra arguments

- The scorer never consults a tool schema. It does not know which
  arguments are optional or have defaults, and it does not use that
  information even if a schema happens to list it.
- Argument dictionaries are compared exactly as given. If gold
  contains a key and the model omits it, that call fails exact match.
  If both omit a key, there is nothing to compare for that key.
- If the model supplies a key gold does not contain, that call fails
  exact match, including keys whose value equals a schema default —
  defaults cannot be verified without the schema, so they are not
  treated specially.
- Consequence, disclosed: exact match is stricter than execution
  success. A call that would run fine with a default filled in still
  fails exact match if gold included that argument and the model did
  not. That strictness is intentional and conservative for a
  non-inferiority claim.

## 6. Exact match

A case scores 1 if and only if all of the following hold:

1. The final attempt contains at least one well-formed `tool_use`
   block (see §2).
2. The number of predicted calls equals the number of gold calls.
3. There is a one-to-one pairing of predicted and gold calls such
   that, for each pair, the names are identical strings
   (case-sensitive) and the argument objects have identical key sets.
4. For every key in every pair, the values are deeply equal under §4.

Anything else scores 0: no tool-use blocks, malformed blocks, unmatched
or extra calls, missing or extra arguments, or any value mismatch.

Primary metric: mean exact-match accuracy, with repeats scored
independently and averaged within a case before any paired difference
is computed.

## 7. Partial credit (secondary only)

Partial credit never rescues a failed primary result. It is supporting
evidence only, and it is pre-registered here so it cannot be redefined
after seeing results.

Per call, with predicted call p and gold call g:

- If name(p) != name(g), s(p, g) = 0.
- Otherwise, let K be the union of argument keys in p and g.
  s(p, g) = (number of keys in K whose values are exactly equal
  under §4) / |K|. If K is empty (both have no arguments), s = 1.

Per case:

- Use the optimal one-to-one assignment from §3, maximizing Σs.
  Unmatched predicted or gold calls contribute 0 to Σs.
- Case score = 2 · Σs / (|pred| + |gold|).
- An empty predicted list scores 0. An empty gold list is excluded
  under §1, not scored.
- The case score equals 1 if and only if the case is an exact match
  under §6. A wrong tool earns no argument credit for that call.
- Calls are weighted equally, regardless of how many arguments they
  have. Argument-count weighting is not used.

Secondary diagnostics, reported separately and not combined into
partial credit:

- Name-only accuracy: fraction of cases where the multiset of
  predicted tool names equals the multiset of gold tool names.
- Lenient-string variant: identical to §§4–7, except strings are
  compared after stripping surrounding whitespace and lowercasing.
  This variant is secondary only and cannot change the primary result.

## 8. Scorer vs loop validator (do not conflate)

- The scorer (this spec) is schema-free. It never reads a tool's
  `input_schema`.
- The agent loop validator (EVAL_DESIGN §3) is schema-aware: it
  validates types, required fields, and enums against the schema to
  decide retries. It never consults gold answers or this scorer.
- A call can therefore pass loop validation (it is executable) and
  still fail exact match (it does not match gold). That distinction
  is intentional: validation measures executability, scoring measures
  fidelity to gold.

## 9. Required pair tests (CI, before any real scoring)

The scorer must pass hand-made right/wrong pairs covering every rule
above, including adversarial near-misses, before it scores any real
case. Minimum pairs:

- Correct call, exact: scores 1 / partial 1.
- Same call, order of calls swapped in a two-call case: scores 1.
- Same tool twice with different arguments, model emits only one:
  exact 0, partial < 1.
- Extra call added, missing call dropped: exact 0, partial < 1.
- Tool name case change (`Get_Product` vs `get_product`): exact 0,
  per-call s = 0.
- String `"5"` vs integer `5`: exact 0.
- Integer `5` vs float `5.0`: exact 1.
- Boolean `True` vs integer `1`: exact 0.
- String case change (`Beta` vs `beta`): exact 0, lenient variant 1.
- String whitespace change (`beta ` vs `beta`): exact 0.
- Missing argument gold contains: exact 0.
- Extra argument gold does not contain: exact 0.
- Argument value wrong, tool right: exact 0, partial between 0 and 1.
- List argument with elements reordered: exact 0.
- Empty predicted list: exact 0, partial 0.
- Malformed / missing tool-use block: exact 0, partial 0.
