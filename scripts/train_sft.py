"""From-scratch supervised fine-tuning (no HF Trainer).

Features: ChatML prompt formatting, response-only loss masking, right padding +
attention masks, gradient accumulation, fp16 autocast with fp32 master weights
and GradScaler, gradient clipping, warmup + cosine LR, validation loss,
``best`` / ``final`` checkpoints and an incremental ``metrics.jsonl``.

Example (Kaggle):
  python scripts/train_sft.py --train_path data/processed/sft_train.jsonl \
      --val_path data/processed/sft_val.jsonl --output_dir outputs/sft
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import torch  # noqa: E402
from torch.utils.data import DataLoader  # noqa: E402
from tqdm.auto import tqdm  # noqa: E402

from alignment.core import build_lm_batch, sequence_logprobs  # noqa: E402
from alignment.prompting import format_prompt  # noqa: E402
from alignment.training import (autocast_ctx, build_optimizer, load_model, load_tokenizer,  # noqa: E402
                                make_scaler, resolve_device)
from alignment.utils import (Timer, append_jsonl, load_records, save_checkpoint, set_seed,  # noqa: E402
                             warmup_cosine, write_json)


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--model_name", default="Qwen/Qwen2.5-0.5B")
    p.add_argument("--train_path", default=str(ROOT / "data/processed/sft_train.jsonl"))
    p.add_argument("--val_path", default=str(ROOT / "data/processed/sft_val.jsonl"))
    p.add_argument("--output_dir", default=str(ROOT / "outputs/sft"))
    p.add_argument("--max_length", type=int, default=512)
    p.add_argument("--batch_size", type=int, default=2)
    p.add_argument("--eval_batch_size", type=int, default=8)
    p.add_argument("--grad_accum", type=int, default=16)
    p.add_argument("--lr", type=float, default=2e-5)
    p.add_argument("--warmup_ratio", type=float, default=0.03)
    p.add_argument("--weight_decay", type=float, default=0.1)
    p.add_argument("--optimizer", default="adamw", choices=["adamw", "rmsprop"])
    p.add_argument("--max_grad_norm", type=float, default=1.0)
    p.add_argument("--epochs", type=int, default=1)
    p.add_argument("--max_steps", type=int, default=-1, help="cap optimizer steps (debug)")
    p.add_argument("--limit_train", type=int, default=-1)
    p.add_argument("--limit_val", type=int, default=-1)
    p.add_argument("--eval_every", type=int, default=100, help="optimizer steps")
    p.add_argument("--log_every", type=int, default=10, help="optimizer steps")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--device", default="auto")
    p.add_argument("--no_fp16", action="store_true")
    p.add_argument("--gradient_checkpointing", action="store_true")
    p.add_argument("--save_dtype", default="fp32", choices=["fp32", "fp16"])
    return p.parse_args()


class Collator:
    def __init__(self, tokenizer, max_length):
        self.tokenizer, self.max_length = tokenizer, max_length

    def __call__(self, rows):
        prompts = [format_prompt(r["instruction"]) for r in rows]
        responses = [r["response"] for r in rows]
        return build_lm_batch(self.tokenizer, prompts, responses, self.max_length)


@torch.no_grad()
def evaluate(model, loader, device, use_fp16):
    model.eval()
    total_nll, total_tokens = 0.0, 0
    for batch in tqdm(loader, desc="val", leave=False):
        with autocast_ctx(device, use_fp16):
            logps, counts = sequence_logprobs(model, batch, return_token_counts=True)
        total_nll += -logps.sum().item()
        total_tokens += counts.sum().item()
    model.train()
    loss = total_nll / max(1, total_tokens)
    return {"val_loss": loss, "val_ppl": math.exp(min(loss, 20)), "val_tokens": total_tokens}


def main():
    args = parse_args()
    set_seed(args.seed)
    device = resolve_device(args.device)
    use_fp16 = not args.no_fp16 and device.type == "cuda"
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    metrics_path = out / "metrics.jsonl"
    if metrics_path.exists():
        metrics_path.unlink()

    tokenizer = load_tokenizer(args.model_name)
    model = load_model(args.model_name, device, torch.float32, args.gradient_checkpointing)
    model.train()

    train_rows = load_records(args.train_path)
    val_rows = load_records(args.val_path)
    if args.limit_train > 0:
        train_rows = train_rows[: args.limit_train]
    if args.limit_val > 0:
        val_rows = val_rows[: args.limit_val]

    collate = Collator(tokenizer, args.max_length)
    gen = torch.Generator().manual_seed(args.seed)
    train_loader = DataLoader(train_rows, batch_size=args.batch_size, shuffle=True, generator=gen,
                              collate_fn=collate, num_workers=2 if device.type == "cuda" else 0)
    val_loader = DataLoader(val_rows, batch_size=args.eval_batch_size, shuffle=False, collate_fn=collate)

    steps_per_epoch = math.ceil(len(train_loader) / args.grad_accum)
    total_steps = steps_per_epoch * args.epochs
    if args.max_steps > 0:
        total_steps = min(total_steps, args.max_steps)
    warmup_steps = int(round(args.warmup_ratio * total_steps))

    optimizer = build_optimizer(model, args.optimizer, args.lr, args.weight_decay)
    scheduler = torch.optim.lr_scheduler.LambdaLR(optimizer, lambda s: warmup_cosine(s, total_steps, warmup_steps))
    scaler = make_scaler(device, use_fp16)

    config = {**vars(args), "device": str(device), "fp16_autocast": use_fp16, "n_train": len(train_rows),
              "n_val": len(val_rows), "total_optimizer_steps": total_steps, "warmup_steps": warmup_steps,
              "effective_batch_size": args.batch_size * args.grad_accum,
              "loss_reduction": "token-level mean over unmasked response tokens (per micro-batch)"}
    write_json(out / "config.json", config)
    print(config)

    timer = Timer()
    best_val = float("inf")
    step, micro = 0, 0
    window_loss, window_n = 0.0, 0
    done = False
    progress = tqdm(total=total_steps, desc="sft")

    def optimizer_step():
        scaler.unscale_(optimizer)
        grad_norm = torch.nn.utils.clip_grad_norm_(model.parameters(), args.max_grad_norm)
        scaler.step(optimizer)
        scaler.update()
        optimizer.zero_grad(set_to_none=True)
        scheduler.step()
        return float(grad_norm)

    def maybe_eval(force=False):
        nonlocal best_val
        if not force and (args.eval_every <= 0 or step % args.eval_every != 0):
            return
        m = evaluate(model, val_loader, device, use_fp16)
        record = {"type": "val", "step": step, "epoch_frac": step / max(1, steps_per_epoch),
                  "elapsed_s": timer.elapsed(), **m}
        if m["val_loss"] < best_val:
            best_val = m["val_loss"]
            save_checkpoint(model, tokenizer, out / "best", args.save_dtype, {"step": step, **m})
            record["saved_best"] = True
        append_jsonl(metrics_path, record)
        print(record)

    for epoch in range(args.epochs):
        for batch in train_loader:
            with autocast_ctx(device, use_fp16):
                logps, counts = sequence_logprobs(model, batch, return_token_counts=True)
                loss = -logps.sum() / counts.sum().clamp_min(1)
            scaler.scale(loss / args.grad_accum).backward()
            window_loss += loss.item()
            window_n += 1
            micro += 1
            last_micro = micro == len(train_loader) * (epoch + 1)
            if micro % args.grad_accum != 0 and not last_micro:
                continue

            grad_norm = optimizer_step()
            step += 1
            progress.update(1)
            if step % args.log_every == 0 or step == total_steps:
                record = {"type": "train", "step": step, "epoch_frac": step / max(1, steps_per_epoch),
                          "train_loss": window_loss / max(1, window_n), "lr": scheduler.get_last_lr()[0],
                          "grad_norm": grad_norm, "loss_scale": scaler.get_scale() if use_fp16 else 1.0,
                          "elapsed_s": timer.elapsed()}
                append_jsonl(metrics_path, record)
                progress.set_postfix(loss=f"{record['train_loss']:.4f}")
                window_loss, window_n = 0.0, 0
            if step < total_steps:
                maybe_eval()
            if step >= total_steps:
                done = True
                break
        if done:
            break

    maybe_eval(force=True)
    save_checkpoint(model, tokenizer, out / "final", args.save_dtype, {"step": step})
    write_json(out / "summary.json", {"best_val_loss": best_val, "steps": step, "elapsed_s": timer.elapsed()})
    print(f"done: best_val_loss={best_val:.4f} steps={step} time={timer.elapsed() / 60:.1f} min")


if __name__ == "__main__":
    main()
