# Required-Arguments Filter Audit — Pinned xLAM Revision

**Status: evidence for owner sign-off. This report does not choose the
final required-arguments rule.** It reproduces what the interim
"no `default` key = required" heuristic removes and surfaces the
description evidence needed to replace or confirm it before
pre-registration.

- Pinned revision: `26d14ebfe18b1f7b524bd39b404b50af5dc97866`.
- Dataset: `Salesforce/xlam-function-calling-60k`, split `train`.
- Offline: no model calls, no candidate-tool runs.
- Sample seed: **42**; sample size: **50 excluded rows**.
- Contents: aggregate counts, tool names, parameter names, parameter
  descriptions, and yes/no flags only. No raw query, gold answer, or
  full tool schema.

## Reproduction

```bash
python scripts/audit_required_filter.py \
  --revision 26d14ebfe18b1f7b524bd39b404b50af5dc97866 \
  --out docs/REQUIRED_FILTER_AUDIT.md
```

The script loads the pinned revision, applies the Phase-0 structural
filters, reproduces the interim required-arguments exclusion, and
asserts the pinned counts below.

## Totals

| Metric | Count |
|---|---:|
| Rows scanned | 60,000 |
| Empty `answers` rows | 0 |
| Malformed gold tool names | 0 |
| Gold names absent from the row's `tools` | 0 |
| Malformed gold arguments (non-object/string-encoded) | 0 |
| **Malformed gold rows (final exclusion, decision 3)** | **0** |
| **Rows removed by the interim required-arguments filter** | **1,316** |
| Missing-parameter instances | 1,432 |
| Rows with exactly one missing-parameter instance | 1,211 |
| Rows with more than one missing-parameter instance | 105 |
| Rows with more than one distinct missing parameter name | 38 |
| Rows where at least one missing call carries other gold arguments | 1,305 |
| Rows with at least one missing call that has empty arguments | 11 |
| Distinct missing parameter names | 29 |
| Single-tool candidate rows removed | 0 |

## Category breakdown

Categories are lexical and mutually exclusive, with fixed precedence: (1) filter/pagination/sort parameter name, (2) description documents a default/optional fallback, (3) core input. Rows can touch more than one category, so the row column does not sum to the total.

| Category | Missing-parameter instances | Share | Excluded rows touched |
|---|---:|---:|---:|
| optional-by-description | 1,297 | 90.6% | 1,199 |
| filter/pagination/sort | 92 | 6.4% | 92 |
| core-input | 43 | 3.0% | 38 |
| **Total** | **1,432** | **100.0%** | **1,316** |

**Description signal (overlapping with the table above).** 1,389 of 1,432 missing-parameter instances (97.0%) have a description that documents a default or optional fallback; 43 have no such language. Literal `default` keys among the missing parameters: 0 (zero by construction). These are the heuristic's contested cases: the descriptor has no `default` key, but its own text says a fallback exists.

## Missing parameters by tool

| Tool | Missing parameter | Instances | Category |
|---|---|---:|---|
| `calculate_electric_field` | `permitivity` | 258 | optional-by-description |
| `cell_density` | `factor` | 130 | optional-by-description |
| `project_investment_growth` | `inflation_adjusted` | 130 | optional-by-description |
| `bacterial_growth` | `doubling_time` | 120 | optional-by-description |
| `dice_roll_probability` | `num_faces` | 111 | optional-by-description |
| `batting_average` | `precision` | 103 | optional-by-description |
| `sort_numbers` | `descending` | 92 | filter/pagination/sort |
| `mean_confidence_interval` | `confidence` | 83 | optional-by-description |
| `trapezoidal_integration` | `n` | 82 | optional-by-description |
| `structural_analysis` | `analysis_mode` | 76 | optional-by-description |
| `wire_resistance` | `material` | 42 | optional-by-description |
| `independent_samples_t_test` | `alpha` | 41 | optional-by-description |
| `probability_of_consecutive_rolls` | `num_sides` | 35 | optional-by-description |
| `predict_forest_area` | `include_humans` | 20 | optional-by-description |
| `reserve_hotel_room` | `promo_code` | 18 | optional-by-description |
| `numerical_derivative` | `function` | 18 | core-input |
| `predict_evolution_rate` | `model` | 17 | optional-by-description |
| `compound_interest` | `n` | 13 | optional-by-description |
| `note_duration` | `bpm` | 11 | optional-by-description |
| `compound_interest` | `time` | 7 | core-input |
| `integrate` | `method` | 6 | core-input |
| `sort_numbers` | `numbers` | 5 | core-input |
| `project_population` | `annual_growth` | 5 | optional-by-description |
| `expand_around_center` | `s` | 2 | core-input |
| `binary_search` | `arr` | 2 | core-input |
| `get_dna_sequence` | `file_format` | 1 | optional-by-description |
| `integrate` | `end_x` | 1 | core-input |
| `generate_random_string` | `digits` | 1 | optional-by-description |
| `find_missing_ranges` | `nums` | 1 | core-input |
| `split_list` | `lst` | 1 | core-input |

