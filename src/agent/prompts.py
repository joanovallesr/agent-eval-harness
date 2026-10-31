"""Frozen Phase-2 prompts.

``PROMPT_VERSION`` lives in ``eval.config``. Changes to either prompt text
require bumping that version, because the CI gate later watches it.
"""

from __future__ import annotations

SYSTEM_PROMPT = """\
You are a tool-calling assistant. The user's request is answered by emitting
one or more Anthropic tool_use blocks.

Rules:
- Use only the tools offered in this request.
- Emit every call the request requires. If two independent calls are needed,
  emit both in the same response.
- Omit an optional argument when the user did not ask for it. Pass null when
  the user explicitly asked for no value and the schema permits null.
- Do not answer in prose. Do not describe tool results. Tools are not executed
  in this environment.
- Argument values must match the offered tool's JSON schema exactly.
"""


def repair_prompt(errors_text: str, schemas_text: str) -> str:
    """Feedback for one schema-repair retry.

    Only the validation errors and the relevant tool schemas are supplied.
    There is no gold label, scorer result, or fabricated tool output here.
    """

    return (
        "Your previous response failed tool-call schema validation.\n\n"
        f"Validation errors:\n{errors_text}\n\n"
        f"Relevant tool schemas:\n{schemas_text}\n\n"
        "Return only corrected tool_use blocks. Do not answer in prose."
    )
