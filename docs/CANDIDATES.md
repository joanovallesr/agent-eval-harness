# Held-Out Tool Candidates (Phase 0 Freeze)

Status: **frozen Phase-0 candidate list**. This is not pre-registration. The
final C held-out tools are selected from this ordered list in Phase 4 (C ∈
{6, 7, 8}, EVAL_DESIGN §7). The claim, margin, arms, and cost cap are locked
elsewhere and are not decided here.

- Frozen: **2026-10-26** (design pivot dated 2026-10-25).
- Owner decisions recorded **2026-10-26**; see below.
- Dataset: `Salesforce/xlam-function-calling-60k`.
- Pinned revision: `26d14ebfe18b1f7b524bd39b404b50af5dc97866` (2025-01-24).
- Seed: **42**.
- Selection: `random.Random(42).sample(sorted(eligible_tools), 12)`.
- Order: the sample order below is the frozen replacement order.
- sha256 of `"\n".join(candidates)` (no trailing newline):
  `6b822299090bda4e6dfca52b6db426b7b2b4b5dba14662505962145415623991`.
- Phase-0 generator code commit (frozen order logic unchanged):
  `847a7de4c8e842087ed3a87c12d9b681ac57ce87` (`scripts/select_candidates.py`).

## Decision status (owner, 2026-10-26)

1. **Required-arguments filter — FINAL: description-aware refinement.**
   Exclude only **core-input** missing rows. A missing parameter is **not** a
   core input when (a) its name matches the filter/pagination/sort pattern or
   (b) its description matches the optional/default-language pattern; the name
   check has precedence. The exact regexes are stated verbatim in
   `docs/REQUIRED_FILTER_AUDIT.md` and implemented once in
   `scripts/select_candidates.py` (`categorize_parameter` /
   `normalize_parameters`), which the audit imports. Measured on the pinned
   revision: **38 excluded rows, 43 missing-parameter instances, final
   eligible pool 70 tools**. The interim "no `default` key = required" rule
   (1,316 rows, 63 tools) is **history only**, not the current rule. The
   70 vs 71 structural difference is `integrate` falling 101 → 95 usable
   cases; `integrate` is not a candidate. Candidate counts, frozen order,
   seed 42, and sha256
   `6b822299090bda4e6dfca52b6db426b7b2b4b5dba14662505962145415623991`
   are unchanged.
2. **Sizing inputs `m` and `u` — FINAL as a rule, not a number.** `m` is the
   count of dev tools (non-candidate tools) with **≥10 usable cases in the
   actual Phase 3 dev sample**. `u` is recomputed from that `m` with the §7
   formula `u = √((m−1)/χ²_{0.20, m−1})`. `m`, `u`, `C`, and `n_t` are **not
   locked by this document**: the structural illustration `m = 1,093`,
   `u = 1.019` is only an illustration of the full-dataset count, not a
   Phase-3 value. Concrete `C` and `n_t` still require Phase 3 variance
   components.
3. **Gold-malformation exclusion — FINAL.** A missing/non-string tool name,
   non-object arguments, or string-encoded arguments excludes the row, counted
   before the split and reported. It is a pre-registered exclusion, **not** a
   score of 0. Zero occurrences on the pinned revision (defensive only).
4. **License / republication verdict — rule approved, verdict not approved.**
   No raw dataset rows, queries, answers, full tool schemas, or reproducing
   traces are published until the owner confirms the verdict
   (`data/README.md`).
5. **Frozen order — FINAL.** The order is generated from the **71-tool
   structural pool** (before the required-arguments filter), seed 42, sha256
   `6b822299090bda4e6dfca52b6db426b7b2b4b5dba14662505962145415623991`.
   The final 70-tool usable pool is used for sizing, not to re-roll the order.
   All 12 candidates clear 100 single-tool cases under both pools.

## Eligibility filters

Applied in this order, before the split is materialized:

1. **Answer-tools only.** Eligibility is defined by the gold `answers` field,
   never by distractor appearances in a row's `tools` list.
