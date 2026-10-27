# Held-Out Tool Candidates (Phase 0 Freeze)

Status: **frozen Phase-0 candidate list**. This is not pre-registration. The
final C held-out tools are selected from this ordered list in Phase 4 (C ∈
{6, 7, 8}, EVAL_DESIGN §7). The claim, margin, arms, and cost cap are locked
elsewhere and are not decided here.

- Frozen: **2026-10-26** (design pivot dated 2026-10-25).
- Dataset: `Salesforce/xlam-function-calling-60k`.
- Pinned revision: `26d14ebfe18b1f7b524bd39b404b50af5dc97866` (2025-01-24).
- Seed: **42**.
- Selection: `random.Random(42).sample(sorted(eligible_tools), 12)`.
- Order: the sample order below is the frozen replacement order.
- sha256 of `"\n".join(candidates)` (no trailing newline):
  `6b822299090bda4e6dfca52b6db426b7b2b4b5dba14662505962145415623991`.
- Generator code commit:
  `7f8e797c48c2a8d094e5b155c36c2988c2be3f3a` (`scripts/select_candidates.py`).

## Eligibility filters

Applied in this order, before the split is materialized:

1. **Answer-tools only.** Eligibility is defined by the gold `answers` field,
   never by distractor appearances in a row's `tools` list.
2. **Structural row filters.** Exclude a row with an empty `answers` list, a
   missing/non-string/empty gold function name, or a gold name not present in
   that row's `tools`. (On the pinned revision: 0 rows fail these checks.)
3. **Single-tool cases only.** Every gold call in the row must name the same
   tool. Multi-tool rows are not assigned to a held-out tool. Rows whose gold
   calls mix a candidate with a non-candidate are excluded and counted. Rows
   whose gold calls span more than one candidate tool are multi-tool and are
   not used in the v1 per-tool analysis.
4. **Required-arguments filter.** A gold call that omits a required parameter
   is excluded. Parameters use the pinned normalizer: in xLAM's flat map
   `param -> {type, description, default?}`, a parameter without a `default`
   is required; a nested JSON-Schema `properties`/`required` form is also
   handled defensively. The pinned revision has **168,870 flat tool
   declarations and zero nested declarations**. The public card describes a
   `required` boolean, but the pinned revision has **zero** `required` keys in
   its 362,482 parameter descriptors; absence of `default` is therefore the
   operative required signal. This discrepancy is flagged for owner sign-off
   before pre-registration. The filter removes 1,316 rows globally (11 with
   entirely empty arguments where required parameters exist) and **zero
   single-tool candidate rows**.
5. **Eligibility floor.** A tool is eligible when it has **at least 100
   single-tool cases** after filters 1–4.

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
| Rows removed by required-arguments filter | 1,316 |
| Rows with empty arguments where required parameters exist | 11 |
| **Eligible tools, structural pool (≥100 single-tool cases)** | **71** |
| Eligible tools after **all** filters, including required arguments | 63 |

The frozen order is generated from the **71-tool structural pool** locked in
Phase 0. The required-arguments filter is applied per case; the verifier
recomputes both pools and asserts that the 12 candidate counts are **identical
under both** (all clear 100). The order is not re-rolled from the 63-tool
all-filter pool, because the candidate list was frozen before the
required-arguments filter was finalized and the instruction is explicitly not
to re-roll candidates. If a candidate had lost cases to this filter, it would
be flagged and a spare would cover it; none did.

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

Every candidate clears the 100-case floor. The required-arguments filter
removes zero candidate single-tool rows, so these counts are also the counts
after **all** filters.

## Reproduce / verify

The exact generation-and-verification command is:

```bash
python scripts/select_candidates.py \
  --revision 26d14ebfe18b1f7b524bd39b404b50af5dc97866
```

The script loads the pinned revision, applies filters 1–4, computes both the
structural 71-tool pool and the 63-tool all-filter pool, regenerates
`random.Random(42).sample(sorted(structural_eligible_tools), 12)`, and
asserts:

- the regenerated order equals the frozen order above;
- its sha256 equals the value above;
- the pinned revision has 168,870 flat declarations, zero nested declarations,
  and zero `required` keys in its 362,482 parameter descriptors;
- the structural pool is 71 and the all-filter pool is 63;
- the 12 candidate counts are identical under both pools, every candidate
  clears the 100-case floor, and the required-arguments filter removes no
  candidate single-tool rows.

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
- No test-split materialization and no pre-registration file or tag.
- No re-roll: the seed, order, and sha256 are frozen.
