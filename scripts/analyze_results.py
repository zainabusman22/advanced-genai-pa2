"""Evidence for the report's interpretation / error-analysis section.

For one judged comparison, writes a Markdown file with:
  * response-length statistics per model (words) and length-vs-verdict correlation,
  * refusal rate per model (keyword heuristic), overall and by category,
  * representative examples where the candidate wins, loses and ties, with the judge's reason.

  python scripts/analyze_results.py --judged outputs/eval/uf_dpo_vs_sft_judged.jsonl \
      --pairs outputs/eval/uf_dpo_vs_sft_pairs.jsonl --out report/analysis_uf.md
"""

from __future__ import annotations

import argparse
import random
import re
import statistics
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from alignment.utils import read_jsonl, write_json  # noqa: E402

REFUSAL = re.compile(
    r"\b(I can(?:'|no)t|I cannot|I won't|I will not|I'm (?:not able|unable|sorry)|I am (?:not able|unable|sorry)|"
    r"as an AI|I must decline|not (?:able|going) to (?:help|assist|provide))", re.I)


def words(text):
    return len((text or "").split())


def clip(text, n):
    text = (text or "").strip().replace("\n", "  \n> ")
    return text if len(text) <= n else text[:n] + " [...]"


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--judged", required=True)
    p.add_argument("--pairs", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--n_examples", type=int, default=3)
    p.add_argument("--max_chars", type=int, default=700)
    p.add_argument("--seed", type=int, default=0)
    args = p.parse_args()

    pairs = {r["pair_id"]: r for r in read_jsonl(args.pairs)}
    judged = read_jsonl(args.judged)
    rows = []
    for j in judged:
        pr = pairs[j["pair_id"]]
        cand_is_a = j["hidden"]["candidate_position"] == "A"
        cand = pr["response_a"] if cand_is_a else pr["response_b"]
        base = pr["response_b"] if cand_is_a else pr["response_a"]
        rows.append({**j, "instruction": pr["instruction"], "cand": cand, "base": base})
    if not rows:
        raise SystemExit("no judged rows")
    cand_name, base_name = rows[0]["hidden"]["candidate"], rows[0]["hidden"]["baseline"]

    stats = {}
    for key, name in (("cand", cand_name), ("base", base_name)):
        lens = [words(r[key]) for r in rows]
        stats[name] = {"mean_words": statistics.mean(lens), "median_words": statistics.median(lens),
                       "empty_responses": sum(l == 0 for l in lens),
                       "refusal_rate": sum(bool(REFUSAL.search(r[key] or "")) for r in rows) / len(rows)}
    decided = [r for r in rows if r["outcome"] in ("win", "loss")]
    longer_wins = sum((words(r["cand"]) > words(r["base"])) == (r["outcome"] == "win") for r in decided)
    stats["longer_response_wins_rate"] = longer_wins / len(decided) if decided else None
    stats["candidate_longer_rate"] = sum(words(r["cand"]) > words(r["base"]) for r in rows) / len(rows)

    by_cat = defaultdict(lambda: {"n": 0, "cand_refusals": 0, "base_refusals": 0})
    for r in rows:
        c = by_cat[r.get("category") or "all"]
        c["n"] += 1
        c["cand_refusals"] += bool(REFUSAL.search(r["cand"] or ""))
        c["base_refusals"] += bool(REFUSAL.search(r["base"] or ""))
    stats["refusals_by_category"] = dict(by_cat)

    rng = random.Random(args.seed)
    lines = [f"# Analysis: {cand_name} (candidate) vs {base_name} (baseline)\n",
             f"Source: `{args.judged}` ({len(rows)} judged pairs)\n",
             "## Length and refusals\n",
             "| Model | Mean words | Median words | Empty | Refusal rate (heuristic) |", "|---|---|---|---|---|"]
    for name in (cand_name, base_name):
        s = stats[name]
        lines.append(f"| {name} | {s['mean_words']:.1f} | {s['median_words']:.0f} | {s['empty_responses']} | "
                     f"{100 * s['refusal_rate']:.1f}% |")
    if stats["longer_response_wins_rate"] is not None:
        lines.append(f"\nCandidate is longer in {100 * stats['candidate_longer_rate']:.1f}% of pairs; among decided "
                     f"pairs the longer response wins {100 * stats['longer_response_wins_rate']:.1f}% of the time "
                     "(a high value suggests length or verbosity bias).\n")
    if len(by_cat) > 1:
        lines += ["| Category | N | Candidate refusals | Baseline refusals |", "|---|---|---|---|"]
        for cat, c in sorted(by_cat.items()):
            lines.append(f"| {cat} | {c['n']} | {c['cand_refusals']} | {c['base_refusals']} |")

    for outcome, title in (("win", "Candidate better"), ("loss", "Candidate worse (regressions)"),
                           ("tie", "Little or no difference")):
        pool = [r for r in rows if r["outcome"] == outcome]
        lines.append(f"\n## {title}: {len(pool)} pairs, {min(len(pool), args.n_examples)} shown\n")
        for r in rng.sample(pool, min(len(pool), args.n_examples)):
            lines += [f"### `{r['pair_id']}` ({r.get('category') or 'n/a'})",
                      f"**Prompt:** {clip(r['instruction'], 400)}\n",
                      f"**{cand_name}:**\n> {clip(r['cand'], args.max_chars)}\n",
                      f"**{base_name}:**\n> {clip(r['base'], args.max_chars)}\n",
                      f"**Judge:** {r['verdict']}: {r.get('reason', '')}\n"]

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    write_json(out.with_suffix(".json"), stats)
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
