"""Multi-step agent loop with scripted mock tools.

This loop is the Phase 5 trace generator. It differs from the single-turn
Phase 2 loop in exactly one way: every emitted tool call is executed against a
deterministic fixture carried by the task, and the result is appended to the
conversation so the model can continue. No fixture is invented here, and no
live backend is contacted.

Both multi-step arms run this same loop. The only per-arm difference is the
model id and the effort setting.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Mapping, Sequence

from agent.agent import (
    CostCeilingReached,
    RequestFailed,
    _content_blocks,
    _extract_calls,
    _get,
    _is_infrastructure_error,
    _text,
    validate_tool_calls,
)
from agent.prompts import repair_prompt
from eval.config import (
    MAX_INFRASTRUCTURE_RETRIES,
    MAX_SCHEMA_REPAIR_RETRIES,
    MULTISTEP_MAX_STEPS,
    MULTISTEP_MAX_TOKENS,
    MULTISTEP_PROMPT_VERSION,
    effort_request_params,
)
from eval.metrics import TokenPrice, add_usage, calculate_cost, normalize_usage

MULTISTEP_SYSTEM_PROMPT = """\
You are a tool-using assistant working through a multi-step task. Your tool
calls are executed and the results are returned to you. Continue until the
task is complete, then reply in plain prose with the result the user asked for.

Rules:
- Use only the tools offered in this request.
- When a later call needs a value from an earlier result, make the earlier
  call first and wait for its result. Do not guess identifiers or amounts.
- Do not invent tool results. Use only the results returned in the
  conversation.
- Keep the final answer short and include the identifiers, amounts, and dates
  the task asks for.
