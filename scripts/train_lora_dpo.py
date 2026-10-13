"""Task 5 ("Idiot Sandwich"): TRL DPOTrainer + PEFT/LoRA persona tuning.

Model:   HuggingFaceTB/SmolLM2-360M-Instruct
Data:    data/persona_lora/ramsay_style_preferences.json (prompt / chosen / rejected)

Rows are converted to TRL's conversational preference format:
  prompt   = [{"role": "user", "content": ...}]
  chosen   = [{"role": "assistant", "content": ...}]
  rejected = [{"role": "assistant", "content": ...}]
With a PEFT config and ``ref_model=None``, DPOTrainer uses the frozen base
weights (adapter disabled) as the reference policy.

LoRA choices (see report): rank 16, alpha 32, dropout 0.05 on all attention and
MLP projections (q,k,v,o,gate,up,down). Style is a broad, low-rank behaviour
change touching both "what to say" (MLP) and "how to attend" (attention), and
the dataset is tiny (~200 train pairs), so a moderate rank with all linear
targets gives enough capacity without the risk of a large-rank overfit.
"""

from __future__ import annotations

import argparse
import inspect
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from alignment.utils import read_json, set_seed, write_json  # noqa: E402


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--model_name", default="HuggingFaceTB/SmolLM2-360M-Instruct")
    p.add_argument("--data_path", default=str(ROOT / "data/persona_lora/ramsay_style_preferences.json"))
    p.add_argument("--output_dir", default=str(ROOT / "outputs/lora_dpo_persona"))
    p.add_argument("--val_size", type=int, default=20)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--lora_r", type=int, default=16)
    p.add_argument("--lora_alpha", type=int, default=32)
    p.add_argument("--lora_dropout", type=float, default=0.05)
    p.add_argument("--target_modules", nargs="+",
                   default=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"])
    p.add_argument("--lr", type=float, default=5e-5)
    p.add_argument("--beta", type=float, default=0.1)
    p.add_argument("--epochs", type=float, default=3)
    p.add_argument("--batch_size", type=int, default=4)
    p.add_argument("--grad_accum", type=int, default=4)
    p.add_argument("--max_length", type=int, default=512)
    p.add_argument("--max_prompt_length", type=int, default=256)
    p.add_argument("--warmup_ratio", type=float, default=0.1)
    return p.parse_args()


def to_conversational(row):
    return {
        "prompt": [{"role": "user", "content": row["prompt"]}],
        "chosen": [{"role": "assistant", "content": row["chosen"]}],
        "rejected": [{"role": "assistant", "content": row["rejected"]}],
    }


def filtered_kwargs(cls, **kwargs):
    """Drop kwargs that this TRL version's config does not accept."""

    params = inspect.signature(cls.__init__).parameters
    accepted = {k: v for k, v in kwargs.items() if k in params}
    dropped = sorted(set(kwargs) - set(accepted))
    if dropped:
        print(f"[{cls.__name__}] ignoring unsupported args: {dropped}")
    return accepted


def main():
    args = parse_args()
    set_seed(args.seed)

    import torch
    from datasets import Dataset
    from peft import LoraConfig
    from transformers import AutoModelForCausalLM, AutoTokenizer
    from trl import DPOConfig, DPOTrainer

    rows = read_json(args.data_path)
    order = list(range(len(rows)))
    random.Random(args.seed).shuffle(order)
    val_idx, train_idx = order[: args.val_size], order[args.val_size:]
    train_ds = Dataset.from_list([to_conversational(rows[i]) for i in train_idx])
    val_ds = Dataset.from_list([to_conversational(rows[i]) for i in val_idx])
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    write_json(out / "split.json", {"train_indices": train_idx, "val_indices": val_idx})

    tokenizer = AutoTokenizer.from_pretrained(args.model_name)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    use_cuda = torch.cuda.is_available()
    model = AutoModelForCausalLM.from_pretrained(args.model_name, torch_dtype=torch.float32)

    peft_config = LoraConfig(r=args.lora_r, lora_alpha=args.lora_alpha, lora_dropout=args.lora_dropout,
                             target_modules=args.target_modules, bias="none", task_type="CAUSAL_LM")

    config_kwargs = dict(
        output_dir=str(out / "trainer"),
        beta=args.beta,
        learning_rate=args.lr,
        lr_scheduler_type="cosine",
        warmup_ratio=args.warmup_ratio,
        num_train_epochs=args.epochs,
        per_device_train_batch_size=args.batch_size,
        per_device_eval_batch_size=args.batch_size,
        gradient_accumulation_steps=args.grad_accum,
        max_length=args.max_length,
        max_prompt_length=args.max_prompt_length,
        fp16=use_cuda,
        bf16=False,  # recent TRL defaults to bf16, which T4 GPUs do not support
        use_cpu=not use_cuda,
        logging_steps=2,
        eval_strategy="steps",
        eval_steps=5,
        save_strategy="no",
        report_to="none",
        seed=args.seed,
        remove_unused_columns=False,
    )
    dpo_args = DPOConfig(**filtered_kwargs(DPOConfig, **config_kwargs))

    trainer_kwargs = dict(model=model, ref_model=None, args=dpo_args, train_dataset=train_ds,
                          eval_dataset=val_ds, peft_config=peft_config)
    trainer_params = inspect.signature(DPOTrainer.__init__).parameters
    trainer_kwargs["processing_class" if "processing_class" in trainer_params else "tokenizer"] = tokenizer
    trainer = DPOTrainer(**trainer_kwargs)
    trainer.model.print_trainable_parameters()
    n_trainable = sum(p.numel() for p in trainer.model.parameters() if p.requires_grad)
    n_total = sum(p.numel() for p in trainer.model.parameters())

    trainer.train()
    final_eval = trainer.evaluate()
    adapter_dir = out / "adapter"
    trainer.model.save_pretrained(adapter_dir)
    tokenizer.save_pretrained(adapter_dir)

    history = trainer.state.log_history
    with open(out / "metrics.jsonl", "w", encoding="utf-8") as f:
        import json

        for rec in history:
            f.write(json.dumps(rec) + "\n")
    import trl

    write_json(out / "config.json", {**vars(args), "n_train": len(train_ds), "n_val": len(val_ds),
                                      "trl_version": trl.__version__, "final_eval": final_eval,
                                      "fp16": use_cuda,
                                      "trainable_params": n_trainable, "total_params": n_total,
                                      "optimizer_steps": trainer.state.global_step})
    print("final eval:", final_eval)


if __name__ == "__main__":
    main()
