"""Build a blinded pairwise-comparison JSONL from two generation files.

Each row shows the judge only ``instruction``, ``response_a`` and
``response_b``. Which model sits in position A is randomised per row (seeded)
and stored under ``hidden`` so it can be mapped back after judging.

Example:
  python scripts/build_pairs.py --model_path outputs/generations/alpaca/dpo_uf.json \
      --baseline_path outputs/generations/alpaca/sft.json \
      --output_path outputs/eval/uf_dpo_vs_sft_pairs.jsonl --eval_kind general
"""

from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from alignment.utils import read_json, write_jsonl  # noqa: E402


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--model_path", required=True, help="generations of the candidate (e.g. DPO)")
    p.add_argument("--baseline_path", required=True, help="generations of the baseline (e.g. SFT)")
    p.add_argument("--output_path", required=True)
    p.add_argument("--eval_kind", default="general", choices=["general", "safety", "persona"])
    p.add_argument("--seed", type=int, default=42)
    args = p.parse_args()

    model_rows = {r["id"]: r for r in read_json(args.model_path)}
    base_rows = read_json(args.baseline_path)
    rng = random.Random(args.seed)
    pairs = []
    for b in base_rows:
        m = model_rows.get(b["id"])
        if m is None:
            raise SystemExit(f"id {b['id']} missing from {args.model_path}")
        assert m["instruction"] == b["instruction"], f"instruction mismatch for {b['id']}"
        model_is_a = rng.random() < 0.5
        a, bb = (m, b) if model_is_a else (b, m)
        pairs.append({
            "pair_id": b["id"],
            "instruction": b["instruction"],
            "response_a": a["output"],
            "response_b": bb["output"],
            "eval_kind": args.eval_kind,
            "category": b.get("category"),
            "hidden": {"model_a": a["generator"], "model_b": bb["generator"],
                       "candidate": m["generator"], "baseline": b["generator"],
                       "candidate_position": "A" if model_is_a else "B"},
        })
    write_jsonl(args.output_path, pairs)
    n_a = sum(p["hidden"]["candidate_position"] == "A" for p in pairs)
    print(f"wrote {len(pairs)} pairs -> {args.output_path} (candidate in A for {n_a})")


if __name__ == "__main__":
    main()
