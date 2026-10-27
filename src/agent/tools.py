"""Tool definitions for the agent: JSON schemas + real implementations.

Each tool is a dict with "name", "description", "input_schema" (Anthropic
tool-use format), and "handler" — the Python function that actually runs it.

TODO(phase-1): start with 3-5 tools that mirror the xLAM-60k dev categories
(e.g. calculator, search stub, date/time, a mock API). Real tools keep the
benchmark honest; stubbed tools make the agent untestable.
TODO(phase-1): every handler returns {"ok": bool, "result": ..., "error": ...}
so the agent loop has a uniform contract.
"""

from __future__ import annotations

from typing import Any


def _placeholder_handler(**kwargs: Any) -> dict[str, Any]:
    raise NotImplementedError("Phase 1: implement real tool handlers")


TOOLS: list[dict[str, Any]] = [
    # TODO(phase-1): fill in. Example shape:
    # {
    #     "name": "get_weather",
    #     "description": "Current weather for a city.",
    #     "input_schema": {
    #         "type": "object",
    #         "properties": {"city": {"type": "string"}},
    #         "required": ["city"],
    #     },
    #     "handler": _placeholder_handler,
    # },
]


def tool_schemas() -> list[dict[str, Any]]:
    """Anthropic API tool definitions (schemas only, no handlers)."""
    return [
        {k: t[k] for k in ("name", "description", "input_schema")} for t in TOOLS
    ]
