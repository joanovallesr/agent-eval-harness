"""Command-line entry point for the Phase-7 advisory eval gate.

Run the live gate from the repository root::

    PYTHONPATH=src:scripts python scripts/eval_gate.py

Render the same comment from a saved results file without an API call::

    PYTHONPATH=src:scripts python scripts/eval_gate.py --dry-run \
        --results results/<run-id>/results.json
"""

from __future__ import annotations

from eval.gate import main


if __name__ == "__main__":
    raise SystemExit(main())
