# Phase 5 — Multi-Step Set and Trace Generation

Status: Part 1 complete. Part 2 is complete; see
`docs/PHASE5_CALIBRATION.md` for the judge calibration, the report-half κ, the
blind relabel batch, and the ship-rule outcome.

## Scope

The judge serves only the secondary multi-step question. It plays no role in
the pre-registered single-turn test. This phase does not touch the Phase 4
test sample, the scorer, `MATCH_SPEC.md`, or the pre-registration.

- 30 hand-written multi-step tasks, 15 tune and 15 report, split by task.
- Every task has at least two dependent tool calls. The mock results are
  scripted fixtures carried by the task, so a later call has a result to
  continue from.
- The loop runs with both models: `haiku-agent` at effort `low` and
  `sonnet-agent` at `api_default`. There is no direct arm; a no-loop arm
  cannot chain calls it never sees results for.
- The task set and tool schemas are original to this repository. They are not
  xLAM rows or xLAM schemas, and they avoid the frozen candidate tools.

## Task set

`data/multistep.jsonl` is tracked. Each row carries an id, split, query,
offered tool names, scripted fixtures, success criteria, and a note.

| Item | Count |
|---|---:|
| Tasks | 30 |
| Tune tasks | 15 |
| Report tasks | 15 |
| Scripted tool calls (fixtures) | 95 |
| Fixtures per task, minimum / mean / maximum | 2 / 3.17 / 4 |
| Distinct tools used | 25 |
| Tasks with 2 / 3 / 4 offered tools | 5 / 21 / 4 |
| Later fixtures that reference an earlier result | 63 |

The validator checks every fixture against its tool's JSON schema, rejects
unknown or held-out candidate tools, requires at least two fixtures, and
requires at least one later fixture argument to reference an earlier result.
A two-call task has one dependency link: the second call depends on the first.
It also enforces the 15/15 task split.

The mock executor matches a call on a fixture's `match` block. Identifiers,
dates, amounts, quantities, and enums must match exactly; free-text fields
(meeting titles, contact names as search queries, document queries, message
text, reasons, passenger and guest names) do not. A call with no matching
fixture gets a visible error result, so the loop can recover or the trace can
show the miss.

## Trace generation

Final run: `results/phase5-multistep-20261031-r3` (gitignored).

| Metric | `haiku-agent` | `sonnet-agent` |
|---|---:|---:|
| Model | `claude-haiku-5-5` | `claude-sonnet-5-5` |
| Effort | `low` (`output_config.effort`) | `api_default` (no effort field) |
| Traces | 30 | 30 |
| Completed traces | 30 | 30 |
| Incomplete traces | 0 | 0 |
| Input tokens | 140,005 | 139,024 |
| Output tokens | 12,134 | 11,660 |
| List-price cost | $0.020068 | $0.394648 |
| Cost per task | $0.000669 | $0.013155 |
| Mean model steps | 3.77 | 3.77 |
| Mean tool calls | 3.03 | 3.03 |
| Latency p50 / p95 | 3.66 s / 5.74 s | 5.51 s / 8.66 s |

Final-run total: **$0.414715** for 60 traces. Diagnostics: 0 truncations,
0 refusals, 0 format failures, 0 schema-repair retries, 0 max-step stops, and
0 fixture misses in either arm.

## Superseded runs and trace spend

The first full pass surfaced fixture-matching defects on free-text fields.
The task queries and success criteria did not change; I fixed the matcher and
regenerated the traces. The final traces were generated from the working tree
that became commits `71d0618` (task set) and `858268f` (loop and packet), so
the run metadata records the parent commit `8b1e477` rather than a commit
containing the loop code. The committed matcher code is the code that produced
the final traces.

| Run | Purpose | Cost |
|---|---|---:|
| `phase5-smoke-20261031` | one-task live smoke | $0.010299 |
| `phase5-multistep-20261031` | first full pass, superseded | $0.437077 |
| `phase5-multistep-20261031-r2` | second full pass, superseded | $0.424923 |
| `phase5-multistep-20261031-r3` | final 60-trace run | $0.414715 |
| **Total Phase 5 trace spend** | | **$1.287014** |

The superseded runs are retained in gitignored results storage, not deleted.
The final run is the only source of the labeling packet.

## Labeling packet

Packet: `results/phase5-labeling-20261031/packet/` (gitignored). Private
mapping: `results/phase5-labeling-20261031/private/mapping.json` (gitignored,
outside the packet).

- 60 traces in randomized order (seed `20261031`), two per task.
- Anonymized: no arm name, model id, effort, cost, token count, latency, or
  judge output. The packet builder refuses to write a packet containing a
  forbidden identity token.
- `labels.jsonl` has 60 rows with blank `label` values for me to fill.
- `RUBRIC.md` is the same `docs/MULTISTEP_RUBRIC.md` version
  `phase5-rubric-v1`.
- `packet.json` records the trace count, task count, seed, and rubric name.

I did not run the judge in Part 1, so no judge labels exist anywhere in the
packet.

## Budget

Phase 5 has a **$1.50** ceiling for traces plus judge calls. Trace spend is
**$1.287014**, leaving **$0.212986** for the judge. No judge spend has
occurred. If the judge cannot fit inside that remainder, I will stop and
report the shortfall rather than exceed the ceiling.

## Next step

Part 2 is recorded in `docs/PHASE5_CALIBRATION.md`: the tune half had zero
disagreements, so the judge prompt was frozen at v1; the report half agreed
30/30 but κ is undefined because every human label is `correct`; the blind
relabel batch also agreed 15/15. The ship rule is a published negative result.
The next useful work is a harder multi-step set with varied failures, not more
judge-prompt tuning against an all-correct label set.
