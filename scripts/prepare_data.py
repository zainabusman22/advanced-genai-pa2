"""Download and convert all training/eval data into simple JSONL/JSON files.

Outputs (default ``data/processed``):
  sft_train.jsonl / sft_val.jsonl          {id, instruction, response}
  hh_train.jsonl  / hh_val.jsonl           {id, instruction, chosen, rejected}
  uf_train.jsonl  / uf_val.jsonl           {id, instruction, chosen, rejected}
and ``eval_sets/alpaca_eval_300_seed42.json``.

Sources (all single-turn after filtering):
  SFT     HuggingFaceH4/ultrachat_200k   train_sft / test_sft (first user/assistant turn)
  HH      Anthropic/hh-rlhf              train / test (single-turn conversations only)
  UF      HuggingFaceH4/ultrafeedback_binarized  train_prefs / test_prefs
  Eval    tatsu-lab/alpaca_eval          alpaca_eval.json, 300 prompts sampled with seed 42

Every subset is drawn by shuffling the source split with ``--seed`` and taking
the first N rows that pass the filters, so the files are reproducible.
"""

from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from alignment.prompting import configure_tokenizer, format_prompt  # noqa: E402
from alignment.utils import read_json, write_json, write_jsonl  # noqa: E402


STATS = {}


def take_filtered(dataset, convert, n, seed, desc):
    order = list(range(len(dataset)))
    random.Random(seed).shuffle(order)
    out, seen = [], 0
    for idx in order:
        seen += 1
        row = convert(dataset[idx], idx)
        if row is not None:
            out.append(row)
            if len(out) >= n:
                break
    print(f"[{desc}] kept {len(out)} / scanned {seen} (source size {len(dataset)})")
    STATS[desc] = {"kept": len(out), "scanned": seen, "rejected_by_filters": seen - len(out),
                   "source_size": len(dataset), "seed": seed}
    if len(out) < n:
        print(f"[{desc}] WARNING: only {len(out)} rows passed filters (wanted {n})")
    return out


class LengthFilter:
    def __init__(self, tokenizer, max_prompt_tokens, max_total_tokens):
        self.tok = tokenizer
        self.max_prompt = max_prompt_tokens
        self.max_total = max_total_tokens

    def n_tokens(self, text):
        return len(self.tok.encode(text, add_special_tokens=False))

    def prompt_ok(self, instruction):
        return self.n_tokens(format_prompt(instruction)) <= self.max_prompt


def clean(text):
    return (text or "").strip()


# --------------------------------------------------------------------------- #
def sft_converter(lf, prefix):
    def convert(row, idx):
        msgs = row.get("messages") or []
        if len(msgs) < 2 or msgs[0]["role"] != "user" or msgs[1]["role"] != "assistant":
            return None
        instruction, response = clean(msgs[0]["content"]), clean(msgs[1]["content"])
        if not instruction or not response or not lf.prompt_ok(instruction):
            return None
        return {"id": f"{prefix}-{idx}", "instruction": instruction, "response": response}

    return convert


def parse_hh(text):
    """Split a single-turn HH transcript into (prompt, response) or None."""

    if text.count("\n\nHuman:") != 1 or text.count("\n\nAssistant:") != 1:
        return None
    head, _, response = text.partition("\n\nAssistant:")
    _, _, prompt = head.partition("\n\nHuman:")
    return clean(prompt), clean(response)


def hh_converter(lf, prefix):
    def convert(row, idx):
        c, r = parse_hh(row["chosen"]), parse_hh(row["rejected"])
        if c is None or r is None or c[0] != r[0]:
            return None
        instruction, chosen, rejected = c[0], c[1], r[1]
        if not instruction or not chosen or not rejected or chosen == rejected:
            return None
        if not lf.prompt_ok(instruction):
            return None
        return {"id": f"{prefix}-{idx}", "instruction": instruction, "chosen": chosen, "rejected": rejected}

    return convert


