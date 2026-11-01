"""Offline tests for the Phase 5 multi-step set, loop, and blind packet."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.multistep_agent import MultiStepAgent, execute_fixture
from agent.multistep_tools import TOOLS_BY_NAME
from eval.config import MULTISTEP_EFFORT_BY_ARM
from eval.labeling import PacketError, build_packet
from eval.metrics import TokenPrice
from eval.multistep import (
    dependency_count,
    load_and_validate,
    load_cases,
    run_multistep,
)

ROOT = Path(__file__).resolve().parents[1]
MULTISTEP = ROOT / "data" / "multistep.jsonl"
PRICE = TokenPrice(input=1.0, output=2.0, cache_read=0.1, cache_write=1.5)
USAGE = {
    "input_tokens": 1000,
    "output_tokens": 500,
    "cache_read_input_tokens": 0,
    "cache_creation_input_tokens": 0,
    "thinking_tokens": 0,
}


def tool_block(name: str, arguments: dict, block_id: str = "toolu_test") -> dict:
    return {"type": "tool_use", "id": block_id, "name": name, "input": arguments}


def text_block(text: str) -> dict:
    return {"type": "text", "text": text}


def response(content: list, *, stop_reason: str = "tool_use", usage: dict | None = None):
    return SimpleNamespace(
        content=content,
        stop_reason=stop_reason,
        usage=dict(USAGE if usage is None else usage),
    )


class FakeMessages:
    def __init__(self, script: list):
        self.script = list(script)
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(copy.deepcopy(kwargs))
        if not self.script:
            raise AssertionError("unexpected API call: fake script is empty")
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class FakeClient:
    def __init__(self, script: list):
        self.messages = FakeMessages(script)


def make_agent(script: list, **kwargs) -> tuple[MultiStepAgent, FakeClient]:
    client = FakeClient(script)
    agent = MultiStepAgent(
        model="claude-haiku-5-5",
        arm="haiku-agent",
        effort="low",
        client=client,
        price=PRICE,
        sleep=lambda _seconds: None,
        **kwargs,
    )
    return agent, client


def load_case(task_id: str):
    return {case.id: case for case in load_cases(MULTISTEP)}[task_id]


def test_set_is_valid_and_split_by_task():
    cases = load_and_validate(MULTISTEP)
    assert len(cases) == 30
    assert sum(1 for case in cases if case.split == "tune") == 15
    assert sum(1 for case in cases if case.split == "report") == 15
    assert all(len(case.fixtures) >= 2 for case in cases)
    assert all(dependency_count(case) >= 1 for case in cases)
    for case in cases:
        assert case.success_criteria
        assert all(name in TOOLS_BY_NAME for name in case.tools)


def test_efforts_are_the_locked_selection():
    assert MULTISTEP_EFFORT_BY_ARM == {
        "haiku-agent": "low",
        "sonnet-agent": "api_default",
    }


def test_execute_fixture_matches_subset_and_reports_misses():
    case = load_case("ms-001")
    result, is_error, index, reused = execute_fixture(
        "search_flights",
        {"origin": "Boston", "destination": "Denver", "date": "2026-11-12"},
        case.fixtures,
        set(),
    )
    assert not is_error
    assert result["flights"][0]["flight_id"] == "FL-913"
    assert index == 0
    assert not reused

    result, is_error, index, _ = execute_fixture(
        "book_flight",
        {"flight_id": "FL-000", "passenger_name": "Ada", "seat": "1A"},
        case.fixtures,
        set(),
    )
    assert is_error
    assert index is None
    assert "No scripted mock result" in result["error"]


def test_empty_match_means_match_any_arguments():
    case = load_case("ms-021")
    result, is_error, index, _ = execute_fixture(
        "search_contacts",
        {"name": "Amara Okafor"},
        case.fixtures,
        set(),
    )
    assert not is_error
    assert index == 0
    assert result["contact_id"] == "A-27"


def test_loop_executes_two_dependent_calls_and_returns_final_answer():
    case = load_case("ms-001")
    agent, client = make_agent(
        [
            response([tool_block("search_flights", {"origin": "Boston", "destination": "Denver", "date": "2026-11-12"}, "t1")]),
            response([tool_block("book_flight", {"flight_id": "FL-913", "passenger_name": "Ada Lovelace", "seat": "14C"}, "t2")]),
            response([text_block("Booked FL-913. Confirmation CNF-4471.")], stop_reason="end_turn"),
        ]
    )
    trace = agent.run(case.to_task())
    assert trace.completed
    assert trace.n_steps == 3
    assert trace.n_tool_calls == 2
    assert trace.final_answer.startswith("Booked FL-913")
    assert trace.cost_usd > 0
    assert trace.tool_results[0]["result"]["flights"][0]["flight_id"] == "FL-913"
    assert trace.tool_results[1]["result"]["confirmation_code"] == "CNF-4471"
    # The second request carries the first scripted result back to the model.
    second_messages = client.messages.calls[1]["messages"]
    assert second_messages[-1]["role"] == "user"
    assert "FL-913" in second_messages[-1]["content"][0]["content"]


def test_schema_repair_feeds_validation_error_and_retries():
    case = load_case("ms-001")
    agent, client = make_agent(
        [
            response([tool_block("book_flight", {"flight_id": "FL-913"}, "t1")]),
            response([tool_block("search_flights", {"origin": "Boston", "destination": "Denver", "date": "2026-11-12"}, "t2")]),
            response([tool_block("book_flight", {"flight_id": "FL-913", "passenger_name": "Ada Lovelace", "seat": "14C"}, "t3")]),
            response([text_block("Booked. CNF-4471.")], stop_reason="end_turn"),
        ]
    )
    trace = agent.run(case.to_task())
    assert trace.schema_repair_retries == 1
    assert trace.completed
    assert trace.validation_errors
    repair_turn = client.messages.calls[1]["messages"][-1]["content"][0]
    assert repair_turn["is_error"] is True
    assert "seat" in repair_turn["content"]


def test_truncation_is_recorded_as_incomplete():
    case = load_case("ms-001")
    agent, _ = make_agent(
        [response([tool_block("search_flights", {"origin": "Boston", "destination": "Denver", "date": "2026-11-12"})], stop_reason="max_tokens")]
    )
    trace = agent.run(case.to_task())
    assert trace.truncated
    assert not trace.completed
    assert trace.failure_reason == "truncated"


def test_max_steps_is_recorded():
    case = load_case("ms-001")
    agent, _ = make_agent(
        [
            response([tool_block("search_flights", {"origin": "Boston", "destination": "Denver", "date": "2026-11-12"})]),
            response([tool_block("search_flights", {"origin": "Boston", "destination": "Denver", "date": "2026-11-12"})]),
        ],
        max_steps=2,
    )
    trace = agent.run(case.to_task())
    assert trace.max_steps_reached
    assert not trace.completed


def test_fixture_miss_is_visible_and_does_not_crash():
    case = load_case("ms-001")
    agent, _ = make_agent(
        [
            response([tool_block("book_flight", {"flight_id": "FL-000", "passenger_name": "Ada", "seat": "1A"})]),
            response([text_block("I could not book the flight.")], stop_reason="end_turn"),
        ]
    )
    trace = agent.run(case.to_task())
    assert trace.fixture_misses
    assert trace.completed


def test_runner_writes_checkpoints_and_aggregates(tmp_path):
    case = load_case("ms-001")
    client = FakeClient(
        [
            response([tool_block("search_flights", {"origin": "Boston", "destination": "Denver", "date": "2026-11-12"})]),
            response([tool_block("book_flight", {"flight_id": "FL-913", "passenger_name": "Ada Lovelace", "seat": "14C"})]),
            response([text_block("Booked. CNF-4471.")], stop_reason="end_turn"),
        ]
    )
    result = run_multistep(
        MULTISTEP,
        tmp_path,
        ["haiku-agent"],
        client=client,
        pricing={"claude-haiku-5-5": PRICE},
        cost_ceiling=1.0,
        run_id="test-multistep",
        case_ids=[case.id],
    )
    run_dir = Path(result["run_dir"])
    assert result["run_status"] == "completed"
    assert result["arms"]["haiku-agent"]["n_traces"] == 1
    assert (run_dir / "summary.json").exists()
    assert (run_dir / "traces.jsonl").exists()
    assert (run_dir / "haiku-agent" / "checkpoints" / "ms-001.json").exists()
    summary = json.loads((run_dir / "summary.json").read_text())
    assert summary["arms"]["haiku-agent"]["n_completed"] == 1
    assert summary["arms"]["haiku-agent"]["input_tokens_total"] == 3000


def test_packet_is_blind_and_deterministic(tmp_path):
    cases = {case.id: case for case in load_cases(MULTISTEP)}
    cases_path = tmp_path / "cases.jsonl"
    with cases_path.open("w", encoding="utf-8") as handle:
        for task_id in ("ms-001", "ms-002"):
            handle.write(json.dumps(cases[task_id].to_task()) + "\n")
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    rows = []
    for task_id, arm, model in (
        ("ms-001", "haiku-agent", "claude-haiku-5-5"),
        ("ms-002", "sonnet-agent", "claude-sonnet-5-5"),
    ):
        rows.append(
            {
                "task_id": task_id,
                "arm": arm,
                "model_id": model,
                "effort": "low",
                "trace": {
                    "steps": [],
                    "final_answer": "done",
                    "completed": True,
                    "fixture_misses": [],
                    "schema_repair_retries": 0,
                },
            }
        )
    (run_dir / "traces.jsonl").write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8"
    )
    packet = tmp_path / "packet"
    mapping = tmp_path / "private" / "mapping.json"
    metadata = build_packet(
        run_dir,
        cases_path,
        packet,
        mapping,
        ROOT / "docs" / "MULTISTEP_RUBRIC.md",
    )
    assert metadata["n_traces"] == 2
    assert metadata["n_tasks"] == 2
    packet_text = (packet / "traces.jsonl").read_text().lower()
    assert "haiku" not in packet_text
    assert "sonnet" not in packet_text
    assert "claude-" not in packet_text
    labels = [
        json.loads(line)
        for line in (packet / "labels.jsonl").read_text().splitlines()
        if line.strip()
    ]
    assert len(labels) == 2
    assert all(row["label"] == "" for row in labels)
    private = json.loads(mapping.read_text())
    assert {row["arm"] for row in private["mapping"]} == {
        "haiku-agent",
        "sonnet-agent",
    }

    with pytest.raises(PacketError):
        build_packet(
            run_dir,
            cases_path,
            packet,
            packet / "mapping.json",
            ROOT / "docs" / "MULTISTEP_RUBRIC.md",
        )
