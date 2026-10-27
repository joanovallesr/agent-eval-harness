"""Tests for the EVAL_DESIGN §7 m/u usable-cases sensitivity."""

from __future__ import annotations

import pytest

from audit_variance_m import pessimistic_u


def test_pessimistic_u_matches_the_section_7_formula() -> None:
    # Worked value from the pinned §7 variance formula at m = 59.
    assert pessimistic_u(59) == pytest.approx(1.0902, abs=1e-4)
    assert pessimistic_u(13) > pessimistic_u(59) > pessimistic_u(2508) > 1.0
    with pytest.raises(ValueError):
        pessimistic_u(1)
