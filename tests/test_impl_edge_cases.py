"""Extra edge-case checks for our own implementation (beyond the public smoke tests)."""

from __future__ import annotations

import math

import torch
import torch.nn.functional as F

import submission_adapter as adapter
from tests.toy_lm import ToyCausalLM, ToyTokenizer


class PadIsEosTokenizer(ToyTokenizer):
    pad_token = ToyTokenizer.eos_token
    pad_token_id = ToyTokenizer.eos_token_id


class NoPadTokenizer(ToyTokenizer):
    pad_token = "<unused-pad>"  # string kept only so ToyTokenizer.encode still works
    pad_token_id = None


class NoEosTokenizer(ToyTokenizer):
    eos_token = "<unused-eos>"  # string kept only so ToyTokenizer.encode still works
    eos_token_id = None


def manual_logp(model, tokenizer, prompt, response, max_length):
    ids = tokenizer.encode(prompt) + tokenizer.encode(response)
    if tokenizer.eos_token_id is not None:
        ids = ids + [tokenizer.eos_token_id]
    ids = ids[:max_length]
    n_prompt = len(tokenizer.encode(prompt))
    logprobs = F.log_softmax(model.logit_bias.detach(), dim=-1)
    return sum(logprobs[t].item() for t in ids[max(n_prompt, 1):])


def test_truncation_can_drop_eos_and_keeps_response_prefix():
    tok = ToyTokenizer()
    # prompt 3 tokens + response 5 tokens + eos = 9; truncate to 6
    batch = adapter.build_sft_batch(tok, ["ab:", "x:"], ["hello", "hi"], max_length=6)
    assert batch["input_ids"].shape[1] <= 6
    row0 = batch["labels"][0]
    assert row0[:3].eq(-100).all()
    assert row0[3:6].tolist() == tok.encode("hel")
    assert tok.eos_token_id not in row0.tolist()
    # second row fits: "x:" + "hi" + eos
    row1 = batch["labels"][1]
    assert row1[2:5].tolist() == tok.encode("hi") + [tok.eos_token_id]
    assert batch["attention_mask"][1].tolist() == [1, 1, 1, 1, 1, 0]


def test_prompt_longer_than_max_length_has_no_response_tokens():
    tok = ToyTokenizer()
    model = ToyCausalLM(tok.vocab_size, bias_scale=0.5)
    batch = adapter.build_sft_batch(tok, ["abcdefgh"], ["yes"], max_length=4)
    assert batch["labels"].eq(-100).all()
    logps = adapter.compute_response_logprobs(model, tok, ["abcdefgh"], ["yes"], max_length=4)
    assert logps.item() == 0.0


def test_pad_equal_to_eos_keeps_eos_label():
    tok = PadIsEosTokenizer()
    batch = adapter.build_sft_batch(tok, ["Q: ", "Longer q: "], ["a", "b"], max_length=32)
    for row, prompt in enumerate(["Q: ", "Longer q: "]):
        n = len(tok.encode(prompt))
        assert batch["labels"][row, n + 1] == tok.eos_token_id
        assert batch["attention_mask"][row, : n + 2].eq(1).all()
    # padding of the short row is masked even though its id equals eos
    assert batch["attention_mask"][0, 5:].eq(0).all()
    assert batch["labels"][0, 5:].eq(-100).all()


def test_missing_pad_and_missing_eos_tokenizers():
    batch = adapter.build_sft_batch(NoPadTokenizer(), ["a", "bbb"], ["c", "d"], max_length=10)
    assert batch["input_ids"].shape == (2, 5)
    tok = NoEosTokenizer()
    batch = adapter.build_sft_batch(tok, ["a: "], ["xy"], max_length=10)
    assert batch["labels"][0].ne(-100).sum() == 2


def test_logprobs_match_manual_and_preserve_row_order():
    tok = ToyTokenizer()
    model = ToyCausalLM(tok.vocab_size, bias_scale=1.0)
    prompts = ["Task: ", "Q: ", "Longer prompt here: "]
    responses = ["abc", "zz", "x"]
    logps = adapter.compute_response_logprobs(model, tok, prompts, responses, max_length=12)
    expected = [manual_logp(model, tok, p, r, 12) for p, r in zip(prompts, responses)]
    torch.testing.assert_close(logps, torch.tensor(expected), rtol=1e-5, atol=1e-5)
    reversed_logps = adapter.compute_response_logprobs(model, tok, prompts[::-1], responses[::-1], max_length=12)
    torch.testing.assert_close(reversed_logps, logps.flip(0))


def test_sft_loss_is_token_mean():
    tok = ToyTokenizer()
    model = ToyCausalLM(tok.vocab_size, bias_scale=1.0)
    prompts, responses = ["A: ", "B: "], ["a", "abcdef"]
    loss = adapter.compute_sft_loss(model, tok, prompts, responses, max_length=32)
    total = sum(manual_logp(model, tok, p, r, 32) for p, r in zip(prompts, responses))
    n_tokens = (1 + 1) + (6 + 1)
    assert math.isclose(loss.item(), -total / n_tokens, rel_tol=1e-5)


def test_dpo_loss_from_logps_matches_formula():
    torch.manual_seed(0)
    pc, pr, rc, rr = (torch.randn(7) * 5 for _ in range(4))
    beta = 0.3
    loss = adapter.compute_dpo_loss_from_logps(pc, pr, rc, rr, beta)
    logits = beta * ((pc - pr) - (rc - rr))
    expected = torch.mean(-torch.log(torch.sigmoid(logits)))
    torch.testing.assert_close(loss, expected, rtol=1e-5, atol=1e-6)
    # numerically stable for large margins
    big = adapter.compute_dpo_loss_from_logps(torch.tensor([0.0]), torch.tensor([1e4]),
                                              torch.tensor([0.0]), torch.tensor([0.0]), 1.0)
    assert torch.isfinite(big) and big.item() > 1e3


def test_dpo_step_increases_chosen_preference_and_reference_untouched():
    tok = ToyTokenizer()
    policy = ToyCausalLM(tok.vocab_size)
    reference = ToyCausalLM(tok.vocab_size)
    ref_before = reference.logit_bias.detach().clone()
    args = (tok, ["Pick: ", "Choose: "], ["a", "a"], ["b", "b"])
    opt = torch.optim.SGD(policy.parameters(), lr=1.0)
    loss0 = adapter.compute_dpo_loss(policy, reference, *args, beta=0.5, max_length=16)
    torch.testing.assert_close(loss0, torch.log(torch.tensor(2.0)))
    loss0.backward()
    opt.step()
    opt.zero_grad()
    loss1 = adapter.compute_dpo_loss(policy, reference, *args, beta=0.5, max_length=16)
    assert loss1.item() < loss0.item()
    assert policy.logit_bias[tok.vocab["a"]] > policy.logit_bias[tok.vocab["b"]]
    assert torch.equal(reference.logit_bias.detach(), ref_before)
    assert reference.logit_bias.grad is None


def test_dpo_batch_rows_align_with_inputs():
    tok = ToyTokenizer()
    batch = adapter.build_dpo_batch(tok, ["p1: ", "prompt2: "], ["c1", "chosen2"], ["r", "rej2"], max_length=20)
    for side, resp in (("chosen", ["c1", "chosen2"]), ("rejected", ["r", "rej2"])):
        for row, (p, r) in enumerate(zip(["p1: ", "prompt2: "], resp)):
            n = len(tok.encode(p))
            labels = batch[side]["labels"][row]
            assert labels[n:n + len(r) + 1].tolist() == tok.encode(r) + [tok.eos_token_id]
