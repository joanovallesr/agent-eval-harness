"""Offline tests for the Phase-5 judge and calibration math."""

from __future__ import annotations

import json
import math
from types import SimpleNamespace

import pytest

from eval.calibration import (
    analyse,
    analyse_relabel,
    bootstrap_kappa_ci,
    cohens_kappa,
    confusion_matrix,
    raw_agreement,
)
from eval.judge import (
    JudgeError,
    build_judge_messages,
    judge_trace,
    parse_verdict,
    run_judge,
)
from eval.metrics import TokenPrice

PRICE = TokenPrice(input=0.10, output=0.50)


def packet_trace(labels: str = "correct") -> dict:
    return {
        "trace_id": "trace-001",
        "task_id": "ms-001",
        "query": "Book the cheapest flight.",
        "offered_tools": ["search_flights", "book_flight"],
        "mock_results": [],
        "success_criteria": "Books FL-913.",
        "trace": {"steps": [], "final_answer": "Booked."},
    }


class FakeCompletions:
    def __init__(self, script: list):
        self.script = list(script)
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if not self.script:
            raise AssertionError("unexpected judge call")
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class FakeChat:
    def __init__(self, script: list):
        self.completions = FakeCompletions(script)


class FakeOpenAI:
    def __init__(self, script: list):
        self.chat = FakeChat(script)


def response(content: str, *, prompt_tokens: int = 100, completion_tokens: int = 20):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        usage=SimpleNamespace(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        ),
    )


def test_kappa_perfect_agreement_is_one():
    labels = ["correct", "wrong", "partial"] * 10
    assert cohens_kappa(labels, labels) == pytest.approx(1.0)


def test_kappa_known_value():
    human = ["correct", "correct", "wrong", "wrong"]
    judge = ["correct", "wrong", "wrong", "wrong"]
    # po = 3/4, pe = 1/2, kappa = 0.5
    assert cohens_kappa(human, judge) == pytest.approx(0.5)


def test_kappa_is_undefined_for_one_class_on_both_sides():
    human = ["correct"] * 10
    judge = ["correct"] * 10
    assert math.isnan(cohens_kappa(human, judge))


def test_one_class_human_with_mixed_judge_gives_zero():
    human = ["correct"] * 10
    judge = ["correct"] * 8 + ["wrong"] * 2
    assert cohens_kappa(human, judge) == pytest.approx(0.0)
    assert raw_agreement(human, judge) == pytest.approx(0.8)


def test_confusion_matrix_counts_and_bootstrap_interval():
    human = ["correct", "correct", "wrong", "partial"]
    judge = ["correct", "wrong", "wrong", "partial"]
    matrix = confusion_matrix(human, judge)
    counts = matrix["by_human"]
    assert counts["correct"]["correct"] == 1
    assert counts["correct"]["wrong"] == 1
    assert counts["wrong"]["wrong"] == 1
    assert counts["partial"]["partial"] == 1
    low, high = bootstrap_kappa_ci(
        human, judge, clusters=["a", "a", "b", "b"], n_resamples=200
    )
    assert low <= cohens_kappa(human, judge) <= high


def test_judge_prompt_is_blind_and_parses_fenced_json():
    messages = build_judge_messages(packet_trace(), "# Rubric\nLabel it.")
    joined = json.dumps(messages).lower()
    assert "haiku" not in joined
    assert "sonnet" not in joined
    label, rationale = parse_verdict(
        '```json\n{"label": "partial", "rationale": "Missed the booking."}\n```'
    )
    assert label == "partial"
    assert "booking" in rationale


def test_judge_trace_records_usage_and_cost():
    client = FakeOpenAI([response('{"label": "correct", "rationale": "Done."}')])
    result = judge_trace(packet_trace(), "# Rubric", client=client, price=PRICE)
    assert result.label == "correct"
    assert result.status == "completed"
    assert result.input_tokens == 100
    assert result.output_tokens == 20
    assert result.cost_usd == pytest.approx((100 * 0.10 + 20 * 0.50) / 1_000_000)
    assert "sonnet" not in json.dumps(client.chat.completions.calls).lower()


def test_judge_trace_retries_malformed_json():
    client = FakeOpenAI(
        [
            response("not json"),
            response('{"label": "wrong", "rationale": "No booking."}'),
        ]
    )
    result = judge_trace(packet_trace(), "# Rubric", client=client, price=PRICE)
    assert result.label == "wrong"
    assert result.parse_retries == 1
    assert result.request_count == 2


def test_judge_trace_returns_parse_failure_after_retries():
    client = FakeOpenAI([response("not json"), response("still not json")])
    result = judge_trace(
        packet_trace(),
        "# Rubric",
        client=client,
        price=PRICE,
        max_parse_retries=1,
    )
    assert result.status == "parse_failure"
    assert result.label == ""


