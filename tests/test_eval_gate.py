"""Offline tests for the Phase-7 advisory eval gate.

These tests never call the API. They cover the comment formatter, the
baseline-diff logic, baseline writing, and the gate's mini-set-only guards.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from eval.gate import (
    COMMENT_MARKER,
    GATE_ARM,
    GATE_BASELINE_PATH,
    GATE_COST_CEILING_USD,
    GATE_EFFORT,
    GATE_PRICING_PATH,
    GateError,
    baseline_from_view,
    compare_to_baseline,
    load_baseline,
    main,
    render_comment,
    run_gate,
    view_from_payload,
    write_baseline_from_payload,
)
from eval.metrics import TokenPrice
from eval.mini_set import load_and_validate

MINI_SET = Path(__file__).resolve().parents[1] / "data" / "miniset.jsonl"
PRICE = TokenPrice(input=1.0, output=2.0)
USAGE = {"input_tokens": 1000, "output_tokens": 500}
CASE_COST = 0.002


def _record(
    task_id: str,
    *,
    exact: int,
    partial: float,
    cost: float = 0.001,
    status: str = "completed",
) -> dict:
    return {
        "task_id": task_id,
        "status": status,
        "exact": exact,
        "partial": partial,
        "cost_usd": cost,
        "format_failure": False,
        "failure_reason": "",
    }


def _payload(
    records: list[dict],
    *,
    run_status: str = "completed",
    abort_reason: str | None = None,
    pricing_path: str = GATE_PRICING_PATH,
) -> dict:
    completed = [record for record in records if record["status"] == "completed"]
    n_cases = len({record["task_id"] for record in completed})
    exact_mean = (
        sum(float(record["exact"]) for record in completed) / len(completed)
        if completed
        else 0.0
    )
    partial_mean = (
        sum(float(record["partial"]) for record in completed) / len(completed)
        if completed
        else 0.0
    )
    cost = sum(float(record["cost_usd"]) for record in records)
    arm_payload = {
        "run": {
            "run_id": "test-run",
            "git_commit": "abc123",
            "arm": GATE_ARM,
            "model_id": "claude-haiku-5-5",
            "effort": GATE_EFFORT,
            "max_tokens": 1024,
            "match_spec_version": "1.1",
            "prompt_version": "phase2-mini-v1",
            "pricing_basis": "list_sync",
            "run_status": run_status,
            "abort_reason": abort_reason,
        },
        "summary": {
            "n_cases": n_cases,
            "n_cases_completed": len({record["task_id"] for record in completed}),
            "n_cases_aborted": len(records) - len(completed),
            "exact_match_mean": exact_mean,
            "partial_mean": partial_mean,
            "cost_usd_total": cost,
            "format_failure_rate": 0.0,
            "format_failure_breakdown": {},
        },
        "per_case": records,
    }
    return {
        "run": {
            "run_id": "test-run",
            "git_commit": "abc123",
            "run_status": run_status,
            "abort_reason": abort_reason,
            "pricing_basis": "list_sync",
            "pricing_path": pricing_path,
            "max_tokens": 1024,
            "match_spec_version": "1.1",
            "cost_ceiling_usd": 0.25,
        },
        "arms": {GATE_ARM: arm_payload},
    }


def _view() -> dict:
    return view_from_payload(
        _payload(
            [
                _record("mini-001", exact=1, partial=1.0, cost=0.002),
                _record("mini-002", exact=0, partial=0.5, cost=0.003),
                _record("mini-003", exact=1, partial=1.0, cost=0.001),
            ]
        )
    )


def test_view_reads_combined_results_contract():
    view = _view()

    assert view["arm"] == GATE_ARM
    assert view["model_id"] == "claude-haiku-5-5"
    assert view["effort"] == GATE_EFFORT
    assert view["max_tokens"] == 1024
    assert view["match_spec_version"] == "1.1"
    assert view["pricing_file"] == GATE_PRICING_PATH
    assert view["miniset_sha256"]
    assert view["n_cases"] == 3
    assert view["exact_matches"] == 2
    assert view["exact_match_mean"] == pytest.approx(2 / 3)
    assert view["partial_mean"] == pytest.approx(2.5 / 3)
    assert view["cost_usd_total"] == pytest.approx(0.006)
    assert view["cases"]["mini-002"] == {"exact": 0.0, "partial": 0.5}


def test_view_reads_single_arm_results_file():
    payload = _payload([_record("mini-001", exact=1, partial=1.0)])
    view = view_from_payload(payload["arms"][GATE_ARM])

    assert view["n_cases"] == 1
    assert view["cases"]["mini-001"]["exact"] == 1.0


def test_diff_lists_every_per_case_change_by_id():
    baseline = baseline_from_view(_view())
    changed = view_from_payload(
        _payload(
            [
                _record("mini-001", exact=1, partial=1.0, cost=0.002),
                _record("mini-002", exact=1, partial=1.0, cost=0.003),
                _record("mini-004", exact=0, partial=0.25, cost=0.001),
            ]
        )
    )

    diff = compare_to_baseline(changed, baseline)

    assert diff is not None
    assert diff["compatible"] is True
    assert diff["exact_delta"] == pytest.approx((2 / 3) - (2 / 3))
    changes = {change["id"]: change for change in diff["changes"]}
    assert set(changes) == {"mini-002", "mini-003", "mini-004"}
    assert changes["mini-002"]["kind"] == "changed"
    assert changes["mini-002"]["baseline_exact"] == 0.0
    assert changes["mini-002"]["run_exact"] == 1.0
    assert changes["mini-003"]["kind"] == "missing_from_run"
    assert changes["mini-004"]["kind"] == "new_case"


def test_no_diff_when_run_matches_baseline():
    view = _view()
    baseline = baseline_from_view(view)

    diff = compare_to_baseline(view, baseline)

    assert diff is not None
    assert diff["compatible"] is True
    assert diff["changes"] == []
    assert diff["exact_delta"] == pytest.approx(0.0)
    assert diff["partial_delta"] == pytest.approx(0.0)
    assert diff["cost_delta"] == pytest.approx(0.0)


def test_diff_flags_incomparable_configuration_and_checksum():
    baseline = baseline_from_view(_view())
    baseline["miniset_sha256"] = "not-the-same"
    baseline["pricing_file"] = "config/other.json"

    diff = compare_to_baseline(_view(), baseline)

    assert diff is not None
    assert diff["compatible"] is False
    fields = {mismatch["field"] for mismatch in diff["mismatches"]}
    assert fields == {"mini-set checksum", "pricing file"}


def test_comment_reports_scores_diff_changes_and_cost():
    view = _view()
    baseline = baseline_from_view(view)

    comment = render_comment(
        status="completed",
        view=view,
        baseline=baseline,
        diff=compare_to_baseline(view, baseline),
    )

    assert comment.startswith(COMMENT_MARKER)
    assert "## Eval gate (advisory)" in comment
    assert "Exact match | 66.7% (2/3) | 66.7% (2/3) | +0.00 pts" in comment
    assert "Partial | 83.3% | 83.3% | +0.00 pts" in comment
    assert "List-price cost | $0.006000 | $0.006000 | +$0.000000" in comment
    assert "### Per-case changes vs baseline" in comment
    assert "- No per-case changes." in comment
    assert "MATCH_SPEC `1.1` (deterministic scorer; no judge)" in comment
    assert "`haiku-agent`" in comment
    assert "`low` (`output_config.effort`)" in comment
    assert "held-out test split not run" in comment
    assert "$0.250000" in comment
    assert "never fails the build" in comment
    assert "no judge verdict" in comment


def test_comment_prints_changed_case_ids():
    view = _view()
    baseline = baseline_from_view(view)
    changed = view_from_payload(
        _payload(
            [
                _record("mini-001", exact=1, partial=1.0),
                _record("mini-002", exact=1, partial=1.0),
                _record("mini-003", exact=1, partial=1.0),
            ]
        )
    )

    comment = render_comment(
        status="completed",
        view=changed,
        baseline=baseline,
        diff=compare_to_baseline(changed, baseline),
    )

    assert "| `mini-002` | 0 → 1 | 0.50 → 1.00 |" in comment


def test_comment_shows_the_run_cost_ceiling_not_only_the_constant():
    payload = _payload([_record("mini-001", exact=1, partial=1.0)])
    payload["run"]["cost_ceiling_usd"] = 0.10
    view = view_from_payload(payload)

    comment = render_comment(status="completed", view=view, baseline=None)

    assert "| Cost ceiling | $0.100000 |" in comment


def test_comment_reports_missing_baseline():
    view = _view()

    comment = render_comment(status="completed", view=view, baseline=None)

    assert f"`{GATE_BASELINE_PATH}` is missing" in comment
    assert "reports the run without a diff" in comment


def test_comment_reports_skipped_secret_without_scores():
    comment = render_comment(
        status="skipped",
        message="`ANTHROPIC_API_KEY` is not configured for this workflow run.",
    )

    assert "**Result:** skipped." in comment
    assert "ANTHROPIC_API_KEY" in comment
    assert "No API calls were made" in comment
    assert "`low` (`output_config.effort`)" in comment


def test_comment_reports_cost_ceiling_stop():
    view = _view()

    comment = render_comment(
        status="cost_ceiling",
        view=view,
        message="The harness stopped at the hard $0.250000 per-run ceiling.",
    )

    assert "**Result:** stopped at the cost ceiling." in comment
    assert "hard $0.250000 per-run ceiling" in comment
    assert "Exact match" in comment


def test_comment_reports_failure_without_traceback():
    comment = render_comment(
        status="failed",
        message="The eval gate could not complete: `RuntimeError: boom`.",
    )

    assert "**Result:** failed (reported, not build-failing)." in comment
    assert "RuntimeError: boom" in comment
    assert "No run scores are available" in comment


def test_write_baseline_records_configuration_and_checksum(tmp_path: Path):
    destination = tmp_path / "baseline.json"

    baseline = write_baseline_from_payload(
        _payload(
            [
                _record("mini-001", exact=1, partial=1.0),
                _record("mini-002", exact=0, partial=0.5),
            ]
        ),
        destination,
        miniset_path=MINI_SET,
    )

    loaded = load_baseline(destination)
    assert loaded is not None
    assert loaded["model_id"] == "claude-haiku-5-5"
    assert loaded["effort"] == GATE_EFFORT
    assert loaded["max_tokens"] == 1024
    assert loaded["match_spec_version"] == "1.1"
    assert loaded["pricing_file"] == GATE_PRICING_PATH
    assert loaded["miniset_path"] == str(MINI_SET)
    assert loaded["miniset_sha256"] == baseline["miniset_sha256"]
    assert loaded["n_cases"] == 2
    assert loaded["cases"]["mini-002"]["partial"] == 0.5


def test_write_baseline_refuses_incomplete_run(tmp_path: Path):
    with pytest.raises(GateError, match="incomplete run"):
        write_baseline_from_payload(
            _payload(
                [_record("mini-001", exact=0, partial=0.0, status="aborted")],
                run_status="aborted",
                abort_reason="cost ceiling",
            ),
            tmp_path / "baseline.json",
            miniset_path=MINI_SET,
        )


def test_write_baseline_requires_force_to_replace(tmp_path: Path):
    destination = tmp_path / "baseline.json"
    payload = _payload([_record("mini-001", exact=1, partial=1.0)])
    write_baseline_from_payload(payload, destination, miniset_path=MINI_SET)

    with pytest.raises(GateError, match="already exists"):
        write_baseline_from_payload(payload, destination, miniset_path=MINI_SET)

    write_baseline_from_payload(
        payload, destination, miniset_path=MINI_SET, force=True
    )


def test_dry_run_renders_comment_from_saved_results(tmp_path: Path):
    results_path = tmp_path / "results.json"
    results_path.write_text(json.dumps(_payload([_record("mini-001", exact=1, partial=1.0)])))
    baseline_path = tmp_path / "baseline.json"
    write_baseline_from_payload(
        json.loads(results_path.read_text()),
        baseline_path,
        miniset_path=MINI_SET,
    )
    comment_path = tmp_path / "comment.md"

    code = main(
        [
            "--dry-run",
            "--results",
            str(results_path),
            "--baseline",
            str(baseline_path),
            "--out-comment",
            str(comment_path),
        ]
    )

    assert code == 0
    body = comment_path.read_text()
    assert body.startswith(COMMENT_MARKER)
    assert "mini-001" not in body  # no per-case changes, but config names the set
    assert "No per-case changes." in body


def test_dry_run_renders_an_aborted_results_file_as_cost_ceiling(tmp_path: Path):
    results_path = tmp_path / "results.json"
    results_path.write_text(
        json.dumps(
            _payload(
                [_record("mini-001", exact=0, partial=0.0, status="aborted")],
                run_status="aborted",
                abort_reason="cost ceiling reached before mini-002",
            )
        )
    )
    comment_path = tmp_path / "comment.md"

    code = main(
        [
            "--dry-run",
            "--results",
            str(results_path),
            "--baseline",
            str(tmp_path / "missing-baseline.json"),
            "--out-comment",
            str(comment_path),
        ]
    )

    body = comment_path.read_text()
    assert code == 0
    assert "**Result:** stopped at the cost ceiling." in body
    assert "cost ceiling reached before mini-002" in body


def test_gate_skips_without_a_key_without_calling_the_api(tmp_path: Path):
    status, view, message = run_gate(
        out_dir=tmp_path,
        api_key_present=False,
    )

    assert status == "skipped"
    assert view is None
    assert "ANTHROPIC_API_KEY" in (message or "")


def test_gate_refuses_a_non_miniset_subset(tmp_path: Path):
    other = tmp_path / "dev.jsonl"
    other.write_text("")

    with pytest.raises(GateError, match="mini-set only"):
        run_gate(
            out_dir=tmp_path,
            subset_path=other,
            api_key_present=True,
            pricing={},
        )


def test_gate_refuses_a_cost_ceiling_above_the_hard_limit(tmp_path: Path):
    with pytest.raises(GateError, match="cost ceiling"):
        run_gate(
            out_dir=tmp_path,
            cost_ceiling=GATE_COST_CEILING_USD + 0.01,
            api_key_present=True,
            pricing={},
        )


class _FakeMessages:
    def __init__(self, script: list) -> None:
        self.script = list(script)
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if not self.script:
            raise AssertionError("unexpected API call: fake script is empty")
        return self.script.pop(0)


class _FakeClient:
    def __init__(self, script: list) -> None:
        self.messages = _FakeMessages(script)


def _gold_response(case) -> SimpleNamespace:
    calls = json.loads(case.answers)
    blocks = [
        {
            "type": "tool_use",
            "id": f"toolu_{index}",
            "name": call["name"],
            "input": call["arguments"],
        }
        for index, call in enumerate(calls)
    ]
    return SimpleNamespace(
        content=blocks,
        stop_reason="tool_use",
        usage=dict(USAGE),
    )


def test_gate_runs_only_haiku_agent_at_low_effort_offline(tmp_path: Path):
    cases = load_and_validate(MINI_SET)
    client = _FakeClient([_gold_response(case) for case in cases])

    status, view, message = run_gate(
        out_dir=tmp_path,
        run_id="gate-offline",
        api_key_present=True,
        client=client,
        pricing={"claude-haiku-5-5": PRICE},
    )

    assert status == "completed"
    assert message is None
    assert view is not None
    assert view["effort"] == GATE_EFFORT
    assert view["max_tokens"] == 1024
    assert view["n_cases"] == 20
    assert view["exact_match_mean"] == 1.0
    assert view["cost_usd_total"] == pytest.approx(20 * CASE_COST)
    assert len(client.messages.calls) == 20
    assert client.messages.calls[0]["max_tokens"] == 1024
    assert client.messages.calls[0]["output_config"] == {"effort": "low"}


def test_gate_reports_a_cost_ceiling_stop_offline(tmp_path: Path):
    case = load_and_validate(MINI_SET)[0]
    client = _FakeClient([_gold_response(case)])

    status, view, message = run_gate(
        out_dir=tmp_path,
        run_id="gate-ceiling",
        cost_ceiling=1e-9,
        api_key_present=True,
        client=client,
        pricing={"claude-haiku-5-5": PRICE},
    )

    assert status == "cost_ceiling"
    assert view is not None
    assert "ceiling" in (message or "")
    assert view["n_cases_aborted"] == 1
