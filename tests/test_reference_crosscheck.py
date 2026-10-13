"""Cross-checks against independent reference implementations.

1. Randomised tests (hundreds of cases) comparing the adapter to a naive,
   loop-based reference, using a *context-dependent* toy LM so that shift /
   off-by-one and padding-leak bugs are detectable (the public ToyCausalLM is
   position-independent and cannot catch them).
2. Real Qwen2.5 tokenizer + tiny random Qwen2 model: our SFT loss and its
   gradients must equal Hugging Face's built-in causal-LM loss, and batched
   log-probs must equal per-row unpadded computation.
"""

from __future__ import annotations

import random

import pytest
import torch
import torch.nn.functional as F
from torch import nn

import submission_adapter as adapter
from tests.toy_lm import ToyTokenizer

CHARS = "abcdefghijklmnopqrstuvwxyzABC 012.,!?:"


# --------------------------------------------------------------------------- #
# Tokenizer variants and a context-dependent toy model
# --------------------------------------------------------------------------- #
class PadIsEos(ToyTokenizer):
    pad_token, pad_token_id = ToyTokenizer.eos_token, ToyTokenizer.eos_token_id


class NoPad(ToyTokenizer):
    pad_token, pad_token_id = "<unused-pad>", None


class NoEos(ToyTokenizer):
    eos_token, eos_token_id = "<unused-eos>", None


class LeftPadding(ToyTokenizer):
    padding_side = "left"


TOKENIZERS = [ToyTokenizer, PadIsEos, NoPad, NoEos, LeftPadding]


class ContextLM(nn.Module):
    """logits[t] = W[x_t] + U @ mean(E[x_<=t]) + b  (causal, depends on position and context)."""

    def __init__(self, vocab, dim=8, seed=0):
        super().__init__()
        g = torch.Generator().manual_seed(seed)
        self.W = nn.Parameter(torch.randn(vocab, vocab, generator=g))
        self.E = nn.Parameter(torch.randn(vocab, dim, generator=g))
        self.U = nn.Parameter(torch.randn(dim, vocab, generator=g))
        self.b = nn.Parameter(torch.randn(vocab, generator=g))

    def forward(self, input_ids, attention_mask=None, **_):
        emb = self.E[input_ids]
        steps = torch.arange(1, input_ids.shape[1] + 1, device=input_ids.device).view(1, -1, 1)
        ctx = emb.cumsum(dim=1) / steps
        logits = self.W[input_ids] + ctx @ self.U + self.b
        return type("Out", (), {"logits": logits})()


class TupleOutputLM(ContextLM):
    def forward(self, input_ids, attention_mask=None, **_):
        return (super().forward(input_ids, attention_mask).logits,)


# --------------------------------------------------------------------------- #
# Naive reference implementation (independent of alignment/core.py)
# --------------------------------------------------------------------------- #
def ref_ids_and_prompt_len(tok, prompt, response, max_length):
    p = tok.encode(prompt, add_special_tokens=False)
    r = tok.encode(response, add_special_tokens=False)
    if tok.eos_token_id is not None:
        r = r + [tok.eos_token_id]
    return (p + r)[:max_length], len(p)


def ref_row_logp(model, tok, prompt, response, max_length):
    ids, n_prompt = ref_ids_and_prompt_len(tok, prompt, response, max_length)
    if len(ids) < 2:
        return 0.0, 0
    with torch.no_grad():
        logits = model(torch.tensor([ids])).logits[0].double()
    logp = F.log_softmax(logits, dim=-1)
    total, n = 0.0, 0
    for t in range(max(n_prompt, 1), len(ids)):
        total += logp[t - 1, ids[t]].item()
        n += 1
    return total, n


def rand_text(rng, lo, hi):
    return "".join(rng.choice(CHARS) for _ in range(rng.randint(lo, hi)))


def rand_case(rng):
    n = rng.randint(1, 5)
    prompts = [rand_text(rng, 0, 10) for _ in range(n)]
    responses = [rand_text(rng, 0, 12) for _ in range(n)]
    return prompts, responses, rng.randint(1, 25)