## Reproducible sample of 50 excluded rows (seed 42)

Sampling: `random.Random(42).sample(sorted(excluded_row_indices), 50)`; the sample order is the `random.sample` order. A sampled row can contribute more than one line when multiple gold calls omit parameters. The table reports sample positions only.

| # | Tool | Missing parameter | Category | Other gold args in that call? | Parameter description |
|---:|---|---|---|---|---|
| 1 | `independent_samples_t_test` | `alpha` | optional-by-description | yes | The significance level of the test. Defaults to 0.05. |
| 2 | `compound_interest` | `n` | optional-by-description | yes | The number of times interest is compounded per year. Defaults to 1. |
| 3 | `calculate_electric_field` | `permitivity` | optional-by-description | yes | Permitivity of the space where field is being calculated, default is 8.854e-12. |
| 3 | `calculate_electric_field` | `permitivity` | optional-by-description | yes | Permitivity of the space where field is being calculated, default is 8.854e-12. |
| 4 | `batting_average` | `precision` | optional-by-description | yes | The number of decimal places to round the batting average. Defaults to 3. |
| 5 | `project_investment_growth` | `inflation_adjusted` | optional-by-description | yes | Whether to adjust the final value for inflation. Defaults to True. |
| 6 | `cell_density` | `factor` | optional-by-description | yes | The calibration factor for converting OD to cell density. Defaults to 1e9. |
| 7 | `sort_numbers` | `descending` | filter/pagination/sort | yes | If True, sorts the numbers in descending order. Defaults to False. |
| 8 | `calculate_electric_field` | `permitivity` | optional-by-description | yes | Permitivity of the space where field is being calculated, default is 8.854e-12. |
| 9 | `cell_density` | `factor` | optional-by-description | yes | The calibration factor for converting OD to cell density. Defaults to 1e9. |
| 10 | `calculate_electric_field` | `permitivity` | optional-by-description | yes | Permitivity of the space where field is being calculated, default is 8.854e-12. |
| 11 | `independent_samples_t_test` | `alpha` | optional-by-description | yes | The significance level of the test. Defaults to 0.05. |
| 12 | `project_investment_growth` | `inflation_adjusted` | optional-by-description | yes | Whether to adjust the final value for inflation. Defaults to True. |
| 13 | `mean_confidence_interval` | `confidence` | optional-by-description | yes | The desired confidence level. Default is 0.95. |
| 14 | `sort_numbers` | `descending` | filter/pagination/sort | yes | If True, sorts the numbers in descending order. Defaults to False. |
| 15 | `project_investment_growth` | `inflation_adjusted` | optional-by-description | yes | Whether to adjust the final value for inflation. Defaults to True. |
| 16 | `calculate_electric_field` | `permitivity` | optional-by-description | yes | Permitivity of the space where field is being calculated, default is 8.854e-12. |
| 17 | `project_investment_growth` | `inflation_adjusted` | optional-by-description | yes | Whether to adjust the final value for inflation. Defaults to True. |
| 18 | `project_investment_growth` | `inflation_adjusted` | optional-by-description | yes | Whether to adjust the final value for inflation. Defaults to True. |
| 19 | `batting_average` | `precision` | optional-by-description | yes | The number of decimal places to round the batting average. Defaults to 3. |
| 20 | `cell_density` | `factor` | optional-by-description | yes | The calibration factor for converting OD to cell density. Defaults to 1e9. |
| 21 | `cell_density` | `factor` | optional-by-description | yes | The calibration factor for converting OD to cell density. Defaults to 1e9. |
| 22 | `dice_roll_probability` | `num_faces` | optional-by-description | yes | The number of faces on each die. Defaults to 6. |
| 23 | `cell_density` | `factor` | optional-by-description | yes | The calibration factor for converting OD to cell density. Defaults to 1e9. |
| 24 | `cell_density` | `factor` | optional-by-description | yes | The calibration factor for converting OD to cell density. Defaults to 1e9. |
| 25 | `project_investment_growth` | `inflation_adjusted` | optional-by-description | yes | Whether to adjust the final value for inflation. Defaults to True. |
| 26 | `batting_average` | `precision` | optional-by-description | yes | The number of decimal places to round the batting average. Defaults to 3. |
| 27 | `calculate_electric_field` | `permitivity` | optional-by-description | yes | Permitivity of the space where field is being calculated, default is 8.854e-12. |
| 28 | `bacterial_growth` | `doubling_time` | optional-by-description | yes | The doubling time of the bacteria in minutes. Defaults to 20. |
| 29 | `calculate_electric_field` | `permitivity` | optional-by-description | yes | Permitivity of the space where field is being calculated, default is 8.854e-12. |
| 30 | `probability_of_consecutive_rolls` | `num_sides` | optional-by-description | yes | The number of sides on the die. Defaults to 6. |
| 31 | `sort_numbers` | `descending` | filter/pagination/sort | yes | If True, sorts the numbers in descending order. Defaults to False. |
| 32 | `calculate_electric_field` | `permitivity` | optional-by-description | yes | Permitivity of the space where field is being calculated, default is 8.854e-12. |
| 33 | `project_investment_growth` | `inflation_adjusted` | optional-by-description | yes | Whether to adjust the final value for inflation. Defaults to True. |
| 34 | `batting_average` | `precision` | optional-by-description | yes | The number of decimal places to round the batting average. Defaults to 3. |
| 35 | `mean_confidence_interval` | `confidence` | optional-by-description | yes | The desired confidence level. Default is 0.95. |
| 36 | `bacterial_growth` | `doubling_time` | optional-by-description | yes | The doubling time of the bacteria in minutes. Defaults to 20. |
| 37 | `calculate_electric_field` | `permitivity` | optional-by-description | yes | Permitivity of the space where field is being calculated, default is 8.854e-12. |
| 37 | `calculate_electric_field` | `permitivity` | optional-by-description | yes | Permitivity of the space where field is being calculated, default is 8.854e-12. |
| 38 | `sort_numbers` | `descending` | filter/pagination/sort | yes | If True, sorts the numbers in descending order. Defaults to False. |
| 39 | `cell_density` | `factor` | optional-by-description | yes | The calibration factor for converting OD to cell density. Defaults to 1e9. |
| 40 | `batting_average` | `precision` | optional-by-description | yes | The number of decimal places to round the batting average. Defaults to 3. |
| 40 | `batting_average` | `precision` | optional-by-description | yes | The number of decimal places to round the batting average. Defaults to 3. |
| 41 | `calculate_electric_field` | `permitivity` | optional-by-description | yes | Permitivity of the space where field is being calculated, default is 8.854e-12. |
| 42 | `batting_average` | `precision` | optional-by-description | yes | The number of decimal places to round the batting average. Defaults to 3. |
| 43 | `independent_samples_t_test` | `alpha` | optional-by-description | yes | The significance level of the test. Defaults to 0.05. |
| 44 | `predict_forest_area` | `include_humans` | optional-by-description | yes | Whether to include the impact of human activities in the prediction. Defaults to False. |
| 45 | `calculate_electric_field` | `permitivity` | optional-by-description | yes | Permitivity of the space where field is being calculated, default is 8.854e-12. |
| 46 | `project_investment_growth` | `inflation_adjusted` | optional-by-description | yes | Whether to adjust the final value for inflation. Defaults to True. |
| 47 | `predict_evolution_rate` | `model` | optional-by-description | yes | The model used to make the prediction, options: 'Darwin', 'Lamarck', default is 'Darwin'. |
| 47 | `project_investment_growth` | `inflation_adjusted` | optional-by-description | yes | Whether to adjust the final value for inflation. Defaults to True. |
| 48 | `calculate_electric_field` | `permitivity` | optional-by-description | yes | Permitivity of the space where field is being calculated, default is 8.854e-12. |
| 49 | `dice_roll_probability` | `num_faces` | optional-by-description | yes | The number of faces on each die. Defaults to 6. |
| 49 | `batting_average` | `precision` | optional-by-description | yes | The number of decimal places to round the batting average. Defaults to 3. |
| 50 | `batting_average` | `precision` | optional-by-description | yes | The number of decimal places to round the batting average. Defaults to 3. |

