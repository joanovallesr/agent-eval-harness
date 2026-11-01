"""Judge calibration: Cohen's kappa, interval, and per-arm summaries.

The judge serves the secondary multi-step question only. Human labels are the
reference. A one-class human label set makes kappa undefined even when raw
agreement is perfect; the analysis reports that condition instead of inventing
a number.
"""

from __future__ import annotations

import argparse
import json
import math
import random
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

KAPPA_SHIP_THRESHOLD = 0.70


def _check_labels(y_human: Sequence[str], y_judge: Sequence[str]) -> None:
    if len(y_human) != len(y_judge):
        raise ValueError("human and judge label lists must have the same length")
    if not y_human:
        raise ValueError("at least one label pair is required")


def cohens_kappa(y_human: Sequence[str], y_judge: Sequence[str]) -> float:
    """Cohen's kappa for the three labels correct/wrong/partial.

    Returns ``nan`` when expected agreement is 1 (one rater used a single
    class), because kappa is undefined there.
    """

    _check_labels(y_human, y_judge)
    n = len(y_human)
    labels = sorted(set(y_human) | set(y_judge))
    observed = sum(1 for human, judge in zip(y_human, y_judge) if human == judge) / n
    expected = 0.0
    for label in labels:
        p_human = sum(1 for value in y_human if value == label) / n
        p_judge = sum(1 for value in y_judge if value == label) / n
        expected += p_human * p_judge
    if math.isclose(1.0, expected, abs_tol=1e-12):
        return float("nan")
    return (observed - expected) / (1.0 - expected)


def raw_agreement(y_human: Sequence[str], y_judge: Sequence[str]) -> float:
    _check_labels(y_human, y_judge)
    return sum(1 for human, judge in zip(y_human, y_judge) if human == judge) / len(
        y_human
    )


def confusion_matrix(
    y_human: Sequence[str],
    y_judge: Sequence[str],
) -> dict[str, Any]:
    """3x3 confusion matrix, human rows x judge columns."""

    _check_labels(y_human, y_judge)
    labels = ["correct", "partial", "wrong"]
    matrix = {human: {judge: 0 for judge in labels} for human in labels}
    for human, judge in zip(y_human, y_judge):
        matrix.setdefault(human, {label: 0 for label in labels})
        matrix[human][judge] = matrix[human].get(judge, 0) + 1
    return {
        "labels": labels,
        "matrix": [[matrix[human].get(judge, 0) for judge in labels] for human in labels],
        "by_human": matrix,
    }


def bootstrap_kappa_ci(
    y_human: Sequence[str],
    y_judge: Sequence[str],
    *,
    clusters: Sequence[str] | None = None,
    n_resamples: int = 2000,
    seed: int = 42,
) -> tuple[float, float]:
    """Percentile bootstrap interval for kappa.

    ``clusters`` resamples tasks rather than traces, so the paired traces from
    one task stay together. Returns ``(nan, nan)`` when every resample is
    degenerate.
    """

    _check_labels(y_human, y_judge)
    if n_resamples <= 0:
        raise ValueError("n_resamples must be positive")
    rng = random.Random(seed)
    n = len(y_human)
    if clusters is None:
        groups = [[index] for index in range(n)]
    else:
        if len(clusters) != n:
            raise ValueError("clusters must have one entry per trace")
        by_cluster: dict[str, list[int]] = {}
        for index, cluster in enumerate(clusters):
            by_cluster.setdefault(str(cluster), []).append(index)
        groups = list(by_cluster.values())
    values: list[float] = []
    for _ in range(n_resamples):
        sampled: list[int] = []
        for _ in range(len(groups)):
            sampled.extend(groups[rng.randrange(len(groups))])
        kappa = cohens_kappa(
            [y_human[index] for index in sampled],
            [y_judge[index] for index in sampled],
        )
        if not math.isnan(kappa):
            values.append(kappa)
    if not values:
        return float("nan"), float("nan")
    values.sort()
    low = values[int(0.025 * (len(values) - 1))]
    high = values[int(0.975 * (len(values) - 1))]
    return low, high