# --------------------------------------------------------------------------- #
# 1. Randomised toy cross-checks
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("tok_cls", TOKENIZERS, ids=lambda c: c.__name__)
def test_random_sft_batches_match_reference(tok_cls):
    rng = random.Random(1)
    tok = tok_cls()
    for _ in range(150):
        prompts, responses, max_length = rand_case(rng)
        batch = adapter.build_sft_batch(tok, prompts, responses, max_length)
        assert set(batch) == {"input_ids", "attention_mask", "labels"}
        assert all(v.dtype == torch.long for v in batch.values())
        width = batch["input_ids"].shape[1]
        assert width <= max_length
        for i, (p, r) in enumerate(zip(prompts, responses)):
            ids, n_prompt = ref_ids_and_prompt_len(tok, p, r, max_length)
            n = len(ids)
            assert batch["input_ids"][i, :n].tolist() == ids
            assert batch["attention_mask"][i].tolist() == [1] * n + [0] * (width - n)
            expected_labels = [-100] * min(n_prompt, n) + ids[min(n_prompt, n):] + [-100] * (width - n)
            assert batch["labels"][i].tolist() == expected_labels


@pytest.mark.parametrize("tok_cls", TOKENIZERS, ids=lambda c: c.__name__)
def test_random_logprobs_and_sft_loss_match_reference(tok_cls):
    rng = random.Random(2)
    tok = tok_cls()
    model = ContextLM(tok.vocab_size)
    for _ in range(100):
        prompts, responses, max_length = rand_case(rng)
        logps = adapter.compute_response_logprobs(model, tok, prompts, responses, max_length)
        refs = [ref_row_logp(model, tok, p, r, max_length) for p, r in zip(prompts, responses)]
        assert logps.shape == (len(prompts),)
        torch.testing.assert_close(logps.double(), torch.tensor([v for v, _ in refs], dtype=torch.double),
                                   rtol=1e-4, atol=1e-4)
        loss = adapter.compute_sft_loss(model, tok, prompts, responses, max_length)
        assert loss.ndim == 0
        n_tok = sum(n for _, n in refs)
        expected = -sum(v for v, _ in refs) / max(1, n_tok)
        assert abs(loss.item() - expected) < 1e-4 * max(1.0, abs(expected))


def test_random_dpo_loss_matches_reference():
    rng = random.Random(3)
    tok = ToyTokenizer()
    policy, reference = ContextLM(tok.vocab_size, seed=1), ContextLM(tok.vocab_size, seed=2)
    for _ in range(60):
        n = rng.randint(1, 4)
        prompts = [rand_text(rng, 0, 8) for _ in range(n)]
        chosen = [rand_text(rng, 0, 10) for _ in range(n)]
        rejected = [rand_text(rng, 0, 10) for _ in range(n)]
        max_length, beta = rng.randint(2, 22), rng.choice([0.01, 0.1, 0.5, 2.0])
        loss = adapter.compute_dpo_loss(policy, reference, tok, prompts, chosen, rejected, beta, max_length)
        terms = []
        for p, c, r in zip(prompts, chosen, rejected):
            pc, _ = ref_row_logp(policy, tok, p, c, max_length)
            pr, _ = ref_row_logp(policy, tok, p, r, max_length)
            rc, _ = ref_row_logp(reference, tok, p, c, max_length)
            rr, _ = ref_row_logp(reference, tok, p, r, max_length)
            z = beta * ((pc - pr) - (rc - rr))
            terms.append(-F.logsigmoid(torch.tensor(z, dtype=torch.double)).item())
        assert loss.ndim == 0
        assert abs(loss.item() - sum(terms) / n) < 1e-4

        batch = adapter.build_dpo_batch(tok, prompts, chosen, rejected, max_length)
        for side, resp in (("chosen", chosen), ("rejected", rejected)):
            single = adapter.build_sft_batch(tok, prompts, resp, max_length)
            for key in ("input_ids", "attention_mask", "labels"):
                assert torch.equal(batch[side][key], single[key])


def test_dpo_from_logps_value_gradient_and_input_types():
    torch.manual_seed(0)
    pc, pr = torch.randn(6, requires_grad=True), torch.randn(6, requires_grad=True)
    rc, rr = torch.randn(6), torch.randn(6)
    beta = 0.25
    loss = adapter.compute_dpo_loss_from_logps(pc, pr, rc, rr, beta)
    loss.backward()
    z = beta * ((pc - pr) - (rc - rr)).detach()
    expected_grad = -beta * torch.sigmoid(-z) / 6  # d loss / d policy_chosen_logp
    torch.testing.assert_close(pc.grad, expected_grad)
    torch.testing.assert_close(pr.grad, -expected_grad)

    # beta as a tensor, float64 inputs, single-element batch, extreme margins
    v = torch.tensor([-3.0], dtype=torch.float64)
    out = adapter.compute_dpo_loss_from_logps(v, v, v, v, torch.tensor(0.1))
    torch.testing.assert_close(out.double(), torch.log(torch.tensor(2.0, dtype=torch.float64)), rtol=1e-6, atol=1e-6)
    huge = adapter.compute_dpo_loss_from_logps(torch.tensor([1e6]), torch.tensor([0.0]),
                                               torch.tensor([0.0]), torch.tensor([0.0]), 1.0)
    assert huge.item() == pytest.approx(0.0, abs=1e-6)
    tiny = adapter.compute_dpo_loss_from_logps(torch.tensor([0.0]), torch.tensor([1e6]),
                                               torch.tensor([0.0]), torch.tensor([0.0]), 1.0)
    assert torch.isfinite(tiny) and tiny.item() == pytest.approx(1e6, rel=1e-6)
    # plain Python lists
    lst = adapter.compute_dpo_loss_from_logps([-1.0, -2.0], [-2.0, -1.0], [-1.0, -1.0], [-1.0, -1.0], 1.0)
    expected = (-F.logsigmoid(torch.tensor(1.0)) - F.logsigmoid(torch.tensor(-1.0))) / 2
    torch.testing.assert_close(lst, expected)


