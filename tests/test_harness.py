"""Harness smoke tests — run without API keys or data.

These must pass in CI on every push. Anything needing a key or the dataset
goes behind a marker and runs manually.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest


def test_tool_schemas_shape():
    from agent.tools import tool_schemas

    for schema in tool_schemas():
        assert set(schema) == {"name", "description", "input_schema"}
        assert schema["input_schema"]["type"] == "object"


def test_kappa_threshold_constant():
    from eval.calibration import KAPPA_SHIP_THRESHOLD

    assert KAPPA_SHIP_THRESHOLD == 0.70


@pytest.mark.skip(reason="Phase 3: implement cohens_kappa first")
def test_cohens_kappa_perfect_agreement():
    from eval.calibration import cohens_kappa

    labels = ["correct", "wrong", "partial"] * 10
    assert cohens_kappa(labels, labels) == pytest.approx(1.0)


@pytest.mark.skip(reason="Phase 2: implement metrics first")
def test_accuracy_ci_bounds():
    pass