2. **Structural row filters.** Exclude a row with an empty `answers` list, a
   missing/non-string/empty gold function name, a gold name not present in
   that row's `tools`, non-object arguments, or string-encoded arguments.
   (On the pinned revision: 0 rows fail these checks — defensive only.)
3. **Single-tool cases only.** Every gold call in the row must name the same
   tool. Multi-tool rows are not assigned to a held-out tool. Rows whose gold
   calls mix a candidate with a non-candidate are excluded and counted. Rows
   whose gold calls span more than one candidate tool are multi-tool and are
   not used in the v1 per-tool analysis.
4. **Required-arguments filter — FINAL (description-aware).** A gold call
   that omits a parameter is excluded only when that parameter is a
   **core input**. A missing parameter is not core when its name matches this
   pattern:

   ```
   (?:^|_)(?:filter|filters|limit|offset|page|per_page|sort|sort_by|order|order_by|descending|ascending|skip|cursor|top)(?:$|_)
   ```

   or its description matches this pattern:

   ```
   \boptional\b|\bdefaults?\b|\bdefault\s+(?:is|to|values?)\b|\bif\s+(?:provided|specified|given|omitted|not\s+provided|none)\b|\bnot\s+required\b|\bcan\s+be\b
   ```

   Both use `re.IGNORECASE`, and the name check has precedence. The exact
   rule is stated verbatim and implemented once in
   `scripts/select_candidates.py`, imported by the audit. A parameter with a
   literal `default` key is optional before this rule runs. The pinned
   revision has **168,870 flat tool declarations and zero nested
   declarations**; the public card describes a `required` boolean, but the
   pinned revision has **zero** `required` keys in its 362,482 parameter
   descriptors. That discrepancy is preserved in
   `docs/REQUIRED_FILTER_AUDIT.md`. This final rule removes **38 rows**
   globally (43 missing-parameter instances) and **zero single-tool candidate
   rows**. The interim "no `default` key = required" heuristic (1,316 rows,
   63 tools) is history only.
5. **Eligibility floor.** A tool is eligible when it has **at least 100
   single-tool cases** after filters 1–4 (under the final description-aware
   rule) or, for the structural pool, after filters 1–3.

Distractor exposure is not a filter. A candidate tool may appear in a dev
row's `tools` list without being a gold answer; this is allowed, counted, and
reported below. It never triggers replacement.

## Pool counts (pinned revision)

| Count | Value |
|---|---:|
| Rows | 60,000 |
| Distinct tool names (pre-filter pool) | 3,605 |
| Single-tool rows | 39,552 |
| Multi-tool rows | 20,448 |
| Mixed candidate + non-candidate rows (excluded) | 1,270 |
| Multi-tool rows spanning only candidate tools | 21 |
| Multi-tool rows with no candidate tool | 19,157 |
| Rows removed by the interim heuristic (historical) | 1,316 |
| Rows removed by the FINAL description-aware rule | 38 |
| Missing-parameter instances under the final rule | 43 |
| **Eligible tools, structural pool (≥100 single-tool cases)** | **71** |
| **Eligible tools under the FINAL required-arguments rule** | **70** |
| Eligible tools under the interim heuristic (historical) | 63 |

**Frozen order — FINAL (owner, 2026-10-26).** The order is generated from the
**71-tool structural pool** locked in Phase 0. The final description-aware
rule yields a **70-tool usable pool**; that pool is used for sizing, never to
re-roll the order. The verifier recomputes both pools and asserts that the 12
candidate counts are **identical under both** (all clear 100). The order is
not re-rolled from the 70-tool pool, because the candidate list was frozen
before the required-arguments rule was settled and the instruction is
explicitly not to re-roll candidates. If a candidate had lost cases to this
filter, it would be flagged and a spare would cover it; none did. The 70 vs
71 difference is `integrate` falling 101 → 95 usable cases; `integrate` is not
a candidate. Dropping the filter entirely returns the pool to 71; no candidate
count changes either way. The interim 63-tool pool is history only.

## Frozen candidates (ordered)

Counts are post-filter single-tool usable cases. "Distractor all" counts rows
where the candidate appears in `tools` but not in `answers` across the full
dataset; "distractor dev" restricts to rows whose answer tools are all
non-candidate (dev-eligible). Distractor exposure is disclosed, not banned.

