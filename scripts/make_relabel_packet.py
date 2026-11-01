"""Build the separate blind relabel batch for the Phase-5 report half."""

from __future__ import annotations

import argparse
import json

from eval.labeling import build_relabel_packet


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a blind relabel batch")
    parser.add_argument("--packet-dir", required=True)
    parser.add_argument("--mapping", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--mapping-out", required=True)
    parser.add_argument("--rubric", required=True)
    parser.add_argument("--n-traces", type=int, default=15)
    parser.add_argument("--seed", type=int, default=20261032)
    args = parser.parse_args()
    metadata = build_relabel_packet(
        args.packet_dir,
        args.mapping,
        args.out,
        args.mapping_out,
        args.rubric,
        n_traces=args.n_traces,
        seed=args.seed,
    )
    print(json.dumps(metadata, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