def test_reference_model_is_never_differentiated_even_if_trainable():
    tok = ToyTokenizer()
    policy, reference = ContextLM(tok.vocab_size, seed=1), ContextLM(tok.vocab_size, seed=2)
    assert all(p.requires_grad for p in reference.parameters())
    loss = adapter.compute_dpo_loss(policy, reference, tok, ["Q: ", "x"], ["ab", "c"], ["ba", "dd"], 0.1, 16)
    loss.backward()
    assert all(p.grad is None for p in reference.parameters())
    assert all(p.grad is not None for p in policy.parameters())


def test_padding_and_row_order_invariance_with_context_model():
    tok = ToyTokenizer()
    model = ContextLM(tok.vocab_size)
    prompts = ["a", "long prompt here: ", "", "mid: "]
    responses = ["x", "short", "the longest response of all", ""]
    batched = adapter.compute_response_logprobs(model, tok, prompts, responses, 64)
    for i in range(4):
        alone = adapter.compute_response_logprobs(model, tok, [prompts[i]], [responses[i]], 64)
        torch.testing.assert_close(batched[i], alone[0], rtol=1e-5, atol=1e-5)
    perm = [2, 0, 3, 1]
    permuted = adapter.compute_response_logprobs(model, tok, [prompts[j] for j in perm],
                                                 [responses[j] for j in perm], 64)
    torch.testing.assert_close(permuted, batched[perm])


def test_dtypes_are_preserved():
    tok = ToyTokenizer()
    m32, m64 = ContextLM(tok.vocab_size), ContextLM(tok.vocab_size).double()
    assert adapter.compute_response_logprobs(m32, tok, ["a"], ["b"], 8).dtype == torch.float32
    assert adapter.compute_response_logprobs(m64, tok, ["a"], ["b"], 8).dtype == torch.float64
    assert adapter.compute_sft_loss(m64, tok, ["a"], ["b"], 8).dtype == torch.float64
    v64 = torch.tensor([-1.0, -2.0], dtype=torch.float64)
    assert adapter.compute_dpo_loss_from_logps(v64, v64, v64, v64, 0.1).dtype == torch.float64
    v16 = torch.tensor([-1.0, -2.0], dtype=torch.float16)
    assert adapter.compute_dpo_loss_from_logps(v16, v16, v16, v16, 0.1).dtype == torch.float32
    batch = adapter.build_sft_batch(tok, ["a"], ["b"], 8)
    assert all(t.dtype == torch.long for t in batch.values())


def test_tuple_inputs_and_tuple_model_outputs():
    tok = ToyTokenizer()
    model = TupleOutputLM(tok.vocab_size)
    out = adapter.compute_response_logprobs(model, tok, ("ab", "c"), ("x", "yz"), 10)
    ref = ContextLM(tok.vocab_size)
    torch.testing.assert_close(out, adapter.compute_response_logprobs(ref, tok, ["ab", "c"], ["x", "yz"], 10))


def test_sft_gradient_matches_finite_difference():
    tok = ToyTokenizer()
    model = ContextLM(tok.vocab_size).double()
    prompts, responses = ["Q: ", "Task: "], ["hello", "ok"]
    loss = adapter.compute_sft_loss(model, tok, prompts, responses, 32)
    loss.backward()
    eps = 1e-6
    for idx in [(tok.vocab["h"],), (tok.vocab["o"],), (tok.eos_token_id,)]:
        with torch.no_grad():
            model.b[idx] += eps
            up = adapter.compute_sft_loss(model, tok, prompts, responses, 32).item()
            model.b[idx] -= 2 * eps
            down = adapter.compute_sft_loss(model, tok, prompts, responses, 32).item()
            model.b[idx] += eps
        assert model.b.grad[idx].item() == pytest.approx((up - down) / (2 * eps), rel=1e-3, abs=1e-6)


