"""Tool-using agent built on the raw Anthropic API.

The loop is deliberately framework-free. It calls the API, reads the
``tool_use`` blocks, validates them against the offered JSON schemas, and on
the agent arm feeds only the validation error and relevant schema back for a
capped repair retry. It never executes a tool, invents a tool result, consults
gold, or calls the scorer.

Two retry kinds stay distinct (EVAL_DESIGN §4):

- infrastructure retries (timeout / 5xx / connection), capped identically on
  every arm;
- schema-repair retries, capped on ``haiku-agent`` only. Direct arms make one
  model request per case/repeat and never repair a schema error.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Mapping, Sequence

from jsonschema import Draft202012Validator

from agent.prompts import SYSTEM_PROMPT, repair_prompt
from eval.config import (
    EFFORT_PROVISIONAL,
    EFFORT_TRANSMITTED,
    MAX_INFRASTRUCTURE_RETRIES,
    MAX_SCHEMA_REPAIR_RETRIES,
    MAX_TOKENS,
    MATCH_SPEC_VERSION,
    PROMPT_VERSION,
    effort_for,
    effort_is_transmitted,
    effort_request_params,
    model_id_for,
)
from eval.metrics import (
    PricingError,
    TokenPrice,
    add_usage,
    calculate_cost,
    normalize_usage,
)


@dataclass
class AgentTrace:
    """Everything needed to score one task after the fact.

    One trace per case per repeat. ``tool_calls`` is every call emitted across
    attempts; ``final_attempt_calls`` is only the last assistant message's
    calls, which is what MATCH_SPEC scores. ``final_attempt_blocks`` preserves
    the raw final message so the scorer can see malformed blocks too.
    """

    task_id: str
    arm: str = ""
    model_id: str = ""
    effort: str = ""
    effort_provisional: bool = EFFORT_PROVISIONAL
    effort_transmitted: bool = EFFORT_TRANSMITTED
    prompt_version: str = ""
    match_spec_version: str = MATCH_SPEC_VERSION
    offered_tools: list[str] = field(default_factory=list)
    messages: list[dict[str, Any]] = field(default_factory=list)
    attempts: list[dict[str, Any]] = field(default_factory=list)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    final_attempt_blocks: list[Any] = field(default_factory=list)
    final_attempt_calls: list[dict[str, Any]] = field(default_factory=list)
    final_answer: str = ""
    retry_count: int = 0
    infrastructure_retry_count: int = 0
    request_count: int = 0
    validation_errors: list[dict[str, Any]] = field(default_factory=list)
    schema_validation_failed: bool = False
    final_schema_valid: bool | None = None
    format_failure: bool = False
    failure_reason: str = ""
    error: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    raw_usage: dict[str, Any] = field(default_factory=dict)
    # Logged cost from actual API usage; never an estimate.
    cost_usd: float = 0.0
    price_configured: bool = False
    latency_s: float = 0.0
    stopped_early: bool = False
    cost_ceiling_reached: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class RequestFailed(RuntimeError):
    """An API request failed after the applicable retry policy."""

    def __init__(
        self,
        message: str,
        retries: int,
        cause: BaseException,
        *,
        kind: str,
    ) -> None:
        super().__init__(message)
        self.retries = retries
        self.cause = cause
        self.kind = kind


class CostCeilingReached(RuntimeError):
    """Accumulated actual cost reached the per-run ceiling mid-case."""


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _plain(value: Any) -> Any:
    """Convert SDK objects to JSON-safe plain data without dropping fields."""

    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if hasattr(value, "model_dump"):
        return _plain(value.model_dump())
    if hasattr(value, "dict"):
        return _plain(value.dict())
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    try:
        json.dumps(value)
    except (TypeError, ValueError):
        return repr(value)
    return value


def _content_blocks(response: Any) -> list[Any]:
    content = _get(response, "content", [])
    if content is None:
        return []
    if isinstance(content, list):
        return [_plain(block) for block in content]
    return [_plain(content)]


def _extract_calls(blocks: Sequence[Any]) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    for block in blocks:
        if not isinstance(block, dict) or block.get("type") != "tool_use":
            continue
        calls.append(
            {
                "type": "tool_use",
                "id": block.get("id"),
                "name": block.get("name"),
                "input": block.get("input"),
            }
        )
    return calls


def _text(blocks: Sequence[Any]) -> str:
    parts = [
        block.get("text", "")
        for block in blocks
        if isinstance(block, dict) and block.get("type") == "text"
    ]
    return "".join(part for part in parts if isinstance(part, str))


def _is_infrastructure_error(exc: BaseException) -> bool:
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True
    status = getattr(exc, "status_code", None)
    if isinstance(status, int) and (status == 408 or status == 429 or status >= 500):
        return True
    name = type(exc).__name__.lower()
    markers = (
        "timeout",
        "connection",
        "overloaded",
        "internalserver",
        "ratelimit",
        "serviceunavailable",
    )
    return any(marker in name for marker in markers)


def validate_tool_calls(
    blocks: Sequence[Any],
    offered_schemas: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Validate one attempt against the offered tool schemas.

    Returns a list of error records. An empty list means the attempt is
    schema-valid. This function never reads gold or the scorer.
    """

    offered = {str(schema["name"]): schema for schema in offered_schemas}
    errors: list[dict[str, Any]] = []
    saw_tool_use = False

    for index, block in enumerate(blocks):
        if not isinstance(block, dict):
            errors.append(
                {
                    "call_index": index,
                    "kind": "malformed_block",
                    "message": "content block is not an object",
                }
            )
            continue
        if block.get("type") != "tool_use":
            continue

        saw_tool_use = True
        name = block.get("name")
        arguments = block.get("input")
        if not isinstance(name, str) or name == "":
            errors.append(
                {
                    "call_index": index,
                    "kind": "malformed_block",
                    "message": "tool_use block has a missing, non-string, or empty name",
                }
            )
            continue
        if name not in offered:
            errors.append(
                {
                    "call_index": index,
                    "tool": name,
                    "kind": "unknown_tool",
                    "message": f"tool {name!r} was not offered in this case",
                }
            )
            continue
        if not isinstance(arguments, dict):
            errors.append(
                {
                    "call_index": index,
                    "tool": name,
                    "kind": "malformed_block",
                    "message": "tool_use input is not a JSON object",
                }
            )
            continue

        schema = offered[name]["input_schema"]
        validator = Draft202012Validator(schema)
        schema_errors = sorted(
            validator.iter_errors(arguments),
            key=lambda error: list(error.path),
        )
        for schema_error in schema_errors:
            errors.append(
                {
                    "call_index": index,
                    "tool": name,
                    "kind": "schema",
                    "message": schema_error.message,
                    "path": list(schema_error.path),
                    "schema_path": list(schema_error.schema_path),
                }
            )

    if not saw_tool_use:
        errors.append(
            {
                "call_index": None,
                "kind": "no_tool_use",
                "message": "the response contained no tool_use block",
            }
        )
    return errors


