"""Tool-using agent built on the raw Anthropic API.

The loop is deliberately framework-free: build the message list, call the API,
validate the tool calls against their JSON schemas, feed validation errors
back for a retry (capped — see docs/EVAL_DESIGN.md section 3), repeat until
the model answers or a budget trips. The loop never consults ground truth or
the scorer to decide whether to retry.

TODO(phase-2): implement run() — system prompt, tool schemas from tools.py,
max-iteration cap, per-task token/cost tracking (see metrics.CostTracker).
TODO(phase-2): retry policy for tool-call errors (transient vs fatal).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class AgentTrace:
    """Everything needed to score one task after the fact."""

    task_id: str
    messages: list[dict[str, Any]] = field(default_factory=list)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    final_answer: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
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
    """

    def __init__(
        self,
        model: str,
        tools: list,
        max_iterations: int = 8,
        system_prompt: str = "",
    ) -> None:
        self.model = model
        self.tools = tools
        self.max_iterations = max_iterations
        self.system_prompt = system_prompt
        # TODO: anthropic client from ANTHROPIC_API_KEY

    def run(self, task: dict[str, Any]) -> AgentTrace:
        """Run one benchmark case to completion. Returns the full trace."""
        raise NotImplementedError("Phase 1: implement the tool-use loop")
