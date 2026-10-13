"""Core SFT / DPO math: batching, response-only log-probs, and losses.

Conventions used everywhere in this module:

* Each row is ``prompt_ids + response_ids + [eos]`` (EOS only if the tokenizer
  defines one), truncated on the right to ``max_length``. Prompt and response
  are tokenized separately so the prompt/response boundary is exact.
* Rows are right-padded to the longest row in the batch. Masks are derived from
  the true row lengths, never from ``pad_token_id``, so tokenizers whose pad
  token equals EOS (e.g. Qwen2.5 base) or that have no pad token still work.
* ``labels`` are unshifted (Hugging Face convention): ``labels[i, t]`` equals
  ``input_ids[i, t]`` for response tokens and ``-100`` for prompt / padding.
  The shift happens inside :func:`sequence_logprobs`.
* SFT loss reduction: mean negative log-likelihood over all unmasked response
  tokens in the batch (token-level mean).
"""

from __future__ import annotations

from typing import Dict, Sequence

import torch
import torch.nn.functional as F

IGNORE_INDEX = -100


# --------------------------------------------------------------------------- #
# Batching
# --------------------------------------------------------------------------- #
def _encode(tokenizer, text: str):
    return list(tokenizer.encode(text, add_special_tokens=False))


def _pad_id(tokenizer) -> int:
    for candidate in (getattr(tokenizer, "pad_token_id", None), getattr(tokenizer, "eos_token_id", None)):
        if candidate is not None:
            return int(candidate)
    return 0


def build_lm_batch(tokenizer, prompts: Sequence[str], responses: Sequence[str], max_length: int) -> Dict[str, torch.Tensor]:
    """Tokenize prompt/response pairs into a right-padded, response-masked batch."""

    if len(prompts) != len(responses):
        raise ValueError("prompts and responses must have the same length")
    if max_length <= 0:
        raise ValueError("max_length must be positive")

    eos_id = getattr(tokenizer, "eos_token_id", None)
    rows, label_rows = [], []
    for prompt, response in zip(prompts, responses):
        prompt_ids = _encode(tokenizer, prompt)
        response_ids = _encode(tokenizer, response)
        if eos_id is not None:
            response_ids = response_ids + [int(eos_id)]
        ids = (prompt_ids + response_ids)[:max_length]
        n_prompt = min(len(prompt_ids), len(ids))
        labels = [IGNORE_INDEX] * n_prompt + ids[n_prompt:]
        rows.append(ids)
        label_rows.append(labels)

    width = max(1, max(len(r) for r in rows))
    pad_id = _pad_id(tokenizer)
    input_ids = torch.full((len(rows), width), pad_id, dtype=torch.long)
    attention_mask = torch.zeros((len(rows), width), dtype=torch.long)
    labels = torch.full((len(rows), width), IGNORE_INDEX, dtype=torch.long)
    for i, (ids, lab) in enumerate(zip(rows, label_rows)):
        n = len(ids)
        if n == 0:
            continue
        input_ids[i, :n] = torch.tensor(ids, dtype=torch.long)
        attention_mask[i, :n] = 1
        labels[i, :n] = torch.tensor(lab, dtype=torch.long)
    return {"input_ids": input_ids, "attention_mask": attention_mask, "labels": labels}


def build_preference_batch(tokenizer, prompts, chosen_responses, rejected_responses, max_length):
    if not (len(prompts) == len(chosen_responses) == len(rejected_responses)):
        raise ValueError("prompts, chosen_responses and rejected_responses must have the same length")
    return {
        "chosen": build_lm_batch(tokenizer, prompts, chosen_responses, max_length),
        "rejected": build_lm_batch(tokenizer, prompts, rejected_responses, max_length),
    }


# --------------------------------------------------------------------------- #
# Log-probabilities
# --------------------------------------------------------------------------- #
def model_device(model) -> torch.device:
    get_emb = getattr(model, "get_input_embeddings", None)
    if callable(get_emb):
        try:
            return get_emb().weight.device
        except Exception:  # pragma: no cover - exotic models
            pass
    return next(model.parameters()).device


