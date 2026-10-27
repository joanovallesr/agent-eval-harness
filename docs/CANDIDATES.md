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
- Generator code commit:
  `847a7de4c8e842087ed3a87c12d9b681ac57ce87` (`scripts/select_candidates.py`).

## Decision status (owner, 2026-10-26)

1. **Required-arguments filter — INTERIM, not final.** "No `default` key =
   required" is a documented **assumption/heuristic**, not a dataset rule. The
   pinned revision has no `required` key anywhere, while the dataset card
   describes a required flag; that card-vs-data discrepancy stays in the
   record. The final rule depends on `docs/REQUIRED_FILTER_AUDIT.md` and owner
   sign-off before pre-registration.
2. **Sizing inputs `m` and `u` — INTERIM placeholder.** The planning baseline
   keeps `m = 59` (71 eligible − 12 candidates) and `u = 1.090`. This is a
   conservative placeholder only, **not** a final derived `m`. The final `m`
   comes from the usable-cases rule in the EVAL_DESIGN §7 sensitivity table
   before pre-registration.
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
   The 63-tool all-filter pool is **diagnostic only**; the order is **not**
   re-rolled from it. All 12 candidates clear 100 single-tool cases under both
   pools.

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
4. **Required-arguments filter — INTERIM heuristic, not a dataset rule.** Under
   the interim heuristic, a gold call that omits a parameter with no `default`
   key is excluded. Parameters use the pinned normalizer: in xLAM's flat map
   `param -> {type, description, default?}`, a parameter without a `default`
   is treated as required; a nested JSON-Schema `properties`/`required` form
   is also handled defensively. The pinned revision has **168,870 flat tool
   declarations and zero nested declarations**. The public card describes a
   `required` boolean, but the pinned revision has **zero** `required` keys in
   its 362,482 parameter descriptors. That discrepancy is preserved in
   `docs/REQUIRED_FILTER_AUDIT.md`; "no default = required" is a documented
   assumption, **not** the dataset's rule. Under this interim heuristic the
   filter removes 1,316 rows globally (11 with entirely empty arguments where
   required parameters exist) and **zero single-tool candidate rows**.
5. **Eligibility floor.** A tool is eligible when it has **at least 100
   single-tool cases** after filters 1–4 (under the interim heuristic) or,
   for the structural pool, after filters 1–3.

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
| Rows removed by the interim required-arguments filter | 1,316 |
| Rows with empty arguments where required parameters exist | 11 |
| **Eligible tools, structural pool (≥100 single-tool cases)** | **71** |
| Eligible tools under the interim required-arguments heuristic | 63 |

**Frozen order — FINAL (owner, 2026-10-26).** The order is generated from the
**71-tool structural pool** locked in Phase 0. The interim required-arguments
heuristic yields a 63-tool all-filter pool; that pool is **diagnostic only**.
The verifier recomputes both pools and asserts that the 12 candidate counts
are **identical under both** (all clear 100). The order is not re-rolled from
the 63-tool pool, because the candidate list was frozen before the
required-arguments rule was settled and the instruction is explicitly not to
re-roll candidates. If a candidate had lost cases to this filter, it would be
flagged and a spare would cover it; none did. Dropping the filter entirely
returns the pool to 71; no candidate count changes either way.

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

Every candidate clears the 100-case floor. The interim required-arguments
heuristic removes zero candidate single-tool rows, so these counts are also the
counts after **all** filters.

## Sizing inputs (interim placeholder)

EVAL_DESIGN §7's planning baseline keeps **m = 59** (71 eligible tools minus
the 12 frozen candidates) and **u = 1.090**. This is an interim, conservative
placeholder only, **not** a final derived `m`: the final `m` must come from the
usable-cases rule in the §7 sensitivity table (dev tools with at least the
chosen number of usable single-tool cases; dev tools with exactly one usable
case cannot estimate within-tool variance and are excluded from `m`). The owner
picks that threshold before pre-registration. The old `m = 13` / `u ≈ 1.240`
figures must not be carried over.

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
  clears the 100-case floor, and the interim required-arguments heuristic
  removes no candidate single-tool rows.

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
- No final required-arguments rule: the current one is an interim heuristic
  pending `docs/REQUIRED_FILTER_AUDIT.md` and owner sign-off.
- No final `m` or `u`: `m = 59`, `u = 1.090` is a placeholder pending the
  EVAL_DESIGN §7 usable-cases sensitivity decision.
- No license verdict: the publication rule is approved, the verdict is not.
- No test-split materialization and no pre-registration file or tag.
- No re-roll: the seed, order, and sha256 are frozen.
