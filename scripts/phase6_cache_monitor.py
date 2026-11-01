"""Operational cache guard for the held-out run.

The pricing basis in ``config/pricing.list_sync.2026-10-31.json`` has no
universal cache-write rate, so any nonzero cache token in any response is a
re-pin signal. This monitor polls the run's checkpoints and terminates the
harness process if it ever sees one, before more spend can accrue.

It is an operational sidecar, not a scoring or analysis component. The report
generator repeats the check over the completed results JSON.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import signal
import time
from pathlib import Path

from phase6_report import has_nonzero_cache


def process_is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--pid", required=True, type=int)
    parser.add_argument("--marker", required=True)
    parser.add_argument("--interval", type=float, default=5.0)
    args = parser.parse_args()

    marker = Path(args.marker)
    while process_is_alive(args.pid):
        for path in glob.glob(
            os.path.join(args.run_dir, "*", "checkpoints", "*.json")
        ):
            try:
                payload = json.loads(Path(path).read_text())
            except (OSError, json.JSONDecodeError):
                continue
            usage = (payload.get("trace") or {}).get("raw_usage") or {}
            if has_nonzero_cache(usage):
                marker.write_text(f"{path}\n")
                os.kill(args.pid, signal.SIGTERM)
                return
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