def test_run_judge_stops_before_crossing_the_cumulative_ceiling(tmp_path):
    packet = tmp_path / "packet"
    packet.mkdir()
    (packet / "traces.jsonl").write_text(json.dumps(packet_trace()) + "\n")
    mapping = tmp_path / "mapping.json"
    mapping.write_text(
        json.dumps(
            {
                "mapping": [
                    {
                        "trace_id": "trace-001",
                        "split": "report",
                        "task_id": "ms-001",
                        "arm": "haiku-agent",
                    }
                ]
            }
        )
    )
    rubric = tmp_path / "RUBRIC.md"
    rubric.write_text("# Rubric")
    client = FakeOpenAI([])
    with pytest.raises(JudgeError):
        run_judge(
            packet,
            mapping,
            rubric,
            tmp_path / "out.jsonl",
            split="report",
            client=client,
            price=PRICE,
            cost_ceiling=0.01,
            prior_cost_usd=0.0095,
        )
    assert not client.chat.completions.calls


def test_analyse_joins_split_and_arm(tmp_path):
    labels = tmp_path / "labels.jsonl"
    judge = tmp_path / "judge.jsonl"
    mapping = tmp_path / "mapping.json"
    labels.write_text(
        "\n".join(
            [
                json.dumps({"trace_id": "t1", "label": "correct"}),
                json.dumps({"trace_id": "t2", "label": "correct"}),
                json.dumps({"trace_id": "t3", "label": "wrong"}),
            ]
        )
        + "\n"
    )
    judge.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "trace_id": "t1",
                        "label": "correct",
                        "status": "completed",
                        "prompt_version": "phase5-judge-v1",
                        "model_id": "gpt-6-luna",
                    }
                ),
                json.dumps(
                    {
                        "trace_id": "t2",
                        "label": "wrong",
                        "status": "completed",
                        "prompt_version": "phase5-judge-v1",
                        "model_id": "gpt-6-luna",
                        "rationale": "missed",
                    }
                ),
                json.dumps(
                    {
                        "trace_id": "t3",
                        "label": "wrong",
                        "status": "completed",
                        "prompt_version": "phase5-judge-v1",
                        "model_id": "gpt-6-luna",
                    }
                ),
            ]
        )
        + "\n"
    )
    mapping.write_text(
        json.dumps(
            {
                "mapping": [
                    {"trace_id": "t1", "split": "report", "task_id": "a", "arm": "haiku-agent"},
                    {"trace_id": "t2", "split": "report", "task_id": "a", "arm": "sonnet-agent"},
                    {"trace_id": "t3", "split": "tune", "task_id": "b", "arm": "haiku-agent"},
                ]
            }
        )
    )
    result = analyse(
        labels,
        judge,
        mapping,
        split="report",
        n_resamples=100,
    )
    assert result["n"] == 2
    assert result["raw_agreement"] == 0.5
    assert result["per_arm_judge"]["haiku-agent"]["n"] == 1
    assert result["per_arm_judge"]["sonnet-agent"]["n"] == 1
    assert result["disagreements"][0]["trace_id"] == "t2"
    assert result["kappa"] == pytest.approx(0.0)
    assert result["kappa_defined"] is True


def test_analyse_relabel_joins_new_ids_and_records_gap(tmp_path):
    labels = tmp_path / "labels.jsonl"
    labels.write_text(
        json.dumps({"trace_id": "t1", "label": "correct"}) + "\n"
        + json.dumps({"trace_id": "t2", "label": "correct"}) + "\n"
    )
    original = tmp_path / "original.jsonl"
    original.write_text(
        json.dumps({"trace_id": "t1", "label": "correct", "status": "completed"}) + "\n"
        + json.dumps({"trace_id": "t2", "label": "correct", "status": "completed"}) + "\n"
    )
    relabel = tmp_path / "relabel.jsonl"
    relabel.write_text(
        json.dumps({"trace_id": "r1", "label": "correct", "status": "completed"}) + "\n"
        + json.dumps({"trace_id": "r2", "label": "wrong", "status": "completed"}) + "\n"
    )
    mapping = tmp_path / "relabel-mapping.json"
    mapping.write_text(
        json.dumps(
            {
                "seed": 7,
                "mapping": [
                    {
                        "trace_id": "r1",
                        "original_trace_id": "t1",
                        "task_id": "a",
                        "arm": "haiku-agent",
                        "split": "report",
                    },
                    {
                        "trace_id": "r2",
                        "original_trace_id": "t2",
                        "task_id": "b",
                        "arm": "sonnet-agent",
                        "split": "report",
                    },
                ],
            }
        )
    )
    result = analyse_relabel(
        original,
        relabel,
        mapping,
        labels,
        n_resamples=100,
    )
    assert result["n"] == 2
    assert result["selection"]["arms"] == {"haiku-agent": 1, "sonnet-agent": 1}
    assert result["judge_self_consistency"]["raw_agreement"] == 0.5
    assert result["human_vs_relabel_judge"]["raw_agreement"] == 0.5
    assert result["gap"]["design_gap"] == "one week"
    assert result["gap"]["human_to_relabel_seconds"] >= 0