def uf_converter(lf, prefix):
    def convert(row, idx):
        chosen_msgs, rejected_msgs = row["chosen"], row["rejected"]
        if len(chosen_msgs) != 2 or len(rejected_msgs) != 2:
            return None
        instruction = clean(row["prompt"])
        chosen, rejected = clean(chosen_msgs[-1]["content"]), clean(rejected_msgs[-1]["content"])
        if not instruction or not chosen or not rejected or chosen == rejected:
            return None
        if row.get("score_chosen") is not None and row.get("score_chosen") == row.get("score_rejected"):
            return None
        if not lf.prompt_ok(instruction):
            return None
        return {"id": f"{prefix}-{idx}", "instruction": instruction, "chosen": chosen, "rejected": rejected}

    return convert


# --------------------------------------------------------------------------- #
def build_alpaca_eval(out_path, n, seed):
    from huggingface_hub import hf_hub_download

    path = hf_hub_download("tatsu-lab/alpaca_eval", "alpaca_eval.json", repo_type="dataset")
    rows = read_json(path)
    indices = random.Random(seed).sample(range(len(rows)), n)
    subset = [
        {
            "id": f"alpaca-eval-{seed}-{i:04d}",
            "source_index": idx,
            "instruction": rows[idx]["instruction"],
            "dataset": "alpaca_eval",
            "source_dataset": rows[idx].get("dataset"),
        }
        for i, idx in enumerate(indices)
    ]
    write_json(out_path, subset)
    print(f"[alpaca_eval] wrote {len(subset)} prompts -> {out_path}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--out_dir", default=str(ROOT / "data" / "processed"))
    p.add_argument("--tokenizer", default="Qwen/Qwen2.5-0.5B")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--sft_train", type=int, default=30000)
    p.add_argument("--sft_val", type=int, default=1000)
    p.add_argument("--dpo_train", type=int, default=15000)
    p.add_argument("--dpo_val", type=int, default=1000)
    p.add_argument("--max_prompt_tokens", type=int, default=256)
    p.add_argument("--max_length", type=int, default=512)
    p.add_argument("--alpaca_n", type=int, default=300)
    p.add_argument("--only", nargs="*", default=["sft", "hh", "uf", "alpaca"])
    args = p.parse_args()

    from datasets import load_dataset
    from transformers import AutoTokenizer

    out = Path(args.out_dir)
    tok = configure_tokenizer(AutoTokenizer.from_pretrained(args.tokenizer))
    lf = LengthFilter(tok, args.max_prompt_tokens, args.max_length)

    if "sft" in args.only:
        ds = load_dataset("HuggingFaceH4/ultrachat_200k")
        write_jsonl(out / "sft_train.jsonl", take_filtered(ds["train_sft"], sft_converter(lf, "ultrachat-train"), args.sft_train, args.seed, "sft_train"))
        write_jsonl(out / "sft_val.jsonl", take_filtered(ds["test_sft"], sft_converter(lf, "ultrachat-test"), args.sft_val, args.seed, "sft_val"))

    if "hh" in args.only:
        ds = load_dataset("Anthropic/hh-rlhf")
        write_jsonl(out / "hh_train.jsonl", take_filtered(ds["train"], hh_converter(lf, "hh-train"), args.dpo_train, args.seed, "hh_train"))
        write_jsonl(out / "hh_val.jsonl", take_filtered(ds["test"], hh_converter(lf, "hh-test"), args.dpo_val, args.seed, "hh_val"))

    if "uf" in args.only:
        ds = load_dataset("HuggingFaceH4/ultrafeedback_binarized")
        write_jsonl(out / "uf_train.jsonl", take_filtered(ds["train_prefs"], uf_converter(lf, "uf-train"), args.dpo_train, args.seed, "uf_train"))
        write_jsonl(out / "uf_val.jsonl", take_filtered(ds["test_prefs"], uf_converter(lf, "uf-test"), args.dpo_val, args.seed, "uf_val"))

    if "alpaca" in args.only:
        build_alpaca_eval(ROOT / "eval_sets" / f"alpaca_eval_{args.alpaca_n}_seed{args.seed}.json", args.alpaca_n, args.seed)

    STATS["settings"] = vars(args)
    write_json(out / "data_stats.json", STATS)
    print(f"stats -> {out / 'data_stats.json'}")


if __name__ == "__main__":
    main()
