"""Tool-using agent built on the raw Anthropic API.

The loop is deliberately framework-free: build the message list, call the API,
validate the tool calls against their JSON schemas, feed validation errors
back for a retry (capped — see docs/EVAL_DESIGN.md §3), repeat until the model
answers or a budget trips. The loop never consults ground truth or the scorer
to decide whether to retry.

TODO(phase-2): implement run() — system prompt, tool schemas from tools.py,
max-iteration cap, per-task token/cost tracking (see metrics.CostTracker),
and the AgentTrace fields below.
TODO(phase-2): retry policy for tool-call errors: infrastructure retries
(API 5xx/timeout, every arm) are distinct from schema-repair retries
(agent loop only). Record both without conflating them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class AgentTrace:
    """Everything needed to score one task after the fact.

    One trace per case per repeat. ``tool_calls`` is every call emitted across
    attempts; ``final_attempt_calls`` is only the last assistant message's
    calls, which is what MATCH_SPEC scores. Retry and validation diagnostics
    are recorded even when the final attempt is clean, so the harness can
    report retry and format-failure rates alongside the score.
    """

    task_id: str
    arm: str = ""
    model_id: str = ""
    effort: str = ""
    prompt_version: str = ""
    messages: list[dict[str, Any]] = field(default_factory=list)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    final_attempt_calls: list[dict[str, Any]] = field(default_factory=list)
    final_answer: str = ""
    retry_count: int = 0
    validation_errors: list[dict[str, Any]] = field(default_factory=list)
    schema_validation_failed: bool = False
    format_failure: bool = False
    input_tokens: int = 0
    output_tokens: int = 0
    # Logged cost from actual API usage; never an estimate.
    cost_usd: float = 0.0
    latency_s: float = 0.0
    stopped_early: bool = False


class Agent:
    """Raw-API tool-using agent.

    Parameters
    ----------
    model : str
        Anthropic model id for the agent loop (e.g. "claude-...").
    tools : list
        Tool schemas + implementations, see tools.py.
    max_iterations : int
        Hard cap on tool-use rounds before we stop and label max-iterations.
    system_prompt : str
        Frozen prompt — changes to it are exactly what the CI gate watches.
    arm : str
        Arm name recorded in the trace (``haiku-agent`` here).
    effort : str
        Pinned effort value recorded in the trace.
    prompt_version : str
        Frozen prompt version recorded in the trace.
    """

    def __init__(
        self,
        model: str,
        tools: list,
        max_iterations: int = 8,
        system_prompt: str = "",
        arm: str = "",
        effort: str = "",
        prompt_version: str = "",
    ) -> None:
        self.model = model
        self.tools = tools
        self.max_iterations = max_iterations
        self.system_prompt = system_prompt
        self.arm = arm
        self.effort = effort
        self.prompt_version = prompt_version
        # TODO: anthropic client from ANTHROPIC_API_KEY

    def run(self, task: dict[str, Any]) -> AgentTrace:
        """Run one benchmark case to completion. Returns the full trace."""
        raise NotImplementedError("Phase 2: implement the tool-use loop")