| # | Tool | Usable single-tool cases | Distractor all | Distractor dev |
|---:|---|---:|---:|---:|
| 1 | `calculate_standard_deviation` | 175 | 314 | 295 |
| 2 | `average` | 117 | 281 | 245 |
| 3 | `get_ip_location` | 127 | 276 | 265 |
| 4 | `find_pairs_with_sum` | 128 | 421 | 383 |
| 5 | `find_majority_element` | 124 | 231 | 231 |
| 6 | `chi_square_independence_test` | 176 | 273 | 250 |
| 7 | `calculate_quarterly_dividend` | 119 | 224 | 213 |
| 8 | `calculate_investment_return` | 235 | 279 | 240 |
| 9 | `get_order` | 112 | 82 | 82 |
| 10 | `find_kth_smallest_number` | 114 | 257 | 239 |
| 11 | `auto_complete` | 106 | 74 | 74 |
| 12 | `assess_diabetes_risk` | 127 | 264 | 255 |

Every candidate clears the 100-case floor. The final description-aware rule
removes zero candidate single-tool rows, so these counts are also the counts
after **all** filters.

## Sizing inputs (final rule, numbers not yet locked)

`m` is the count of dev tools (non-candidate tools) with **≥10 usable cases in
the actual Phase 3 dev sample**, and `u` is recomputed from that `m` with the
§7 formula `u = √((m−1)/χ²_{0.20, m−1})`. This is the final sizing **rule**;
`m`, `u`, `C`, and `n_t` are not locked here. The full-dataset structural
illustration in EVAL_DESIGN §7 (`m = 1,093`, `u = 1.019`) is an illustration
only, not a Phase-3 value. The old `m = 13` / `u ≈ 1.240` figures must not be
carried over.

## Reproduce / verify

The exact generation-and-verification command is:

```bash
python scripts/select_candidates.py \
  --revision 26d14ebfe18b1f7b524bd39b404b50af5dc97866
```

The script loads the pinned revision, applies filters 1–4, computes both the
structural 71-tool pool and the 70-tool usable pool under the final
description-aware rule, regenerates
`random.Random(42).sample(sorted(structural_eligible_tools), 12)`, and
asserts:

- the regenerated order equals the frozen order above;
- its sha256 equals the value above;
- the pinned revision has 168,870 flat declarations, zero nested declarations,
  and zero `required` keys in its 362,482 parameter descriptors;
- the structural pool is 71 and the final usable pool is 70;
- the final rule excludes 38 rows, and the 12 candidate counts are identical
  under both pools, every candidate clears the 100-case floor, and the final
  rule removes no candidate single-tool rows.

It prints aggregates and tool names only. It never prints or writes a raw
dataset row, query, answer, or full tool schema.

## Leak check and replacement rule

**Leak definition:** a held-out candidate tool appearing as a **gold/answer
tool in dev**. Distractor appearances do not count as leaks.

**Result:** zero candidate answer appearances in dev-eligible rows by
construction, because dev is defined by answer tools and mixed
candidate+non-candidate rows are excluded. The Phase-0 verifier recomputes
distractor exposure for every candidate; those are the counts in the table.

**Replacement rule (12 candidates, not 8):** the selected set is the first C
candidates in the frozen order. If a selected candidate leaks or falls below
the 100-case floor after a later filter, it is replaced by the next unused
candidate in the frozen order. With C = 8 there are 4 spares; C = 7 leaves 5;
C = 6 leaves 6. If the spares are exhausted below C, EVAL_DESIGN §7's
fallback applies. Distractor exposure never consumes a spare.

## What is not here

- No raw xLAM rows, queries, answers, or full tool schemas.
- No license verdict: the publication rule is approved, the verdict is not.
- No Phase-3 number for `m`, `u`, `C`, or `n_t`: decision 2 fixes the
  **rule** (≥10 usable Phase-3 dev cases per dev tool), while the concrete
  values require Phase 3 variance components.
- No test-split materialization and no pre-registration file or tag.
- No re-roll: the seed, order, and sha256 are frozen.
