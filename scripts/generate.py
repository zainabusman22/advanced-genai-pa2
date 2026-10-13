"""Generate responses for a fixed evaluation set.

Writes a JSON list of ``{id, instruction, output, generator, dataset, ...}``
and a resumable ``<output>.partial.jsonl`` while running.

Qwen models (base / SFT / DPO) use the ChatML template from
``alignment.prompting`` (``--template chatml``); the SmolLM2 persona models use
the tokenizer's own chat template (``--template tokenizer``).

Example:
  python scripts/generate.py --model_path outputs/sft/best \
      --eval_set eval_sets/alpaca_eval_300_seed42.json \
      --output_path outputs/generations/alpaca/sft.json --generator_name qwen2.5-0.5b-sft
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import torch  # noqa: E402
from tqdm.auto import tqdm  # noqa: E402
from transformers import AutoModelForCausalLM, AutoTokenizer  # noqa: E402

from alignment.prompting import STOP_STRINGS, configure_tokenizer, format_prompt  # noqa: E402
from alignment.utils import append_jsonl, load_records, read_jsonl, set_seed, write_json  # noqa: E402


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--model_path", required=True)
    p.add_argument("--adapter_path", default=None, help="optional PEFT/LoRA adapter directory")
    p.add_argument("--eval_set", required=True)
    p.add_argument("--output_path", required=True)
    p.add_argument("--generator_name", required=True)
    p.add_argument("--dataset_name", default=None)
    p.add_argument("--template", default="chatml", choices=["chatml", "tokenizer"])
    p.add_argument("--system_prompt", default=None, help="only used with --template tokenizer")
    p.add_argument("--max_new_tokens", type=int, default=512)
    p.add_argument("--batch_size", type=int, default=16)
    p.add_argument("--repetition_penalty", type=float, default=1.1)
    p.add_argument("--no_repeat_ngram_size", type=int, default=0)
    p.add_argument("--limit", type=int, default=-1, help="sanity check on the first N prompts")
    p.add_argument("--device", default="cuda:0" if torch.cuda.is_available() else "cpu")
    p.add_argument("--dtype", default="fp16", choices=["fp16", "fp32"])
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--overwrite", action="store_true")
    return p.parse_args()


def build_prompt(tokenizer, instruction, template, system_prompt):
    if template == "chatml":
        return format_prompt(instruction)
    messages = [{"role": "system", "content": system_prompt}] if system_prompt else []
    messages.append({"role": "user", "content": instruction})
    return tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)


def stop_token_ids(tokenizer):
    ids = set()
    if tokenizer.eos_token_id is not None:
        ids.add(int(tokenizer.eos_token_id))
    vocab = tokenizer.get_vocab()
    for s in STOP_STRINGS:
        if s in vocab:
            ids.add(int(vocab[s]))
    return sorted(ids)


def clean_output(text):
    for s in STOP_STRINGS:
        text = text.split(s)[0]
    return text.strip()


def main():
    args = parse_args()
    set_seed(args.seed)
    device = torch.device(args.device)
    dtype = torch.float16 if (args.dtype == "fp16" and device.type == "cuda") else torch.float32

    out_path = Path(args.output_path)
    partial_path = Path(str(out_path) + ".partial.jsonl")
    if out_path.exists() and not args.overwrite and args.limit <= 0:
        print(f"{out_path} exists; skipping (use --overwrite to regenerate)")
        return
    if args.overwrite and partial_path.exists():
        partial_path.unlink()

    rows = load_records(args.eval_set)
    if args.limit > 0:
        rows = rows[: args.limit]

    tokenizer = AutoTokenizer.from_pretrained(args.adapter_path or args.model_path)
    if args.template == "chatml":
        configure_tokenizer(tokenizer)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    tokenizer.padding_side = "left"

    model = AutoModelForCausalLM.from_pretrained(args.model_path, torch_dtype=dtype).to(device)
    if args.adapter_path:
        from peft import PeftModel

        model = PeftModel.from_pretrained(model, args.adapter_path)
        model = model.merge_and_unload()
    model.eval()

    done = {r["id"]: r for r in read_jsonl(partial_path)} if args.limit <= 0 else {}
    todo = [r for r in rows if r["id"] not in done]
    print(f"{len(done)} cached, {len(todo)} to generate")
    prompts = {r["id"]: build_prompt(tokenizer, r["instruction"], args.template, args.system_prompt) for r in todo}
    # Batch similar-length prompts together; output order is restored below.
    todo.sort(key=lambda r: len(prompts[r["id"]]))

    gen_kwargs = dict(max_new_tokens=args.max_new_tokens, do_sample=False, num_beams=1,
                      repetition_penalty=args.repetition_penalty, eos_token_id=stop_token_ids(tokenizer),
                      pad_token_id=tokenizer.pad_token_id)
    if args.no_repeat_ngram_size > 0:
        gen_kwargs["no_repeat_ngram_size"] = args.no_repeat_ngram_size

    sanity = []
    for start in tqdm(range(0, len(todo), args.batch_size), desc=args.generator_name):
        chunk = todo[start:start + args.batch_size]
        enc = tokenizer([prompts[r["id"]] for r in chunk], return_tensors="pt", padding=True,
                        add_special_tokens=False).to(device)
        with torch.no_grad():
            gen = model.generate(**enc, **gen_kwargs)
        new_tokens = gen[:, enc["input_ids"].shape[1]:]
        texts = tokenizer.batch_decode(new_tokens, skip_special_tokens=False)
        for r, text, toks in zip(chunk, texts, new_tokens):
            n_gen = int((toks != tokenizer.pad_token_id).sum())
            entry = {
                "id": r["id"],
                "instruction": r["instruction"],
                "output": clean_output(text),
                "generator": args.generator_name,
                "dataset": args.dataset_name or r.get("dataset", "eval"),
                "category": r.get("category"),
                "n_new_tokens": n_gen,
                "hit_max_new_tokens": n_gen >= args.max_new_tokens,
            }
            if args.limit > 0:
                sanity.append(entry)
            else:
                append_jsonl(partial_path, entry)
                done[r["id"]] = entry

    if args.limit > 0:
        for e in sanity:
            print("=" * 80)
            print("PROMPT:", e["instruction"][:300])
            print("-" * 80)
            print(e["output"][:1200])
            print(f"[{e['n_new_tokens']} tokens, hit_max={e['hit_max_new_tokens']}]")
        return

    ordered = [done[r["id"]] for r in rows]
    settings = {k: v for k, v in gen_kwargs.items() if k not in ("eos_token_id", "pad_token_id")}
    for e in ordered:
        e["generation_settings"] = settings | {"template": args.template}
    write_json(out_path, ordered)
    if partial_path.exists():
        os.remove(partial_path)
    n_trunc = sum(e["hit_max_new_tokens"] for e in ordered)
    print(f"wrote {len(ordered)} generations -> {out_path} ({n_trunc} hit max_new_tokens)")


if __name__ == "__main__":
    main()
