"""Regression tests for CI dependency-source drift.

The eval-gate job must install the same dependency sources as the test job.
A separate hand-maintained subset is what let the first live PR run fail with
``ModuleNotFoundError: No module named 'scipy'``.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
INSTALL_PATTERN = re.compile(
    r"^\s*-\s+name:\s*Install[^\n]*\n\s+run:\s*(\S[^\n]*)$",
    re.MULTILINE,
)


def _workflow_path() -> Path:
    """Allow a demonstration run against an arbitrary workflow file."""

    override = os.environ.get("CI_WORKFLOW_PATH")
    return Path(override) if override else WORKFLOW


def _job_block(workflow: str, job: str) -> str:
    lines = workflow.splitlines()
    marker = f"  {job}:"
    try:
        start = lines.index(marker)
    except ValueError as exc:
        raise AssertionError(f"workflow has no job {job!r}") from exc

    end = len(lines)
    for index in range(start + 1, len(lines)):
        line = lines[index]
        if line.startswith("  ") and not line.startswith("    ") and line.strip():
            end = index
            break
    return "\n".join(lines[start:end])


def _install_commands(workflow: str, job: str) -> list[str]:
    block = _job_block(workflow, job)
    return [
        match.group(1).strip() for match in INSTALL_PATTERN.finditer(block)
    ]


def test_eval_gate_installs_the_same_dependency_sources_as_test():
    workflow = _workflow_path().read_text()
    test_commands = _install_commands(workflow, "test")
    gate_commands = _install_commands(workflow, "eval-gate")

    assert test_commands, "the test job must declare at least one install step"
    assert gate_commands == test_commands, (
        "the eval-gate job must install the same dependency sources as the "
        f"test job; test={test_commands}, eval-gate={gate_commands}"
    )