## Supplementary: every core-input exclusion

These are the only missing parameters without a documented default/optional fallback or a filter/sort name. They are listed in full, not sampled, because they drive the description-aware option below.

| # | Tool | Missing parameter | Other gold args in that call? | Parameter description |
|---:|---|---|---|---|
| 1 | `compound_interest` | `time` | no | The time in years for which the investment is held. |
| 2 | `sort_numbers` | `numbers` | yes | The list of numbers to be sorted. |
| 3 | `compound_interest` | `time` | no | The time in years for which the investment is held. |
| 4 | `compound_interest` | `time` | no | The time in years for which the investment is held. |
| 5 | `compound_interest` | `time` | no | The time in years for which the investment is held. |
| 6 | `numerical_derivative` | `function` | yes | The function to calculate the derivative of. |
| 7 | `numerical_derivative` | `function` | yes | The function to calculate the derivative of. |
| 8 | `integrate` | `method` | yes | The method of numerical integration to use. Choices are 'trapezoid' or 'simpson'. |
| 9 | `numerical_derivative` | `function` | yes | The function to calculate the derivative of. |
| 10 | `numerical_derivative` | `function` | yes | The function to calculate the derivative of. |
| 11 | `expand_around_center` | `s` | yes | The input string. |
| 12 | `numerical_derivative` | `function` | yes | The function to calculate the derivative of. |
| 13 | `numerical_derivative` | `function` | yes | The function to calculate the derivative of. |
| 14 | `numerical_derivative` | `function` | yes | The function to calculate the derivative of. |
| 15 | `integrate` | `method` | yes | The method of numerical integration to use. Choices are 'trapezoid' or 'simpson'. |
| 16 | `expand_around_center` | `s` | yes | The input string. |
| 17 | `numerical_derivative` | `function` | yes | The function to calculate the derivative of. |
| 18 | `numerical_derivative` | `function` | yes | The function to calculate the derivative of. |
| 19 | `numerical_derivative` | `function` | yes | The function to calculate the derivative of. |
| 20 | `numerical_derivative` | `function` | yes | The function to calculate the derivative of. |
| 21 | `numerical_derivative` | `function` | yes | The function to calculate the derivative of. |
| 22 | `integrate` | `end_x` | no | The ending x-value to integrate over. |
| 23 | `integrate` | `method` | no | The method of numerical integration to use. Choices are 'trapezoid' or 'simpson'. |
| 24 | `numerical_derivative` | `function` | yes | The function to calculate the derivative of. |
| 25 | `sort_numbers` | `numbers` | yes | The list of numbers to be sorted. |
| 26 | `numerical_derivative` | `function` | yes | The function to calculate the derivative of. |
| 27 | `integrate` | `method` | yes | The method of numerical integration to use. Choices are 'trapezoid' or 'simpson'. |
| 28 | `integrate` | `method` | yes | The method of numerical integration to use. Choices are 'trapezoid' or 'simpson'. |
| 29 | `binary_search` | `arr` | yes | The sorted list of integers. |
| 30 | `numerical_derivative` | `function` | yes | The function to calculate the derivative of. |
| 31 | `sort_numbers` | `numbers` | no | The list of numbers to be sorted. |
| 32 | `numerical_derivative` | `function` | yes | The function to calculate the derivative of. |
| 33 | `find_missing_ranges` | `nums` | yes | The sorted list of integers. |
| 34 | `numerical_derivative` | `function` | yes | The function to calculate the derivative of. |
| 35 | `numerical_derivative` | `function` | yes | The function to calculate the derivative of. |
| 36 | `compound_interest` | `time` | no | The time in years for which the investment is held. |
| 37 | `compound_interest` | `time` | no | The time in years for which the investment is held. |
| 38 | `compound_interest` | `time` | no | The time in years for which the investment is held. |
| 39 | `binary_search` | `arr` | yes | The sorted list of integers. |
| 40 | `sort_numbers` | `numbers` | no | The list of numbers to be sorted. |
| 41 | `split_list` | `lst` | yes | The input list. |
| 42 | `integrate` | `method` | yes | The method of numerical integration to use. Choices are 'trapezoid' or 'simpson'. |
| 43 | `sort_numbers` | `numbers` | yes | The list of numbers to be sorted. |