def sequence_logprobs(model, batch: Dict[str, torch.Tensor], return_token_counts: bool = False):
    """Sum of log p(token) over response tokens, one value per row.

    Half-precision logits are upcast to float32; float32/float64 are kept as is.
    """

    device = model_device(model)
    input_ids = batch["input_ids"].to(device)
    attention_mask = batch["attention_mask"].to(device)
    labels = batch["labels"].to(device)

    outputs = model(input_ids=input_ids, attention_mask=attention_mask)
    logits = outputs.logits if hasattr(outputs, "logits") else outputs[0]
    logits = logits[:, :-1, :]
    if logits.dtype in (torch.float16, torch.bfloat16):
        logits = logits.float()  # never reduce over the vocab in half precision
    targets = labels[:, 1:]
    mask = targets.ne(IGNORE_INDEX)
    safe_targets = targets.masked_fill(~mask, 0)

    # log_softmax(x)[y] = x[y] - logsumexp(x); avoids materialising a second vocab-sized tensor.
    target_logits = torch.gather(logits, dim=-1, index=safe_targets.unsqueeze(-1)).squeeze(-1)
    token_logps = target_logits - torch.logsumexp(logits, dim=-1)
    token_logps = token_logps * mask
    summed = token_logps.sum(dim=-1)
    if return_token_counts:
        return summed, mask.sum(dim=-1)
    return summed


# --------------------------------------------------------------------------- #
# Losses
# --------------------------------------------------------------------------- #
def sft_loss_from_batch(model, batch):
    """Token-level mean NLL over unmasked response tokens in the batch."""

    logps, counts = sequence_logprobs(model, batch, return_token_counts=True)
    return -logps.sum() / counts.sum().clamp_min(1)


def dpo_loss_from_logps(policy_chosen_logps, policy_rejected_logps, reference_chosen_logps,
                        reference_rejected_logps, beta: float, return_metrics: bool = False):
    """Batch-mean DPO loss: -log sigmoid(beta * (Δ_policy - Δ_reference))."""

    def as_float(x):
        t = torch.as_tensor(x)
        return t if t.dtype in (torch.float32, torch.float64) else t.float()

    pc = as_float(policy_chosen_logps)
    device = pc.device
    pr = as_float(policy_rejected_logps).to(device)
    rc = as_float(reference_chosen_logps).detach().to(device)
    rr = as_float(reference_rejected_logps).detach().to(device)

    chosen_rewards = beta * (pc - rc)
    rejected_rewards = beta * (pr - rr)
    logits = chosen_rewards - rejected_rewards  # = beta * (Δθ - Δref)
    losses = -F.logsigmoid(logits)
    loss = losses.mean()
    if not return_metrics:
        return loss
    with torch.no_grad():
        metrics = {
            "loss": loss.detach(),
            "chosen_rewards": chosen_rewards.detach(),
            "rejected_rewards": rejected_rewards.detach(),
            "reward_margin": (chosen_rewards - rejected_rewards).detach(),
            "reward_correct": (chosen_rewards > rejected_rewards).float().detach(),
        }
    return loss, metrics


def dpo_loss_from_batch(policy_model, reference_model, batch, beta: float, return_metrics: bool = False):
    """Policy forward with grad, reference forward under ``torch.no_grad``."""

    policy_chosen = sequence_logprobs(policy_model, batch["chosen"])
    policy_rejected = sequence_logprobs(policy_model, batch["rejected"])
    with torch.no_grad():
        reference_chosen = sequence_logprobs(reference_model, batch["chosen"])
        reference_rejected = sequence_logprobs(reference_model, batch["rejected"])
    return dpo_loss_from_logps(policy_chosen, policy_rejected, reference_chosen, reference_rejected,
                               beta, return_metrics=return_metrics)
