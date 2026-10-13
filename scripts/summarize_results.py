"""Collect *_summary.json judge summaries into a Markdown table for the report.

  python scripts/summarize_results.py --eval_dir outputs/eval --out report/results_table.md
"""

from __future__ import annotations

import argparse
import glob
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from alignment.utils import read_json  # noqa: E402


def pct(x):
    return "-" if x is None else f"{100 * x:.1f}%"


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--eval_dir", default=str(ROOT / "outputs/eval"))
    p.add_argument("--out", default=str(ROOT / "report/results_table.md"))
    args = p.parse_args()

    lines = ["| Comparison | Judge | N | Wins | Ties | Losses | Unknown | Failed | Win rate (excl. ties) | Win rate (ties = 1/2) | A-position rate |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    cat_lines = []
    for path in sorted(glob.glob(os.path.join(args.eval_dir, "*_summary.json"))):
        s = read_json(path)
        name = f"{s['candidate']} vs {s['baseline']}"
        lines.append(f"| {name} | {s['judge_model']} | {s['n_pairs']} | {s['wins']} | {s['ties']} | {s['losses']} | "
                     f"{s['unknown']} | {s['failed']} | {pct(s['win_rate_excluding_ties'])} | "
                     f"{pct(s['win_rate_ties_half'])} | {pct(s['position_A_rate_among_decided'])} |")
        if len(s.get("by_category", {})) > 1:
            cat_lines.append(f"\n**{name}** by category (win/tie/loss):\n")
            cat_lines.append("| Category | Wins | Ties | Losses | Unknown/Failed |")
            cat_lines.append("|---|---|---|---|---|")
            for cat, c in s["by_category"].items():
                cat_lines.append(f"| {cat} | {c.get('win', 0)} | {c.get('tie', 0)} | {c.get('loss', 0)} | "
                                 f"{c.get('unknown', 0) + c.get('failed', 0)} |")
    text = "\n".join(lines + cat_lines) + "\n"
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
