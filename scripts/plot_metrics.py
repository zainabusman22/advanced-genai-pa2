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

plt.rcParams.update({"font.size": 12, "axes.titlesize": 13, "legend.fontsize": 9})
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


def style_handles(train_label, val_label, ckpt_label, show_log2=True, log2_label="log 2 (step-0 loss)"):
    """Gray proxy handles explaining line styles, shared by all runs in a figure legend."""

    from matplotlib.lines import Line2D

    handles = [Line2D([], [], color="gray", lw=1, alpha=0.7, label=train_label),
               Line2D([], [], color="gray", marker="o", label=val_label),
               Line2D([], [], color="gray", marker="*", ls="none", markersize=13, label=ckpt_label)]
    if show_log2:
        handles.append(Line2D([], [], color="gray", ls="--", lw=1, label=log2_label))
    return handles


def plot_dpo(named_paths, out_dir, smooth):
    from matplotlib.lines import Line2D

    fig, axes = plt.subplots(1, 3, figsize=(15, 4.3))
    run_handles = []
    for i, (name, path) in enumerate(named_paths):
        rows = read_jsonl(path)
        c = COLORS[i % len(COLORS)]
        run_handles.append(Line2D([], [], color=c, lw=3, label=name))
        best = [r for r in rows if r.get("saved_best")]
        for ax, tkey, vkey in ((axes[0], "train_loss", "val_loss"), (axes[1], "train_acc", "val_acc"),
                               (axes[2], "train_margin", "val_margin")):
            x, y = series(rows, "train", tkey)
            if y:
                ax.plot(x, ema(y, smooth), color=c, alpha=0.45, lw=1)
            x, y = series(rows, "val", vkey)
            ax.plot(x, y, "o-", color=c, ms=4)
            if best:
                ax.scatter([best[-1]["step"]], [best[-1][vkey]], color=c, marker="*", s=170, zorder=5,
                           edgecolors="black", linewidths=0.6)
    axes[0].axhline(np.log(2), color="gray", ls="--", lw=1)
    for ax, ylabel in zip(axes, ("DPO loss", "reward accuracy", "reward margin ($r_w - r_l$)")):
        ax.set_ylabel(ylabel)
        ax.set_xlabel("optimizer step (effective batch 32)")
        ax.grid(alpha=0.3)
    fig.legend(handles=run_handles + style_handles(f"training (EMA {smooth})", "validation (1,000 pairs)",
                                                   "selected checkpoint"),
               loc="lower center", ncol=7, fontsize=10.5, frameon=False, bbox_to_anchor=(0.5, -0.01))
    fig.tight_layout(rect=(0, 0.08, 1, 1))
    fig.savefig(out_dir / "dpo_curves.png", dpi=160)
    plt.close(fig)


def plot_lora(named_paths, out_dir):
    """Overlay one or more TRL LoRA-DPO runs: train loss, val loss (log scale), val reward margin."""

    from matplotlib.lines import Line2D

    fig, axes = plt.subplots(1, 3, figsize=(15, 4.3))
    run_handles = []
    for i, (name, path) in enumerate(named_paths):
        rows = read_jsonl(path)
        c = COLORS[i % len(COLORS)]
        run_handles.append(Line2D([], [], color=c, lw=3, label=name))
        tr = [r for r in rows if "loss" in r and "eval_loss" not in r and "step" in r]
        ev = [r for r in rows if "eval_loss" in r]
        axes[0].plot([r["step"] for r in tr], [r["loss"] for r in tr], color=c, lw=1.3)
        axes[1].plot([r["step"] for r in ev], [r["eval_loss"] for r in ev], "o-", color=c, ms=4)
        if ev and "eval_rewards/margins" in ev[0]:
            axes[2].plot([r["step"] for r in ev], [r["eval_rewards/margins"] for r in ev], "o-", color=c, ms=4)
        if "v3" in name and ev:  # the submitted LoRA checkpoint is v3's final step
            axes[1].scatter([ev[-1]["step"]], [ev[-1]["eval_loss"]], color=c, marker="*", s=170, zorder=5,
                            edgecolors="black", linewidths=0.6)
            axes[2].scatter([ev[-1]["step"]], [ev[-1]["eval_rewards/margins"]], color=c, marker="*", s=170,
                            zorder=5, edgecolors="black", linewidths=0.6)
    axes[0].axhline(np.log(2), color="gray", ls="--", lw=1)
    axes[1].axhline(np.log(2), color="gray", ls="--", lw=1)
    axes[1].set_yscale("log")
    for ax, ylabel in zip(axes, ("training DPO loss", "validation DPO loss (log scale)",
                                 "validation reward margin ($r_w - r_l$)")):
        ax.set_ylabel(ylabel)
        ax.set_xlabel("optimizer step (effective batch 16)")
        ax.grid(alpha=0.3)
    fig.legend(handles=run_handles + style_handles("training (raw)", "validation (20 pairs)",
                                                   "selected (v3, final)", log2_label="log 2"),
               loc="lower center", ncol=7, fontsize=10.5, frameon=False, bbox_to_anchor=(0.5, -0.01))
    fig.tight_layout(rect=(0, 0.08, 1, 1))
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
