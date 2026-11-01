"""Harness smoke tests — run without API keys or data.

These must pass in CI on every push. Anything needing a key or the dataset
goes behind a marker and runs manually.
"""

from __future__ import annotations

import pytest


def test_tool_schemas_shape():
    from agent.tools import tool_schemas

    for schema in tool_schemas():
        assert set(schema) == {"name", "description", "input_schema"}
        assert schema["input_schema"]["type"] == "object"


def test_kappa_threshold_constant():
    from eval.calibration import KAPPA_SHIP_THRESHOLD

    assert KAPPA_SHIP_THRESHOLD == 0.70


@pytest.mark.skip(reason="Phase 2: implement metrics first")
def test_accuracy_ci_bounds():
    pass