def _read_jsonl(path: Path | str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with Path(path).open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def _write_json(path: Path | str, payload: Mapping[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _mapping_rows(mapping_path: Path | str) -> dict[str, dict[str, Any]]:
    payload = json.loads(Path(mapping_path).read_text())
    rows = payload.get("mapping", payload)
    return {
        str(row["trace_id"]): row
        for row in rows
        if isinstance(row, dict) and row.get("trace_id")
    }


def _number(value: float) -> float | None:
    return None if math.isnan(value) else value


def analyse(
    labels_path: Path | str,
    judge_path: Path | str,
    mapping_path: Path | str,
    *,
    split: str,
    n_resamples: int = 2000,
    seed: int = 42,
) -> dict[str, Any]:
    human = {
        str(row["trace_id"]): str(row["label"])
        for row in _read_jsonl(labels_path)
        if row.get("label")
    }
    judge_rows = {
        str(row["trace_id"]): row
        for row in _read_jsonl(judge_path)
        if row.get("status", "completed") == "completed" and row.get("label")
    }
    mapping = _mapping_rows(mapping_path)
    selected = [
        trace_id
        for trace_id, row in mapping.items()
        if row.get("split") == split and trace_id in human and trace_id in judge_rows
    ]
    selected.sort()
    if not selected:
        raise ValueError(f"no completed judge labels for split {split!r}")
    y_human = [human[trace_id] for trace_id in selected]
    y_judge = [judge_rows[trace_id]["label"] for trace_id in selected]
    clusters = [str(mapping[trace_id].get("task_id", trace_id)) for trace_id in selected]
    kappa = cohens_kappa(y_human, y_judge)
    ci_low, ci_high = bootstrap_kappa_ci(
        y_human,
        y_judge,
        clusters=clusters,
        n_resamples=n_resamples,
        seed=seed,
    )
    per_arm: dict[str, dict[str, Any]] = {}
    for trace_id, human_label, judge_label in zip(selected, y_human, y_judge):
        arm = str(mapping[trace_id].get("arm", "unknown"))
        bucket = per_arm.setdefault(
            arm,
            {
                "n": 0,
                "correct": 0,
                "partial": 0,
                "wrong": 0,
                "human_correct": 0,
                "agreement": 0,
            },
        )
        bucket["n"] += 1
        bucket[judge_label] = bucket.get(judge_label, 0) + 1
        bucket["human_correct"] += 1 if human_label == "correct" else 0
        bucket["agreement"] += 1 if human_label == judge_label else 0
    for bucket in per_arm.values():
        bucket["judge_correct_rate"] = bucket["correct"] / bucket["n"]
        bucket["agreement_rate"] = bucket["agreement"] / bucket["n"]
    disagreements = [
        {
            "trace_id": trace_id,
            "task_id": mapping[trace_id].get("task_id"),
            "arm": mapping[trace_id].get("arm"),
            "human": human_label,
            "judge": judge_label,
            "rationale": judge_rows[trace_id].get("rationale", ""),
        }
        for trace_id, human_label, judge_label in zip(selected, y_human, y_judge)
        if human_label != judge_label
    ]
    return {
        "split": split,
        "n": len(selected),
        "human_label_counts": {
            label: sum(1 for value in y_human if value == label)
            for label in sorted(set(y_human))
        },
        "judge_label_counts": {
            label: sum(1 for value in y_judge if value == label)
            for label in sorted(set(y_judge))
        },
        "kappa": _number(kappa),
        "kappa_ci_low": _number(ci_low),
        "kappa_ci_high": _number(ci_high),
        "kappa_defined": not math.isnan(kappa),
        "raw_agreement": raw_agreement(y_human, y_judge),
        "confusion_matrix": confusion_matrix(y_human, y_judge),
        "per_arm_judge": per_arm,
        "disagreements": disagreements,
        "bootstrap": {
            "n_resamples": n_resamples,
            "seed": seed,
            "cluster": "task_id",
        },
        "judge": {
            "prompt_versions": sorted(
                {str(judge_rows[trace_id].get("prompt_version")) for trace_id in selected}
            ),
            "model_ids": sorted(
                {str(judge_rows[trace_id].get("model_id")) for trace_id in selected}
            ),
        },
    }


def _iso_mtime(path: Path | str) -> str:
    return datetime.fromtimestamp(Path(path).stat().st_mtime, timezone.utc).isoformat()


def analyse_relabel(
    original_judge_path: Path | str,
    relabel_judge_path: Path | str,
    relabel_mapping_path: Path | str,
    labels_path: Path | str,
    *,
    n_resamples: int = 2000,
    seed: int = 42,
) -> dict[str, Any]:
    """Compare the original report labels with a separate blind relabel batch."""

    human = {
        str(row["trace_id"]): str(row["label"])
        for row in _read_jsonl(labels_path)
        if row.get("label")
    }
    original = {
        str(row["trace_id"]): str(row["label"])
        for row in _read_jsonl(original_judge_path)
        if row.get("status", "completed") == "completed" and row.get("label")
    }
    relabel = {
        str(row["trace_id"]): str(row["label"])
        for row in _read_jsonl(relabel_judge_path)
        if row.get("status", "completed") == "completed" and row.get("label")
    }
    payload = json.loads(Path(relabel_mapping_path).read_text())
    rows = [
        row
        for row in payload.get("mapping", payload)
        if isinstance(row, dict)
        and row.get("original_trace_id") in human
        and row.get("original_trace_id") in original
        and row.get("trace_id") in relabel
    ]
    if not rows:
        raise ValueError("no relabel rows join the original labels and judge outputs")
    rows.sort(key=lambda row: str(row["trace_id"]))
    y_human = [human[str(row["original_trace_id"])] for row in rows]
    y_original = [original[str(row["original_trace_id"])] for row in rows]
    y_relabel = [relabel[str(row["trace_id"])] for row in rows]
    clusters = [str(row.get("task_id", row["trace_id"])) for row in rows]

    def _block(y_a: Sequence[str], y_b: Sequence[str]) -> dict[str, Any]:
        kappa = cohens_kappa(y_a, y_b)
        low, high = bootstrap_kappa_ci(
            y_a,
            y_b,
            clusters=clusters,
            n_resamples=n_resamples,
            seed=seed,
        )
        return {
            "n": len(y_a),
            "kappa": _number(kappa),
            "kappa_ci_low": _number(low),
            "kappa_ci_high": _number(high),
            "kappa_defined": not math.isnan(kappa),
            "raw_agreement": raw_agreement(y_a, y_b),
            "confusion_matrix": confusion_matrix(y_a, y_b),
        }

    report_mtime = Path(original_judge_path).stat().st_mtime
    relabel_mtime = Path(relabel_judge_path).stat().st_mtime
    label_mtime = Path(labels_path).stat().st_mtime
    return {
        "n": len(rows),
        "selection": {
            "seed": payload.get("seed"),
            "arms": {
                arm: sum(1 for row in rows if row.get("arm") == arm)
                for arm in sorted({str(row.get("arm")) for row in rows})
            },
        },
        "judge_self_consistency": _block(y_original, y_relabel),
        "human_vs_relabel_judge": _block(y_human, y_relabel),
        "label_counts": {
            "human": {
                label: sum(1 for value in y_human if value == label)
                for label in sorted(set(y_human))
            },
            "judge_original": {
                label: sum(1 for value in y_original if value == label)
                for label in sorted(set(y_original))
            },
            "judge_relabel": {
                label: sum(1 for value in y_relabel if value == label)
                for label in sorted(set(y_relabel))
            },
        },
        "gap": {
            "human_labels_completed_at": _iso_mtime(labels_path),
            "original_report_judge_completed_at": _iso_mtime(original_judge_path),
            "relabel_judge_completed_at": _iso_mtime(relabel_judge_path),
            "human_to_relabel_seconds": relabel_mtime - label_mtime,
            "report_judge_to_relabel_seconds": relabel_mtime - report_mtime,
            "design_gap": "one week",
        },
        "bootstrap": {
            "n_resamples": n_resamples,
            "seed": seed,
            "cluster": "task_id",
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 5 judge calibration")
    parser.add_argument("--labels", required=True)
    parser.add_argument("--judge", required=True)
    parser.add_argument("--mapping", required=True)
    parser.add_argument("--split", choices=("tune", "report"), required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--n-resamples", type=int, default=2000)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    result = analyse(
        args.labels,
        args.judge,
        args.mapping,
        split=args.split,
        n_resamples=args.n_resamples,
        seed=args.seed,
    )
    _write_json(args.out, result)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
