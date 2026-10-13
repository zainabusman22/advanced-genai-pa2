"""Training-loop helpers shared by the from-scratch SFT and DPO scripts."""

from __future__ import annotations

import contextlib

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer

from .prompting import configure_tokenizer


def autocast_ctx(device: torch.device, enabled: bool):
    if enabled and device.type == "cuda":
        return torch.autocast(device_type="cuda", dtype=torch.float16)
    return contextlib.nullcontext()


def load_tokenizer(path):
    return configure_tokenizer(AutoTokenizer.from_pretrained(path))


def load_model(path, device, dtype=torch.float32, gradient_checkpointing=False):
    kwargs = {"torch_dtype": dtype}
    if device.type == "cuda":
        kwargs["attn_implementation"] = "sdpa"
    model = AutoModelForCausalLM.from_pretrained(path, **kwargs).to(device)
    model.config.use_cache = False
    if gradient_checkpointing:
        model.gradient_checkpointing_enable()
    return model


def build_optimizer(model, name: str, lr: float, weight_decay: float):
    decay, no_decay = [], []
    for n, p in model.named_parameters():
        if not p.requires_grad:
            continue
        (no_decay if p.ndim < 2 or "norm" in n.lower() or n.endswith(".bias") else decay).append(p)
    groups = [
        {"params": decay, "weight_decay": weight_decay},
        {"params": no_decay, "weight_decay": 0.0},
    ]
    if name == "adamw":
        return torch.optim.AdamW(groups, lr=lr, betas=(0.9, 0.95), eps=1e-8)
    if name == "rmsprop":
        return torch.optim.RMSprop(groups, lr=lr)
    raise ValueError(f"unknown optimizer {name}")


def make_scaler(device: torch.device, enabled: bool):
    return torch.amp.GradScaler("cuda", enabled=enabled and device.type == "cuda")


def resolve_device(name: str) -> torch.device:
    if name == "auto":
        return torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
    return torch.device(name)
