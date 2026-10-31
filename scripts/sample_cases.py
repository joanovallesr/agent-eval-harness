#!/usr/bin/env python3
"""Deterministic Phase-3 pilot/dev sampler for the pinned xLAM revision.

The sampler loads the pinned revision from the local Hugging Face cache,
applies the final filters from ``docs/CANDIDATES.md`` (answer-tools only,
structural filters, malformed-gold exclusion/count, single-tool only, the
description-aware required-arguments rule), excludes every frozen candidate
as a gold/answer tool, and emits a gitignored JSONL case file plus a metadata
file containing aggregates, counts, and tool names only.

Candidate tools may still appear as distractors. Those appearances are
allowed by the leak rule and are counted and reported.

Run::

    python scripts/sample_cases.py --stage pilot --n-tools 30 \
        --cases-per-tool 1 --seed 42
    python scripts/sample_cases.py --stage dev --n-tools 12 \
        --cases-per-tool 10 --seed 43
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from select_candidates import (
    FROZEN_CANDIDATES,
    PINNED_REVISION,
    normalize_parameters,
)

DEFAULT_CASE_DIR = Path("data/phase3")
DEFAULT_SEED = 42
CANDIDATE_SET = frozenset(FROZEN_CANDIDATES)


def _read_json(value: Any) -> Any:
    if isinstance(value, str):
        return json.loads(value)
    return value


def _case_id(revision: str, index: int, row: Mapping[str, Any]) -> str:
    digest = hashlib.sha256(
        f"{revision}:{index}:{row.get('query')}:{row.get('answers')}:"
        f"{row.get('tools')}".encode("utf-8")
    ).hexdigest()
    return f"xlam-{digest[:16]}"


def _json_schema_type(raw_type: Any) -> str | None:
    """Map xLAM's Python-flavoured type strings to JSON Schema types.

    Unknown/opaque types (for example ``Callable``) are left untyped rather
    than guessed; the scorer is schema-free anyway, and the loop validator
    must not reject a gold-shaped call because of an invented type.
    """

    if not isinstance(raw_type, str):
        return None
    head = raw_type.split(",", 1)[0].strip()
    if head == "str" or head.startswith("str"):
        return "string"
    if head == "int" or head.startswith("int"):
        return "integer"
    if head == "float" or head.startswith("float"):
        return "number"
    if head == "bool" or head.startswith("bool"):
        return "boolean"
    if (
        head in {"list", "List", "set", "Tuple"}
        or head.startswith("List[")
        or head.startswith("Tuple[")
    ):
        return "array"
    if head == "Dict" or head.startswith("Dict["):
        return "object"
    return None


def convert_tool_definition(tool: Mapping[str, Any]) -> dict[str, Any]:
    """Convert one xLAM tool declaration to the pinned Anthropic shape.

    ``parameters`` is a flat ``name -> descriptor`` map in the pinned
    revision. The required set uses the FINAL core-input rule so an omitted
    optional parameter is schema-valid.
    """

    name = tool.get("name")
    if not isinstance(name, str) or not name:
        raise ValueError("tool definition needs a non-empty name")
    parameters = tool.get("parameters")
    if not isinstance(parameters, dict):
        parameters = {}
    properties: dict[str, Any] = {}
    for parameter_name, descriptor in parameters.items():
        if not isinstance(parameter_name, str) or not isinstance(descriptor, dict):
            continue
        property_schema: dict[str, Any] = {}
        schema_type = _json_schema_type(descriptor.get("type"))
        if schema_type is not None:
            property_schema["type"] = schema_type
        description = descriptor.get("description")
        if isinstance(description, str):
            property_schema["description"] = description
        if "default" in descriptor:
            property_schema["default"] = descriptor["default"]
        properties[parameter_name] = property_schema
    spec = normalize_parameters(dict(tool))
    return {
        "name": name,
        "description": str(tool.get("description", "")),
        "input_schema": {
            "type": "object",
            "properties": properties,
            "required": sorted(spec.required),
        },
    }


def _parse_row(
    row: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, dict[str, Any]]]:
    answers = _read_json(row.get("answers"))
    tools_list = _read_json(row.get("tools"))
    if not isinstance(answers, list) or not isinstance(tools_list, list):
        raise ValueError("row answers/tools must parse to lists")
    tools = {
        tool["name"]: tool
        for tool in tools_list
        if isinstance(tool, dict) and isinstance(tool.get("name"), str)
    }
    return answers, tools_list, tools


def apply_final_filters(
    rows: Iterable[Mapping[str, Any]],
    *,
    revision: str = PINNED_REVISION,
    candidates: frozenset[str] = CANDIDATE_SET,
) -> dict[str, Any]:
    """Apply every final filter and return dev-eligible single-tool cases."""

    counts: Counter = Counter()
    by_tool: dict[str, list[dict[str, Any]]] = defaultdict(list)
    distractor_rows: Counter = Counter()
    distractor_cases: Counter = Counter()
    distractor_all_rows: Counter = Counter()

    for index, row in enumerate(rows):
        counts["rows"] += 1
        try:
            answers, tools_list, tools = _parse_row(row)
        except (json.JSONDecodeError, TypeError, ValueError):
            counts["malformed_gold"] += 1
            continue
        if not answers:
            counts["empty_answers"] += 1
            continue

        malformed = any(
            not isinstance(answer, dict)
            or not isinstance(answer.get("name"), str)
            or answer.get("name") == ""
            or not isinstance(answer.get("arguments"), dict)
            for answer in answers
        )
        if malformed:
            counts["malformed_gold"] += 1
            continue

        # Structural filter: gold name must be present in this row's tools.
        if any(answer["name"] not in tools for answer in answers):
            counts["gold_name_not_offered"] += 1
            continue

        gold_names = {answer["name"] for answer in answers}
        for candidate in candidates:
            if candidate in tools and candidate not in gold_names:
                distractor_all_rows[candidate] += 1

        # FINAL description-aware required-arguments rule: only a missing
        # core input excludes the row. The global count matches the Phase-0
        # audit; the single-tool count is what changes dev eligibility.
        required_gap = any(
            normalize_parameters(tools[answer["name"]]).required
            - set(answer["arguments"])
            for answer in answers
        )
        if required_gap:
            counts["required_core_input_rows"] += 1

        # Multi-tool classification happens before the required-arguments
        # filter, so the excluded mixed-case count matches the Phase-0 audit.
        names = {answer["name"] for answer in answers}
        if len(names) > 1:
            counts["multi_tool"] += 1
            if names & candidates and names - candidates:
                counts["mixed_candidate_non_candidate"] += 1
            continue

        if required_gap:
            counts["required_core_input_missing_single_tool"] += 1
            continue

        tool = next(iter(names))
        if tool in candidates:
            counts["candidate_answer_rows"] += 1
            continue

        # Distractor exposure is disclosed, never filtered.
        exposed = [
            candidate
            for candidate in candidates
            if candidate in tools and candidate not in names
        ]
        for candidate in exposed:
            distractor_rows[candidate] += 1

        definitions = [
            convert_tool_definition(tool_definition)
            for tool_definition in tools_list
            if isinstance(tool_definition, dict)
            and isinstance(tool_definition.get("name"), str)
        ]
        # Keep row order for tool definitions; the API is order-insensitive.
        case = {
            "id": _case_id(revision, index, row),
            "query": row.get("query"),
            "tools": definitions,
            "answers": json.dumps(answers),
            "tool_name": tool,
            "source_index": index,
        }
        by_tool[tool].append(case)
        for candidate in exposed:
            distractor_cases[candidate] += 1
        counts["eligible_single_tool_non_candidate"] += 1

    counts["tools_with_eligible_cases"] = len(by_tool)
    return {
        "counts": dict(counts),
        "by_tool": dict(by_tool),
        "distractor_rows": dict(distractor_rows),
        "distractor_cases": dict(distractor_cases),
        "distractor_all_rows": dict(distractor_all_rows),
    }


def sample_stage(
    by_tool: Mapping[str, Sequence[dict[str, Any]]],
    *,
    stage: str,
    seed: int,
    n_tools: int,
    cases_per_tool: int,
) -> dict[str, Any]:
    """Seeded stratified sample: same cases for every arm in the stage."""

    if n_tools < 1 or cases_per_tool < 1:
        raise ValueError("n_tools and cases_per_tool must be >= 1")
    rng = random.Random(f"{seed}:{stage}")
    eligible = sorted(
        tool
        for tool, cases in by_tool.items()
        if len(cases) >= cases_per_tool
    )
    if len(eligible) < n_tools:
        raise ValueError(
            f"{stage}: {len(eligible)} tools have >= {cases_per_tool} cases; "
            f"need {n_tools}"
        )
    chosen_tools = sorted(rng.sample(eligible, n_tools))
    cases: list[dict[str, Any]] = []
    distribution: dict[str, int] = {}
    for tool in chosen_tools:
        pool = sorted(by_tool[tool], key=lambda case: case["id"])
        picked = rng.sample(pool, cases_per_tool)
        distribution[tool] = len(picked)
        cases.extend(picked)
    cases.sort(key=lambda case: (case["tool_name"], case["id"]))
    return {
        "cases": cases,
        "tool_distribution": distribution,
        "n_tools": len(chosen_tools),
        "n_cases": len(cases),
    }


def sample_metadata(
    *,
    stage: str,
    seed: int,
    revision: str,
    filter_report: Mapping[str, Any],
    sample: Mapping[str, Any],
) -> dict[str, Any]:
    distractor_sample: Counter = Counter()
    for case in sample["cases"]:
        offered = {
            tool["name"] for tool in case["tools"] if isinstance(tool, dict)
        }
        for candidate in CANDIDATE_SET.intersection(offered):
            distractor_sample[candidate] += 1
    return {
        "stage": stage,
        "seed": seed,
        "seed_salt": f"{seed}:{stage}",
        "revision": revision,
        "candidates": list(FROZEN_CANDIDATES),
        "filters": dict(filter_report["counts"]),
        "candidate_answer_rows_excluded": int(
            filter_report["counts"].get("candidate_answer_rows", 0)
        ),
        "mixed_candidate_non_candidate_excluded": int(
            filter_report["counts"].get("mixed_candidate_non_candidate", 0)
        ),
        "malformed_gold_excluded": int(
            filter_report["counts"].get("malformed_gold", 0)
        ),
        "required_core_input_excluded": int(
            filter_report["counts"].get("required_core_input_rows", 0)
        ),
        "required_core_input_excluded_single_tool": int(
            filter_report["counts"].get("required_core_input_missing_single_tool", 0)
        ),
        "distractor_exposure_all_rows": dict(
            filter_report.get("distractor_all_rows", {})
        ),
        "distractor_exposure_dev_eligible_single_tool": dict(
            filter_report["distractor_rows"]
        ),
        "distractor_exposure_sample": dict(distractor_sample),
        "n_cases": sample["n_cases"],
        "n_tools": sample["n_tools"],
        "cases_per_tool": (
            sample["n_cases"] // sample["n_tools"] if sample["n_tools"] else 0
        ),
        "tool_distribution": dict(sorted(sample["tool_distribution"].items())),
        "contains_raw_rows": False,
    }


def write_stage(
    *,
    rows: Iterable[Mapping[str, Any]],
    stage: str,
    seed: int,
    n_tools: int,
    cases_per_tool: int,
    out_dir: Path = DEFAULT_CASE_DIR,
    revision: str = PINNED_REVISION,
) -> dict[str, Any]:
    filter_report = apply_final_filters(rows, revision=revision)
    sample = sample_stage(
        filter_report["by_tool"],
        stage=stage,
        seed=seed,
        n_tools=n_tools,
        cases_per_tool=cases_per_tool,
    )
    metadata = sample_metadata(
        stage=stage,
        seed=seed,
        revision=revision,
        filter_report=filter_report,
        sample=sample,
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    case_path = out_dir / f"{stage}.jsonl"
    metadata_path = out_dir / f"{stage}.meta.json"
    with case_path.open("w", encoding="utf-8") as handle:
        for case in sample["cases"]:
            handle.write(json.dumps(case, sort_keys=True) + "\n")
    metadata = dict(metadata)
    metadata["case_file"] = str(case_path)
    metadata["metadata_file"] = str(metadata_path)
    metadata_path.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n")
    return metadata


def load_rows_from_cache(revision: str = PINNED_REVISION, split: str = "train"):
    """Load the pinned revision from the local HF cache, never the network."""

    if revision != PINNED_REVISION:
        raise ValueError(
            f"revision {revision!r} is not the pinned {PINNED_REVISION!r}"
        )
    # Offline mode makes a cache miss a loud failure instead of a silent
    # download of a different snapshot.
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("HF_DATASETS_OFFLINE", "1")
    from datasets import load_dataset

    return load_dataset(
        "Salesforce/xlam-function-calling-60k",
        revision=revision,
        split=split,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", required=True, choices=("pilot", "dev"))
    parser.add_argument("--n-tools", type=int, required=True)
    parser.add_argument("--cases-per-tool", type=int, required=True)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--revision", default=PINNED_REVISION)
    parser.add_argument("--split", default="train")
    parser.add_argument("--out-dir", default=str(DEFAULT_CASE_DIR))
    args = parser.parse_args()

    rows = load_rows_from_cache(args.revision, args.split)
    metadata = write_stage(
        rows=rows,
        stage=args.stage,
        seed=args.seed,
        n_tools=args.n_tools,
        cases_per_tool=args.cases_per_tool,
        out_dir=Path(args.out_dir),
        revision=args.revision,
    )
    printable = {
        key: metadata[key]
        for key in (
            "stage",
            "seed",
            "revision",
            "filters",
            "candidate_answer_rows_excluded",
            "mixed_candidate_non_candidate_excluded",
            "malformed_gold_excluded",
            "required_core_input_excluded",
            "required_core_input_excluded_single_tool",
            "distractor_exposure_all_rows",
            "distractor_exposure_dev_eligible_single_tool",
            "distractor_exposure_sample",
            "n_cases",
            "n_tools",
            "cases_per_tool",
            "tool_distribution",
            "case_file",
            "metadata_file",
        )
    }
    print(json.dumps(printable, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