class Agent:
    """Raw-API tool-using agent.

    Parameters
    ----------
    model:
        Exact Anthropic model id recorded in every trace.
    tools:
        Full tool definitions (``name``, ``description``, ``input_schema``).
    arm:
        Arm name. ``haiku-agent`` enables schema-repair retries; direct arms
        do not.
    client:
        Injectable Anthropic client. Offline tests pass a fake so CI never
        makes a live call.
    price:
        Pinned list price for this model. ``None`` means cost stays 0 and the
        harness must refuse a live run.
    cost_ceiling_usd:
        Per-run ceiling. The accumulated actual cost is checked after every
        model response and before any additional schema-repair or
        infrastructure attempt. Infrastructure retries are per model request
        (initial + 2 = 3 HTTP requests); schema-repair retries are per case
        (initial + 2 = 3 model requests). Maximum HTTP requests per case:
        9 for ``haiku-agent``, 3 for a direct arm.
    """

    def __init__(
        self,
        model: str,
        tools: list[dict[str, Any]],
        arm: str = "",
        max_iterations: int = 8,
        system_prompt: str = SYSTEM_PROMPT,
        effort: str = "",
        effort_provisional: bool = EFFORT_PROVISIONAL,
        prompt_version: str = PROMPT_VERSION,
        client: Any = None,
        price: TokenPrice | None = None,
        cost_ceiling_usd: float | None = None,
        max_tokens: int = MAX_TOKENS,
        max_schema_repair_retries: int = MAX_SCHEMA_REPAIR_RETRIES,
        max_infrastructure_retries: int = MAX_INFRASTRUCTURE_RETRIES,
        sleep: Callable[[float], None] | None = None,
        request_params: Mapping[str, Any] | None = None,
    ) -> None:
        self.model = model
        self.tools = tools
        self.arm = arm
        # Retained for API compatibility; Phase 2 does not execute tools, so
        # this is not the schema-repair cap.
        self.max_iterations = max_iterations
        self.system_prompt = system_prompt
        self.effort = effort or (effort_for(arm) if arm else "")
        self.effort_provisional = effort_provisional
        self.prompt_version = prompt_version
        self.client = client
        self.price = price
        self.cost_ceiling_usd = cost_ceiling_usd
        self.max_tokens = max_tokens
        self.max_schema_repair_retries = max_schema_repair_retries
        self.max_infrastructure_retries = max_infrastructure_retries
        self._sleep = sleep or time.sleep
        # One place for an owner-pinned API parameter. A concrete effort is
        # transmitted only through the SDK's ``output_config.effort`` field.
        if request_params is None:
            self.request_params = effort_request_params(self.effort)
        else:
            self.request_params = dict(request_params)
        self.effort_transmitted = effort_is_transmitted(self.effort) and bool(
            self.request_params.get("output_config", {}).get("effort")
        )
        self._tools_by_name = {str(tool["name"]): tool for tool in tools}

    @property
    def schema_repair_enabled(self) -> bool:
        return self.arm == "haiku-agent"

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

    def _offered_schemas(self, task: Mapping[str, Any]) -> list[dict[str, Any]]:
        names = task.get("tools") or list(self._tools_by_name)
        if not isinstance(names, (list, tuple)) or not names:
            raise ValueError("task['tools'] must be a non-empty list of tool names")

        # xLAM cases carry their own tool definitions; the mini-set uses the
        # global in-repo schemas.
        local_definitions = task.get("tool_definitions")
        local_by_name: dict[str, Mapping[str, Any]] = {}
        if local_definitions is not None:
            if not isinstance(local_definitions, (list, tuple)):
                raise ValueError("task['tool_definitions'] must be a list")
            for tool in local_definitions:
                if not isinstance(tool, Mapping) or not isinstance(
                    tool.get("name"), str
                ):
                    raise ValueError("tool definition must carry a string name")
                local_by_name[str(tool["name"])] = tool

        schemas: list[dict[str, Any]] = []
        for name in names:
            tool = local_by_name.get(name) or self._tools_by_name.get(name)
            if tool is None:
                raise ValueError(f"task offers unknown tool {name!r}")
            schemas.append(
                {
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "input_schema": tool["input_schema"],
                }
            )
        return schemas

    def _budget_reached(self, trace: AgentTrace, starting_cost_usd: float) -> bool:
        if self.cost_ceiling_usd is None:
            return False
        return starting_cost_usd + trace.cost_usd >= self.cost_ceiling_usd

    def _request(
        self,
        messages: list[dict[str, Any]],
        schemas: list[dict[str, Any]],
        budget_check: Callable[[], bool] | None = None,
    ) -> tuple[Any, int, float]:
        """Make one request with the shared infrastructure-retry policy.

        ``budget_check`` runs before the request and before each
        infrastructure retry, using accumulated actual cost only.
        """

        started = time.perf_counter()
        retries = 0
        kwargs: dict[str, Any] = dict(self.request_params)
        kwargs.update(
            {
                "model": self.model,
                "max_tokens": self.max_tokens,
                "system": self.system_prompt,
                "messages": messages,
                "tools": schemas,
            }
        )
        while True:
            if budget_check is not None and budget_check():
                raise CostCeilingReached("cost ceiling reached before an API request")
            try:
                response = self._client().messages.create(**kwargs)
                return response, retries, time.perf_counter() - started
            except Exception as exc:
                if _is_infrastructure_error(exc):
                    if retries < self.max_infrastructure_retries:
                        retries += 1
                        if budget_check is not None and budget_check():
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
                # Auth/configuration/400-class errors are failures, not
                # infrastructure events: they get no infrastructure retry.
                raise RequestFailed(str(exc), retries, exc, kind="api_error") from exc

    def _record_usage(self, trace: AgentTrace, raw_usage: Any) -> float:
        usage = normalize_usage(raw_usage)
        add_usage(trace.raw_usage, usage)
        trace.input_tokens = int(trace.raw_usage.get("input_tokens", 0) or 0)
        trace.output_tokens = int(trace.raw_usage.get("output_tokens", 0) or 0)
        if self.price is None:
            return 0.0
        trace.price_configured = True
        return calculate_cost(usage, self.price)

    def _repair_message(
        self,
        errors: list[dict[str, Any]],
        schemas: list[dict[str, Any]],
    ) -> str:
        names = {error.get("tool") for error in errors if error.get("tool")}
        relevant = [schema for schema in schemas if schema["name"] in names] or schemas
        return repair_prompt(
            json.dumps(errors, indent=2, sort_keys=True),
            json.dumps(relevant, indent=2, sort_keys=True),
        )

    def _repair_content(
        self,
        errors: list[dict[str, Any]],
        schemas: list[dict[str, Any]],
        blocks: list[Any],
    ) -> str | list[dict[str, Any]]:
        """Build the repair turn.

        Anthropic requires one ``tool_result`` block for every assistant
        ``tool_use`` before the next user turn. Those results contain **only
        the validation error and relevant schema** with ``is_error: true``.
        They are not tool outputs: no tool was executed and no result is
        invented. A text-only attempt has no tool_use id, so it gets a plain
        text repair message instead.
        """

        tool_results: list[dict[str, Any]] = []
        for index, block in enumerate(blocks):
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            tool_use_id = block.get("id")
            if not isinstance(tool_use_id, str) or not tool_use_id:
                continue
            call_errors = [
                error for error in errors if error.get("call_index") == index
            ]
            if call_errors:
                content = self._repair_message(call_errors, schemas)
            else:
                content = (
                    "Call rejected: this attempt failed schema validation "
                    "elsewhere. This call was not executed and no tool output "
                    "is available."
                )
            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,
                    "is_error": True,
                    "content": content,
                }
            )
        if tool_results:
            return tool_results
        return self._repair_message(errors, schemas)

    def _set_final(self, trace: AgentTrace, blocks: list[Any]) -> None:
        trace.final_attempt_blocks = list(blocks)
        trace.final_attempt_calls = _extract_calls(blocks)
        trace.final_answer = _text(blocks)
        if not trace.final_attempt_calls:
            trace.format_failure = True

    def run(self, task: dict[str, Any], *, starting_cost_usd: float = 0.0) -> AgentTrace:
        """Run one benchmark case and return the full trace."""

        schemas = self._offered_schemas(task)
        trace = AgentTrace(
            task_id=str(task.get("id", "")),
            arm=self.arm,
            model_id=self.model,
            effort=self.effort,
            effort_provisional=self.effort_provisional,
            effort_transmitted=self.effort_transmitted,
            prompt_version=self.prompt_version,
            offered_tools=[schema["name"] for schema in schemas],
        )
        messages: list[dict[str, Any]] = [
            {"role": "user", "content": str(task.get("query", ""))}
        ]
        total_attempts = 1 + (
            self.max_schema_repair_retries if self.schema_repair_enabled else 0
        )
        attempt_index = 0

        def budget_check() -> bool:
            return self._budget_reached(trace, starting_cost_usd)

        while attempt_index < total_attempts:
            attempt_index += 1
            try:
                response, infra_retries, latency = self._request(
                    messages,
                    schemas,
                    budget_check,
                )
            except CostCeilingReached as exc:
                trace.cost_ceiling_reached = True
                trace.stopped_early = True
                trace.failure_reason = "cost_ceiling"
                trace.format_failure = True
                trace.error = str(exc)
                self._set_final(trace, [])
                break
            except RequestFailed as exc:
                trace.infrastructure_retry_count += exc.retries
                trace.stopped_early = True
                trace.failure_reason = exc.kind
                trace.format_failure = True
                trace.error = str(exc)
                self._set_final(trace, [])
                break

            trace.request_count += 1
            trace.infrastructure_retry_count += infra_retries
            trace.latency_s += latency

            blocks = _content_blocks(response)
            raw_usage = _get(response, "usage", None)
            attempt_cost = self._record_usage(trace, raw_usage)
            trace.cost_usd += attempt_cost
            stop_reason = str(_get(response, "stop_reason", "") or "")

            errors: list[dict[str, Any]] = []
            if stop_reason not in {"refusal", "max_tokens"}:
                # Schema-aware validation is the repair signal for the agent
                # arm and a diagnostic only for direct arms.
                errors = validate_tool_calls(blocks, schemas)
                for error in errors:
                    error["attempt"] = attempt_index

            trace.validation_errors.extend(errors)
            trace.attempts.append(
                {
                    "attempt": attempt_index,
                    "blocks": blocks,
                    "text": _text(blocks),
                    "stop_reason": stop_reason,
                    "usage": normalize_usage(raw_usage),
                    "cost_usd": attempt_cost,
                    "infrastructure_retries": infra_retries,
                    "latency_s": latency,
                    "validation_errors": errors,
                    "schema_valid": not errors,
                }
            )
            trace.tool_calls.extend(_extract_calls(blocks))

            # Check accumulated actual cost after every model response,
            # before any schema-repair or infrastructure attempt.
            if budget_check():
                trace.cost_ceiling_reached = True
                trace.stopped_early = True
                trace.failure_reason = "cost_ceiling"
                trace.format_failure = True
                if stop_reason not in {"refusal", "max_tokens"}:
                    trace.final_schema_valid = not errors
                self._set_final(trace, blocks)
                break

            if stop_reason in {"refusal", "max_tokens"}:
                trace.stopped_early = True
                trace.failure_reason = (
                    "refusal" if stop_reason == "refusal" else "truncated"
                )
                trace.format_failure = True
                # Preserve the actual final message for the trace; the
                # harness enforces the failure policy (score 0) separately.
                self._set_final(trace, blocks)
                break

            if not self.schema_repair_enabled:
                trace.final_schema_valid = not errors
                self._set_final(trace, blocks)
                if not trace.final_attempt_calls:
                    trace.failure_reason = "text_only"
                break

            if not errors:
                trace.final_schema_valid = True
                self._set_final(trace, blocks)
                break

            if attempt_index >= total_attempts:
                trace.schema_validation_failed = True
                trace.final_schema_valid = False
                trace.format_failure = True
                trace.failure_reason = "schema_validation_after_retries"
                self._set_final(trace, blocks)
                break

            trace.retry_count += 1
            messages.append({"role": "assistant", "content": blocks})
            messages.append(
                {
                    "role": "user",
                    "content": self._repair_content(errors, schemas, blocks),
                }
            )

        trace.messages = messages
        return trace


def build_agent(
    arm: str,
    client: Any = None,
    price: TokenPrice | None = None,
    **kwargs: Any,
) -> Agent:
    """Build the configured agent for one arm."""

    from agent.tools import tool_schemas

    effort = kwargs.pop("effort", effort_for(arm))
    return Agent(
        model=model_id_for(arm),
        tools=tool_schemas(),
        arm=arm,
        effort=effort,
        client=client,
        price=price,
        **kwargs,
    )


__all__ = [
    "Agent",
    "AgentTrace",
    "CostCeilingReached",
    "PricingError",
    "RequestFailed",
    "build_agent",
    "validate_tool_calls",
]