# --------------------------------------------------------------------------- #
# 2. Real Qwen2.5 tokenizer + tiny Qwen2 model vs Hugging Face's own loss
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def qwen():
    transformers = pytest.importorskip("transformers")
    try:
        tok = transformers.AutoTokenizer.from_pretrained("Qwen/Qwen2.5-0.5B", local_files_only=True)
    except Exception:
        try:
            tok = transformers.AutoTokenizer.from_pretrained("Qwen/Qwen2.5-0.5B")
        except Exception:
            pytest.skip("Qwen tokenizer not available offline")
    cfg = transformers.Qwen2Config(vocab_size=len(tok), hidden_size=32, intermediate_size=64, num_hidden_layers=2,
                                   num_attention_heads=4, num_key_value_heads=2, max_position_embeddings=512,
                                   tie_word_embeddings=True)
    torch.manual_seed(0)
    model = transformers.Qwen2ForCausalLM(cfg).double().eval()
    return tok, model


QWEN_PROMPTS = [
    "<|im_start|>user\nWhat is 2+2?<|im_end|>\n<|im_start|>assistant\n",
    "<|im_start|>system\nYou are a helpful assistant.<|im_end|>\n<|im_start|>user\nTranslate 'café' → English 🙂<|im_end|>\n<|im_start|>assistant\n",
    "Plain prompt without template:",
]
QWEN_RESPONSES = ["4", "Coffee shop. ☕ Here's a longer answer with   spaces\nand newlines.", ""]


def test_qwen_sft_loss_and_grads_equal_hf_builtin_loss(qwen):
    tok, model = qwen
    for max_length in (512, 40, 20):
        batch = adapter.build_sft_batch(tok, QWEN_PROMPTS, QWEN_RESPONSES, max_length)
        model.zero_grad()
        hf_loss = model(input_ids=batch["input_ids"], attention_mask=batch["attention_mask"],
                        labels=batch["labels"]).loss
        hf_loss.backward()
        hf_grad = model.model.embed_tokens.weight.grad.clone()
        model.zero_grad()
        ours = adapter.compute_sft_loss(model, tok, QWEN_PROMPTS, QWEN_RESPONSES, max_length)
        ours.backward()
        torch.testing.assert_close(ours.double(), hf_loss.double(), rtol=1e-6, atol=1e-6)
        torch.testing.assert_close(model.model.embed_tokens.weight.grad, hf_grad, rtol=1e-5, atol=1e-8)


def test_qwen_eos_padding_truncation_and_batch_invariance(qwen):
    tok, model = qwen
    assert tok.pad_token_id == tok.eos_token_id  # Qwen2.5 base: pad == eos, the tricky case
    batch = adapter.build_sft_batch(tok, QWEN_PROMPTS, QWEN_RESPONSES, 512)
    for i, (p, r) in enumerate(zip(QWEN_PROMPTS, QWEN_RESPONSES)):
        n_p, n_r = len(tok.encode(p, add_special_tokens=False)), len(tok.encode(r, add_special_tokens=False))
        assert batch["labels"][i, n_p + n_r] == tok.eos_token_id
        assert batch["labels"][i, :n_p].eq(-100).all()
        assert batch["labels"][i, n_p + n_r + 1:].eq(-100).all()
        assert batch["attention_mask"][i].sum() == n_p + n_r + 1

    old_side = tok.padding_side
    tok.padding_side = "left"  # must not affect our manual right padding
    try:
        batched = adapter.compute_response_logprobs(model, tok, QWEN_PROMPTS, QWEN_RESPONSES, 512)
    finally:
        tok.padding_side = old_side
    for i in range(len(QWEN_PROMPTS)):
        alone = adapter.compute_response_logprobs(model, tok, [QWEN_PROMPTS[i]], [QWEN_RESPONSES[i]], 512)
        torch.testing.assert_close(batched[i], alone[0], rtol=1e-6, atol=1e-6)

    # truncation that removes EOS
    p, r = QWEN_PROMPTS[1], QWEN_RESPONSES[1]
    n_p = len(tok.encode(p, add_special_tokens=False))
    cut = adapter.build_sft_batch(tok, [p], [r], n_p + 3)
    assert cut["input_ids"].shape[1] == n_p + 3
    assert cut["labels"][0, n_p:].tolist() == tok.encode(r, add_special_tokens=False)[:3]
