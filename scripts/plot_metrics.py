"""Plot training curves from metrics.jsonl files.

  python scripts/plot_metrics.py --sft outputs/sft/metrics.jsonl \
      --dpo HH=outputs/dpo_hh/metrics.jsonl UF=outputs/dpo_uf/metrics.jsonl \
      --lora outputs/lora_dpo_persona/metrics.jsonl --out_dir report/figures
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from alignment.utils import read_jsonl  # noqa: E402

COLORS = ["#2a6fdb", "#d1495b", "#2e933c", "#edae49"]


def ema(values, alpha):
    if alpha <= 0 or not values:
        return values
    out, cur = [], values[0]
    for v in values:
        cur = alpha * cur + (1 - alpha) * v
        out.append(cur)
    return out


def series(rows, kind, key):
    pts = [(r["step"], r[key]) for r in rows if r.get("type") == kind and r.get(key) is not None]
    return [p[0] for p in pts], [p[1] for p in pts]


def mark_best(ax, rows, key, color):
    best = [r for r in rows if r.get("saved_best")]
    if best:
        b = best[-1]
        ax.axvline(b["step"], color=color, ls=":", lw=1)
        ax.scatter([b["step"]], [b[key]], color=color, marker="*", s=120, zorder=5, label=f"best ckpt (step {b['step']})")


def plot_sft(path, out_dir, smooth):
    rows = read_jsonl(path)
    fig, ax = plt.subplots(figsize=(6.5, 4))
    x, y = series(rows, "train", "train_loss")
    ax.plot(x, y, color=COLORS[0], alpha=0.25, lw=1)
    ax.plot(x, ema(y, smooth), color=COLORS[0], label=f"train loss (EMA {smooth})")
    x, y = series(rows, "val", "val_loss")
    ax.plot(x, y, "o-", color=COLORS[1], label="validation loss (1k ex.)")
    mark_best(ax, rows, "val_loss", COLORS[1])
    ax.set_xlabel("optimizer step (effective batch 32)")
    ax.set_ylabel("response-token NLL")
    ax.set_title("SFT: Qwen2.5-0.5B on UltraChat (30k)")
    ax.grid(alpha=0.3)
    ax.legend()
    fig.tight_layout()
    fig.savefig(out_dir / "sft_loss.png", dpi=160)
    plt.close(fig)


def plot_dpo(named_paths, out_dir, smooth):
    fig, axes = plt.subplots(1, 3, figsize=(15, 4))
    for i, (name, path) in enumerate(named_paths):
        rows = read_jsonl(path)
        c = COLORS[i % len(COLORS)]
        for ax, tkey, vkey, label in (
            (axes[0], "train_loss", "val_loss", "DPO loss"),
            (axes[1], "train_acc", "val_acc", "reward accuracy"),
            (axes[2], "train_margin", "val_margin", "reward margin"),
        ):
            x, y = series(rows, "train", tkey)
            if y:
                ax.plot(x, ema(y, smooth), color=c, alpha=0.5, lw=1, label=f"{name} train (EMA {smooth})")
            x, y = series(rows, "val", vkey)
            ax.plot(x, y, "o-", color=c, label=f"{name} val")
            if vkey == "val_acc":
                mark_best(ax, rows, vkey, c)
            ax.set_title(label)
            ax.set_xlabel("optimizer step (effective batch 32)")
            ax.grid(alpha=0.3)
    axes[0].axhline(np.log(2), color="gray", ls="--", lw=1, label="log 2")
    for ax in axes:
        ax.legend(fontsize=7)
    fig.suptitle("From-scratch DPO from the SFT best checkpoint (beta = 0.1)")
    fig.tight_layout()
    fig.savefig(out_dir / "dpo_curves.png", dpi=160)
    plt.close(fig)


def plot_lora(named_paths, out_dir):
    """Overlay one or more TRL LoRA-DPO runs: train loss, val loss (log scale), val reward margin."""

    fig, axes = plt.subplots(1, 3, figsize=(15, 4))
    for i, (name, path) in enumerate(named_paths):
        rows = read_jsonl(path)
        c = COLORS[i % len(COLORS)]
        tr = [r for r in rows if "loss" in r and "eval_loss" not in r and "step" in r]
        ev = [r for r in rows if "eval_loss" in r]
        axes[0].plot([r["step"] for r in tr], [r["loss"] for r in tr], color=c, label=name)
        axes[1].plot([r["step"] for r in ev], [r["eval_loss"] for r in ev], "o-", color=c, label=name)
        if ev and "eval_rewards/margins" in ev[0]:
            acc = ev[-1].get("eval_rewards/accuracies")
            axes[2].plot([r["step"] for r in ev], [r["eval_rewards/margins"] for r in ev], "o-", color=c,
                         label=f"{name} (final val acc {acc:.2f})" if acc is not None else name)
    axes[0].set_title("LoRA-DPO train loss")
    axes[1].set_title("LoRA-DPO validation loss (log scale)")
    axes[1].set_yscale("log")
    axes[2].set_title("LoRA-DPO validation reward margin")
    for ax in axes:
        ax.axhline(np.log(2), color="gray", ls="--", lw=1) if ax is not axes[2] else None
        ax.set_xlabel("optimizer step (effective batch 16)")
        ax.grid(alpha=0.3)
        ax.legend(fontsize=8)
    fig.suptitle("TRL DPOTrainer + LoRA persona tuning (SmolLM2-360M-Instruct, 200 train / 20 val pairs, beta = 0.1)")
    fig.tight_layout()
    fig.savefig(out_dir / "lora_dpo_curves.png", dpi=160)
    plt.close(fig)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--sft", default=None)
    p.add_argument("--dpo", nargs="*", default=[], help="NAME=path/to/metrics.jsonl")
    p.add_argument("--lora", nargs="*", default=[], help="NAME=path/to/metrics.jsonl (or a bare path)")
    p.add_argument("--out_dir", default=str(ROOT / "report/figures"))
    p.add_argument("--smooth", type=float, default=0.8)
    args = p.parse_args()
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    if args.sft:
        plot_sft(args.sft, out, args.smooth)
    if args.dpo:
        plot_dpo([item.split("=", 1) for item in args.dpo], out, args.smooth)
    if args.lora:
        plot_lora([item.split("=", 1) if "=" in item else ("LoRA-DPO", item) for item in args.lora], out)
    print(f"figures written to {out}")


if __name__ == "__main__":
    main()