"""


@dataclass
class MultiStepTrace:
    """One complete multi-step trace, with diagnostics but no score."""

    task_id: str
    arm: str = ""
    model_id: str = ""
    effort: str = ""
    effort_transmitted: bool = False
    prompt_version: str = MULTISTEP_PROMPT_VERSION
    offered_tools: list[str] = field(default_factory=list)
    messages: list[dict[str, Any]] = field(default_factory=list)
    steps: list[dict[str, Any]] = field(default_factory=list)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    tool_results: list[dict[str, Any]] = field(default_factory=list)
    final_answer: str = ""
    n_steps: int = 0
    n_tool_calls: int = 0
    schema_repair_retries: int = 0
    infrastructure_retries: int = 0
    request_count: int = 0
    validation_errors: list[dict[str, Any]] = field(default_factory=list)
    fixture_misses: list[dict[str, Any]] = field(default_factory=list)
    truncated: bool = False
    refusal: bool = False
    format_failure: bool = False
    max_steps_reached: bool = False
    completed: bool = False
    failure_reason: str = ""
    error: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    raw_usage: dict[str, Any] = field(default_factory=dict)
    cost_usd: float = 0.0
    price_configured: bool = False
    latency_s: float = 0.0
    stopped_early: bool = False
    cost_ceiling_reached: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _result_content(result: Any) -> str:
    return json.dumps(result, sort_keys=True)


def _repair_tool_results(
    blocks: Sequence[Any],
    errors: list[dict[str, Any]],
    schemas: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    names = {error.get("tool") for error in errors if error.get("tool")}
    relevant = [schema for schema in schemas if schema["name"] in names] or schemas
    message = repair_prompt(
        json.dumps(errors, indent=2, sort_keys=True),
        json.dumps(relevant, indent=2, sort_keys=True),
    )
    results: list[dict[str, Any]] = []
    for index, block in enumerate(blocks):
        if not isinstance(block, dict) or block.get("type") != "tool_use":
            continue
        tool_use_id = block.get("id")
        if not isinstance(tool_use_id, str) or not tool_use_id:
            continue
        call_errors = [
            error for error in errors if error.get("call_index") == index
        ]
        content = (
            message
            if call_errors
            else "This call was not executed because the attempt failed validation."
        )
        results.append(
            {
                "type": "tool_result",
                "tool_use_id": tool_use_id,
                "is_error": True,
                "content": content,
            }
        )
    return results


def execute_fixture(
    name: str,
    arguments: Mapping[str, Any],
    fixtures: Sequence[Mapping[str, Any]],
    used: set[int],
) -> tuple[Any, bool, int | None, bool]:
    """Return the scripted result for one call.

    Matching is a subset test over the fixture's ``match`` block (or its full
    ``args`` when no ``match`` is supplied). Extra arguments the model sends
    are ignored for matching but are still visible in the trace.
    """

    for index, fixture in enumerate(fixtures):
        if fixture.get("tool") != name:
            continue
        if "match" in fixture:
            expected = fixture.get("match") or {}
        else:
            expected = fixture.get("args") or {}
        if not isinstance(expected, Mapping):
            continue
        if all(arguments.get(key) == value for key, value in expected.items()):
            reused = index in used
            used.add(index)
            return fixture.get("result"), False, index, reused
    return (
        {
            "error": "No scripted mock result matches this call.",
            "tool": name,
            "arguments": dict(arguments),
        },
        True,
        None,
        False,
    )


class MultiStepAgent:
    """Run one multi-step task against scripted fixtures."""

    def __init__(
        self,
        model: str,
        arm: str = "",
        effort: str = "",
        client: Any = None,
        price: TokenPrice | None = None,
        cost_ceiling_usd: float | None = None,
        max_steps: int = MULTISTEP_MAX_STEPS,
        max_tokens: int = MULTISTEP_MAX_TOKENS,
        max_schema_repair_retries: int = MAX_SCHEMA_REPAIR_RETRIES,
        max_infrastructure_retries: int = MAX_INFRASTRUCTURE_RETRIES,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        self.model = model
        self.arm = arm
        self.effort = effort
        self.client = client
        self.price = price
        self.cost_ceiling_usd = cost_ceiling_usd
        self.max_steps = max_steps
        self.max_tokens = max_tokens
        self.max_schema_repair_retries = max_schema_repair_retries
        self.max_infrastructure_retries = max_infrastructure_retries
        self._sleep = sleep or time.sleep
        self.request_params = effort_request_params(effort)
        self.effort_transmitted = bool(
            self.request_params.get("output_config", {}).get("effort")
        )

    def _client(self) -> Any:
        if self.client is None:
            try:
                import anthropic
            except ImportError as exc:  # pragma: no cover - environment guard
                raise RuntimeError(
                    "the anthropic SDK is required for a live run"
                ) from exc
            self.client = anthropic.Anthropic()
        return self.client

    def _budget_reached(self, trace: MultiStepTrace, starting_cost_usd: float) -> bool:
        if self.cost_ceiling_usd is None:
            return False
        return starting_cost_usd + trace.cost_usd >= self.cost_ceiling_usd

    def _request(
        self,
        messages: list[dict[str, Any]],
        schemas: list[dict[str, Any]],
        budget_check: Callable[[], bool],
    ) -> tuple[Any, int, float]:
        started = time.perf_counter()
        retries = 0
        kwargs: dict[str, Any] = dict(self.request_params)
        kwargs.update(
            {
                "model": self.model,
                "max_tokens": self.max_tokens,
                "system": MULTISTEP_SYSTEM_PROMPT,
                "messages": messages,
                "tools": schemas,
            }
        )
        while True:
            if budget_check():
                raise CostCeilingReached("cost ceiling reached before an API request")
            try:
                response = self._client().messages.create(**kwargs)
                return response, retries, time.perf_counter() - started
            except Exception as exc:
                if _is_infrastructure_error(exc):
                    if retries < self.max_infrastructure_retries:
                        retries += 1
                        if budget_check():
                            raise CostCeilingReached(
                                "cost ceiling reached before an infrastructure retry"
                            ) from exc
                        self._sleep(min(2 ** (retries - 1), 8))
                        continue
                    raise RequestFailed(
                        str(exc),
                        retries,
                        exc,
                        kind="infrastructure_exhausted",
                    ) from exc
                raise RequestFailed(str(exc), retries, exc, kind="api_error") from exc

    def _record_usage(self, trace: MultiStepTrace, raw_usage: Any) -> float:
        usage = normalize_usage(raw_usage)
        add_usage(trace.raw_usage, usage)
        trace.input_tokens = int(trace.raw_usage.get("input_tokens", 0) or 0)
        trace.output_tokens = int(trace.raw_usage.get("output_tokens", 0) or 0)
        if self.price is None:
            return 0.0
        trace.price_configured = True
        return calculate_cost(usage, self.price)

    def run(
        self,
        task: Mapping[str, Any],
        *,
        starting_cost_usd: float = 0.0,
    ) -> MultiStepTrace:
        from agent.multistep_tools import tool_schemas

        tool_names = list(task.get("tools") or [])
        schemas = tool_schemas(tool_names)
        fixtures = list(task.get("fixtures") or [])
        trace = MultiStepTrace(
            task_id=str(task.get("id", "")),
            arm=self.arm,
            model_id=self.model,
            effort=self.effort,
            effort_transmitted=self.effort_transmitted,
            offered_tools=[schema["name"] for schema in schemas],
        )
        messages: list[dict[str, Any]] = [
            {"role": "user", "content": str(task.get("query", ""))}
        ]
        used_fixtures: set[int] = set()
        repair_attempts = 0

        def budget_check() -> bool:
            return self._budget_reached(trace, starting_cost_usd)

        while trace.n_steps < self.max_steps:
            trace.n_steps += 1
            try:
                response, infra_retries, latency = self._request(
                    messages, schemas, budget_check
                )
            except CostCeilingReached as exc:
                trace.cost_ceiling_reached = True
                trace.stopped_early = True
                trace.failure_reason = "cost_ceiling"
                trace.error = str(exc)
                break
            except RequestFailed as exc:
                trace.infrastructure_retries += exc.retries
                trace.stopped_early = True
                trace.failure_reason = exc.kind
                trace.error = str(exc)
                break

            trace.request_count += 1
            trace.infrastructure_retries += infra_retries
            trace.latency_s += latency

            blocks = _content_blocks(response)
            raw_usage = _get(response, "usage", None)
            step_cost = self._record_usage(trace, raw_usage)
            trace.cost_usd += step_cost
            stop_reason = str(_get(response, "stop_reason", "") or "")
            calls = _extract_calls(blocks)
            step: dict[str, Any] = {
                "step": trace.n_steps,
                "blocks": blocks,
                "text": _text(blocks),
                "stop_reason": stop_reason,
                "usage": normalize_usage(raw_usage),
                "cost_usd": step_cost,
                "infrastructure_retries": infra_retries,
                "tool_calls": calls,
                "tool_results": [],
                "schema_repair": False,
            }
            trace.steps.append(step)

            if stop_reason == "max_tokens":
                trace.truncated = True
                trace.failure_reason = "truncated"
                trace.final_answer = _text(blocks)
                break
            if stop_reason == "refusal":
                trace.refusal = True
                trace.failure_reason = "refusal"
                trace.final_answer = _text(blocks)
                break

            if not calls:
                trace.final_answer = _text(blocks)
                if not trace.final_answer.strip():
                    trace.format_failure = True
                    trace.failure_reason = "empty_final_answer"
                break

            errors = [
                error
                for error in validate_tool_calls(blocks, schemas)
                if error.get("kind") != "no_tool_use"
            ]
            if errors:
                for error in errors:
                    error["step"] = trace.n_steps
                trace.validation_errors.extend(errors)
                if repair_attempts < self.max_schema_repair_retries:
                    repair_attempts += 1
                    trace.schema_repair_retries += 1
                    step["schema_repair"] = True
                    messages.append({"role": "assistant", "content": blocks})
                    messages.append(
                        {
                            "role": "user",
                            "content": _repair_tool_results(blocks, errors, schemas),
                        }
                    )
                    continue
                trace.format_failure = True
                trace.failure_reason = "schema_validation_after_retries"
                trace.final_answer = _text(blocks)
                break

            results_for_message: list[dict[str, Any]] = []
            for call in calls:
                name = str(call.get("name") or "")
                arguments = call.get("input")
                if not isinstance(arguments, Mapping):
                    arguments = {}
                result, is_error, fixture_index, reused = execute_fixture(
                    name, arguments, fixtures, used_fixtures
                )
                record = {
                    "step": trace.n_steps,
                    "tool_use_id": call.get("id"),
                    "name": name,
                    "arguments": dict(arguments),
                    "result": result,
                    "is_error": is_error,
                    "fixture_index": fixture_index,
                    "fixture_reused": reused,
                }
                trace.tool_results.append(record)
                step["tool_results"].append(record)
                trace.tool_calls.append(
                    {
                        "type": "tool_use",
                        "id": call.get("id"),
                        "name": name,
                        "input": arguments,
                    }
                )
                if is_error:
                    trace.fixture_misses.append(
                        {
                            "step": trace.n_steps,
                            "tool": name,
                            "arguments": dict(arguments),
                        }
                    )
                results_for_message.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": call.get("id"),
                        "is_error": is_error,
                        "content": _result_content(result),
                    }
                )

            messages.append({"role": "assistant", "content": blocks})
            messages.append({"role": "user", "content": results_for_message})

            if budget_check():
                trace.cost_ceiling_reached = True
                trace.stopped_early = True
                trace.failure_reason = "cost_ceiling"
                break
        else:
            trace.max_steps_reached = True
            trace.failure_reason = "max_steps"
            trace.final_answer = _text(
                trace.steps[-1]["blocks"] if trace.steps else []
            )

        trace.messages = messages
        trace.n_tool_calls = len(trace.tool_calls)
        trace.completed = bool(
            trace.final_answer.strip()
            and not trace.truncated
            and not trace.refusal
            and not trace.format_failure
            and not trace.max_steps_reached
            and not trace.cost_ceiling_reached
            and not trace.stopped_early
        )
        return trace


__all__ = [
    "MULTISTEP_SYSTEM_PROMPT",
    "MultiStepAgent",
    "MultiStepTrace",
    "execute_fixture",
]
