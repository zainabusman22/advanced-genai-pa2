"""From-scratch DPO training (no TRL / HF Trainer).

The trainable policy and the frozen reference are both initialised from the
SFT ``best`` checkpoint. With two GPUs the policy (fp32 weights, fp16
autocast, GradScaler) lives on cuda:0 and the frozen fp16 reference on cuda:1.

Logged to ``metrics.jsonl``:
  train rows: DPO loss, reward accuracy, reward margin, chosen/rejected rewards
  val rows:   the same quantities over the whole validation set
The checkpoint with the best validation reward accuracy is saved as ``best``.

Example:
  python scripts/train_dpo.py --sft_path outputs/sft/best \
      --train_path data/processed/hh_train.jsonl --val_path data/processed/hh_val.jsonl \
      --output_dir outputs/dpo_hh
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

from alignment.core import build_preference_batch, dpo_loss_from_logps, sequence_logprobs  # noqa: E402
from alignment.prompting import format_prompt  # noqa: E402
from alignment.training import (autocast_ctx, build_optimizer, load_model, load_tokenizer,  # noqa: E402
                                make_scaler, resolve_device)
from alignment.utils import (Timer, append_jsonl, load_records, save_checkpoint, set_seed,  # noqa: E402
                             write_json)


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--sft_path", default=str(ROOT / "outputs/sft/best"))
    p.add_argument("--reference_path", default=None, help="defaults to --sft_path")
    p.add_argument("--train_path", required=True)
    p.add_argument("--val_path", required=True)
    p.add_argument("--output_dir", required=True)
    p.add_argument("--beta", type=float, default=0.1)
    p.add_argument("--max_length", type=int, default=512)
    p.add_argument("--max_prompt_length", type=int, default=256)
    p.add_argument("--batch_size", type=int, default=1)
    p.add_argument("--eval_batch_size", type=int, default=4)
    p.add_argument("--grad_accum", type=int, default=32)
    p.add_argument("--lr", type=float, default=1e-6)
    p.add_argument("--warmup_steps", type=int, default=0, help="linear warmup; LR is constant afterwards")
    p.add_argument("--weight_decay", type=float, default=0.0)
    p.add_argument("--optimizer", default="adamw", choices=["adamw", "rmsprop"])
    p.add_argument("--max_grad_norm", type=float, default=1.0)
    p.add_argument("--epochs", type=int, default=1)
    p.add_argument("--max_steps", type=int, default=-1)
    p.add_argument("--limit_train", type=int, default=-1)
    p.add_argument("--limit_val", type=int, default=-1)
    p.add_argument("--eval_every", type=int, default=50, help="optimizer steps")
    p.add_argument("--eval_at_start", action=argparse.BooleanOptionalAction, default=True)
    p.add_argument("--log_every", type=int, default=5)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--policy_device", default="auto")
    p.add_argument("--reference_device", default="auto")
    p.add_argument("--no_fp16", action="store_true")
    p.add_argument("--gradient_checkpointing", action="store_true")
    p.add_argument("--save_dtype", default="fp32", choices=["fp32", "fp16"])
    return p.parse_args()


class PreferenceCollator:
    def __init__(self, tokenizer, max_length):
        self.tokenizer, self.max_length = tokenizer, max_length

    def __call__(self, rows):
        prompts = [format_prompt(r["instruction"]) for r in rows]
        batch = build_preference_batch(self.tokenizer, prompts, [r["chosen"] for r in rows],
                                       [r["rejected"] for r in rows], self.max_length)
        batch["ids"] = [r.get("id") for r in rows]
        return batch


def pair_logps(model, batch, device, use_fp16):
    with autocast_ctx(device, use_fp16):
        chosen = sequence_logprobs(model, batch["chosen"])
        rejected = sequence_logprobs(model, batch["rejected"])
    return chosen, rejected


@torch.no_grad()
def evaluate(policy, reference, loader, args, pdev, rdev, use_fp16):
    policy.eval()
    sums = {"loss": 0.0, "acc": 0.0, "margin": 0.0, "chosen_reward": 0.0, "rejected_reward": 0.0,
            "policy_chosen_logp": 0.0, "policy_rejected_logp": 0.0}
    n = 0
    for batch in tqdm(loader, desc="val", leave=False):
        pc, pr = pair_logps(policy, batch, pdev, use_fp16)
        rc, rr = pair_logps(reference, batch, rdev, use_fp16)
        loss, m = dpo_loss_from_logps(pc, pr, rc, rr, args.beta, return_metrics=True)
        k = pc.shape[0]
        sums["loss"] += loss.item() * k
        sums["acc"] += m["reward_correct"].sum().item()
        sums["margin"] += m["reward_margin"].sum().item()
        sums["chosen_reward"] += m["chosen_rewards"].sum().item()
        sums["rejected_reward"] += m["rejected_rewards"].sum().item()
        sums["policy_chosen_logp"] += pc.sum().item()
        sums["policy_rejected_logp"] += pr.sum().item()
        n += k
    policy.train()
    return {f"val_{k}": v / max(1, n) for k, v in sums.items()} | {"val_pairs": n}


def filter_rows(rows, tokenizer, max_prompt_length):
    kept = [r for r in rows
            if len(tokenizer.encode(format_prompt(r["instruction"]), add_special_tokens=False)) <= max_prompt_length]
    if len(kept) != len(rows):
        print(f"dropped {len(rows) - len(kept)} rows with prompt > {max_prompt_length} tokens")
    return kept


def main():
    args = parse_args()
    set_seed(args.seed)
    n_gpu = torch.cuda.device_count()
    pdev = resolve_device(args.policy_device)
    if args.reference_device == "auto":
        rdev = torch.device("cuda:1") if n_gpu >= 2 else pdev
    else:
        rdev = torch.device(args.reference_device)
    use_fp16 = not args.no_fp16 and pdev.type == "cuda"
    ref_path = args.reference_path or args.sft_path

    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    metrics_path = out / "metrics.jsonl"
    if metrics_path.exists():
        metrics_path.unlink()

    tokenizer = load_tokenizer(args.sft_path)
    policy = load_model(args.sft_path, pdev, torch.float32, args.gradient_checkpointing)
    policy.train()
    reference = load_model(ref_path, rdev, torch.float16 if rdev.type == "cuda" else torch.float32)
    reference.eval()
    reference.requires_grad_(False)

    train_rows = filter_rows(load_records(args.train_path), tokenizer, args.max_prompt_length)
    val_rows = filter_rows(load_records(args.val_path), tokenizer, args.max_prompt_length)
    if args.limit_train > 0:
        train_rows = train_rows[: args.limit_train]
    if args.limit_val > 0:
        val_rows = val_rows[: args.limit_val]

    collate = PreferenceCollator(tokenizer, args.max_length)
    gen = torch.Generator().manual_seed(args.seed)
    train_loader = DataLoader(train_rows, batch_size=args.batch_size, shuffle=True, generator=gen,
                              collate_fn=collate, num_workers=2 if pdev.type == "cuda" else 0)
    val_loader = DataLoader(val_rows, batch_size=args.eval_batch_size, shuffle=False, collate_fn=collate)

    steps_per_epoch = math.ceil(len(train_loader) / args.grad_accum)
    total_steps = steps_per_epoch * args.epochs
    if args.max_steps > 0:
        total_steps = min(total_steps, args.max_steps)

    optimizer = build_optimizer(policy, args.optimizer, args.lr, args.weight_decay)
    warm = args.warmup_steps
    scheduler = torch.optim.lr_scheduler.LambdaLR(optimizer, lambda s: min(1.0, (s + 1) / warm) if warm > 0 else 1.0)
    scaler = make_scaler(pdev, use_fp16)

    config = {**vars(args), "reference_path": ref_path, "policy_device": str(pdev), "reference_device": str(rdev),
              "fp16_autocast": use_fp16, "reference_dtype": "fp16" if rdev.type == "cuda" else "fp32",
              "n_train": len(train_rows), "n_val": len(val_rows), "total_optimizer_steps": total_steps,
              "effective_batch_size": args.batch_size * args.grad_accum}
    write_json(out / "config.json", config)
    print(config)

    timer = Timer()
    best_acc = -1.0
    step, micro = 0, 0

    def run_eval():
        nonlocal best_acc
        m = evaluate(policy, reference, val_loader, args, pdev, rdev, use_fp16)
        record = {"type": "val", "step": step, "pairs_seen": micro * args.batch_size,
                  "elapsed_s": timer.elapsed(), **m}
        if step > 0 and m["val_acc"] > best_acc:
            best_acc = m["val_acc"]
            save_checkpoint(policy, tokenizer, out / "best", args.save_dtype, {"step": step, **m})
            record["saved_best"] = True
        append_jsonl(metrics_path, record)
        print(record)

    if args.eval_at_start:
        run_eval()

    window = {"loss": 0.0, "acc": 0.0, "margin": 0.0, "chosen": 0.0, "rejected": 0.0, "n": 0}
    progress = tqdm(total=total_steps, desc="dpo")
    done = False
    for epoch in range(args.epochs):
        for batch in train_loader:
            pc, pr = pair_logps(policy, batch, pdev, use_fp16)
            with torch.no_grad():
                rc, rr = pair_logps(reference, batch, rdev, use_fp16)
            loss, m = dpo_loss_from_logps(pc, pr, rc, rr, args.beta, return_metrics=True)
            scaler.scale(loss / args.grad_accum).backward()

            k = pc.shape[0]
            window["loss"] += loss.item() * k
            window["acc"] += m["reward_correct"].sum().item()
            window["margin"] += m["reward_margin"].sum().item()
            window["chosen"] += m["chosen_rewards"].sum().item()
            window["rejected"] += m["rejected_rewards"].sum().item()
            window["n"] += k
            micro += 1
            last_micro = micro == len(train_loader) * (epoch + 1)
            if micro % args.grad_accum != 0 and not last_micro:
                continue

            scaler.unscale_(optimizer)
            grad_norm = torch.nn.utils.clip_grad_norm_(policy.parameters(), args.max_grad_norm)
            scaler.step(optimizer)
            scaler.update()
            optimizer.zero_grad(set_to_none=True)
            scheduler.step()
            step += 1
            progress.update(1)

            if step % args.log_every == 0 or step == total_steps:
                n = max(1, window["n"])
                record = {"type": "train", "step": step, "pairs_seen": micro * args.batch_size,
                          "train_loss": window["loss"] / n, "train_acc": window["acc"] / n,
                          "train_margin": window["margin"] / n, "train_chosen_reward": window["chosen"] / n,
                          "train_rejected_reward": window["rejected"] / n, "lr": scheduler.get_last_lr()[0],
                          "grad_norm": float(grad_norm), "elapsed_s": timer.elapsed()}
                append_jsonl(metrics_path, record)
                progress.set_postfix(loss=f"{record['train_loss']:.4f}", acc=f"{record['train_acc']:.2f}")
                window = {key: 0.0 for key in window} | {"n": 0}

            if step >= total_steps:
                done = True
                break
            if args.eval_every > 0 and step % args.eval_every == 0:
                run_eval()
        if done:
            break

    run_eval()
    save_checkpoint(policy, tokenizer, out / "final", args.save_dtype, {"step": step})
    write_json(out / "summary.json", {"best_val_acc": best_acc, "steps": step, "elapsed_s": timer.elapsed()})
    print(f"done: best_val_acc={best_acc:.4f} steps={step} time={timer.elapsed() / 60:.1f} min")


if __name__ == "__main__":
    main()
