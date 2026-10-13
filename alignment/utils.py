"""Small I/O, seeding, scheduling and checkpoint helpers shared by the scripts."""

from __future__ import annotations

import json
import math
import os
import random
import time
from pathlib import Path

import numpy as np
import torch


def set_seed(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)


def read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_json(path, obj) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def read_jsonl(path):
    rows = []
    if not os.path.exists(path):
        return rows
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def write_jsonl(path, rows) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


def append_jsonl(path, row) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
        f.flush()


def load_records(path):
    """Load a .json list or a .jsonl file."""

    return read_jsonl(path) if str(path).endswith(".jsonl") else read_json(path)


def warmup_cosine(step: int, total: int, warmup: int) -> float:
    """LR multiplier: linear warmup then cosine decay to 0."""

    if warmup > 0 and step < warmup:
        return (step + 1) / warmup
    progress = (step - warmup) / max(1, total - warmup)
    return 0.5 * (1.0 + math.cos(math.pi * min(1.0, progress)))


def save_checkpoint(model, tokenizer, out_dir, save_dtype: str = "fp32", extra: dict | None = None) -> None:
    """Save a HF checkpoint; optionally down-cast the saved weights to save disk."""

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    state_dict = None
    if save_dtype == "fp16":
        state_dict = {k: v.detach().to(torch.float16) for k, v in model.state_dict().items()}
    model.save_pretrained(out_dir, safe_serialization=True, state_dict=state_dict)
    tokenizer.save_pretrained(out_dir)
    if extra:
        write_json(out_dir / "checkpoint_info.json", extra)


class Timer:
    def __init__(self):
        self.start = time.time()

    def elapsed(self) -> float:
        return time.time() - self.start
