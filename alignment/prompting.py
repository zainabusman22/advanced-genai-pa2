"""Prompt template shared by SFT, DPO, and evaluation.

We use Qwen's ChatML layout for every Qwen-based model (base, SFT, both DPO
runs) so that training and generation see exactly the same prompt string. The
response is the raw assistant text; the batch builders append the tokenizer's
EOS token after it.
"""

from __future__ import annotations

SYSTEM_PROMPT = "You are a helpful assistant."

CHATML_TEMPLATE = (
    "<|im_start|>system\n{system}<|im_end|>\n"
    "<|im_start|>user\n{instruction}<|im_end|>\n"
    "<|im_start|>assistant\n"
)

# Tokens that end an assistant turn during generation.
STOP_STRINGS = ("<|im_end|>", "<|endoftext|>")


def format_prompt(instruction: str, system: str = SYSTEM_PROMPT) -> str:
    return CHATML_TEMPLATE.format(system=system, instruction=instruction.strip())


def configure_tokenizer(tokenizer):
    """Use <|im_end|> as EOS so trained models end their turn the ChatML way.

    Qwen2.5 base ships with eos = pad = <|endoftext|>. Our batch builders never
    rely on pad != eos (masks come from lengths), so either choice is safe; we
    pick <|im_end|> because it matches the template above.
    """

    if "<|im_end|>" in tokenizer.get_vocab():
        tokenizer.eos_token = "<|im_end|>"
    if tokenizer.pad_token is None:
        tokenizer.pad_token = "<|endoftext|>" if "<|endoftext|>" in tokenizer.get_vocab() else tokenizer.eos_token
    return tokenizer