## What the audit supports (owner decision pending)

Three options, with their measured consequences on this revision:

| Option | Rows excluded | Eligible tools | Candidate rows removed |
|---|---:|---:|---:|
| Keep the interim heuristic | 1,316 | 63 | 0 |
| Description-aware refinement | 38 | 70 | 0 |
| Drop the filter | 0 | 71 | 0 |

The description-aware refinement treats a parameter as optional when its description documents a default/optional fallback, or its name is a filter/pagination/sort parameter, even if the descriptor lacks a `default` key; only core inputs remain required. Tools that lose the 100-case floor under it: `integrate`.

**Supported rule, if the owner accepts description text as the signal:** the description-aware refinement. The audit finds that 97.0% of missing-parameter instances are parameters whose own description documents a default/optional fallback, while only 43 instances (38 rows) are core inputs with no such language. This is a recommendation from the counts, **not a decision**: the owner signs the final rule before pre-registration.

**If the filter is dropped**, the eligible pool returns to the structural 71 tools. The 63-tool figure is an artifact of the interim heuristic, not a property of the dataset. **No option removes a candidate single-tool row** (interim gaps: 0; description-aware gaps: 0): all 12 candidate counts, the frozen order, and its sha256 are unchanged, so no candidate replacement is triggered.

The public card describes a `required` boolean; the pinned revision has zero `required` keys in its 362,482 parameter descriptors. That card-vs-data discrepancy remains open and must be recorded in pre-registration whichever rule is chosen.
