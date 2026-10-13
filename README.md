# Small-Model Alignment: SFT + DPO on Qwen2.5-0.5B

Post-training pipeline for the Advanced GenAI PA2 assignment (see `docs/assignment_manual.pdf`):

1. **SFT** of `Qwen/Qwen2.5-0.5B` on 30k UltraChat examples (own training loop).
2. **From-scratch DPO** (own loss and loop) on 15k HH-RLHF pairs and, separately, 15k UltraFeedback pairs.
3. **TRL `DPOTrainer` + LoRA** persona tuning of `HuggingFaceTB/SmolLM2-360M-Instruct` ("Idiot Sandwich").
4. Fixed-prompt generation, blinded pairwise files, and resumable LLM-as-judge evaluation via `kaggle_benchmarks`.

## Layout

```text
alignment/
  core.py            batching, response-only log-probs, SFT loss, DPO loss   <- graded math
  prompting.py       ChatML prompt template shared by SFT / DPO / generation
  training.py        model/optimizer/AMP helpers
  utils.py           I/O, seeding, LR schedule, checkpoint saving
submission_adapter.py  thin wrappers around alignment/core.py (grading interface)
scripts/
  prepare_data.py        download + filter + subsample all datasets (seed 42)
  train_sft.py           Task 3
  train_dpo.py           Task 4 (used for both HH-RLHF and UltraFeedback)
  train_lora_dpo.py      Task 5 (TRL DPOTrainer + PEFT LoRA)
  generate.py            Tasks 2 and 6 (greedy, resumable)
  build_pairs.py         blinded, A/B-randomised pairwise JSONL
  judge_pairwise_kaggle.py  Task 7 (batched, retrying, resumable judge)
  summarize_results.py   judge summaries -> Markdown table
  analyze_results.py     lengths, refusal rates, win/loss examples for error analysis
  plot_metrics.py        training curves for the report
notebooks/             Kaggle driver notebooks 01-06 (regenerate with make_notebooks.py)
eval_sets/             hh_rlhf_eval_300_seed42.json (provided), alpaca_eval_300_seed42.json,
                       persona_style_eval_24.json
tests/                 public smoke tests + own edge-case tests
report/                report source and figures
```

## Design choices that matter for grading

* **Prompt format** (SFT, DPO and evaluation): Qwen ChatML,
  `<|im_start|>system\nYou are a helpful assistant.<|im_end|>\n<|im_start|>user\n{instruction}<|im_end|>\n<|im_start|>assistant\n`,
  response followed by EOS. The tokenizer's EOS is set to `<|im_end|>`.
* **Batching**: prompt and response tokenized separately (`add_special_tokens=False`), EOS appended
  to the response *before* right-truncation to `max_length`, right-padded to the longest row.
  Masks are built from true lengths, so `pad_token == eos_token` tokenizers are handled correctly.
  `labels` are unshifted, `-100` on prompt and padding.
* **Log-probs**: summed over response tokens only (shifted next-token prediction), float32.
* **SFT loss reduction**: mean NLL over all unmasked response tokens in the batch.
* **DPO loss**: `mean(-logsigmoid(beta * ((pi_c - pi_r) - (ref_c - ref_r))))`; reference forward
  under `torch.no_grad()`.

## Running

Local checks (CPU, no downloads):

```bash
PYTHONPATH=. python -m pytest -q tests/
```

On Kaggle (GPU T4 x2) run the notebooks in order. Each one saves its outputs for the next one to attach as an input dataset:

| Notebook | Does | Approx. time |
|---|---|---|
| `01_setup_data_sft` | setup, tests, data prep, base generations (GPU1), SFT (GPU0) | ~2 h |
| `02_dpo_hh` | DPO on HH-RLHF (policy GPU0, frozen fp16 reference GPU1) | ~2.5 h |
| `03_dpo_uf` | DPO on UltraFeedback | ~2.5 h |
| `04_lora_persona` | TRL + LoRA persona DPO, persona generations and pairs | ~0.3 h |
| `05_generate_eval_outputs` | SFT / DPO generations, pairwise files | ~1 h |
| `06_judge_kaggle` | judging with `google/gemini-3.6-flash` (benchmark notebook) | ~0.5 h |

Notebooks 02, 03 and 04 are independent and can run at the same time in separate sessions.

Equivalent CLI:

```bash
python scripts/prepare_data.py
python scripts/train_sft.py --output_dir outputs/sft
python scripts/train_dpo.py --sft_path outputs/sft/best --train_path data/processed/hh_train.jsonl --val_path data/processed/hh_val.jsonl --output_dir outputs/dpo_hh
python scripts/train_dpo.py --sft_path outputs/sft/best --train_path data/processed/uf_train.jsonl --val_path data/processed/uf_val.jsonl --output_dir outputs/dpo_uf
python scripts/train_lora_dpo.py
python scripts/generate.py --model_path outputs/dpo_uf/best --eval_set eval_sets/alpaca_eval_300_seed42.json --generator_name qwen2.5-0.5b-dpo-uf --output_path outputs/generations/alpaca/dpo_uf.json
python scripts/build_pairs.py --model_path outputs/generations/alpaca/dpo_uf.json --baseline_path outputs/generations/alpaca/sft.json --output_path outputs/eval/uf_dpo_vs_sft_pairs.jsonl
python scripts/judge_pairwise_kaggle.py --pairs_path outputs/eval/uf_dpo_vs_sft_pairs.jsonl --output_path outputs/eval/uf_dpo_vs_sft_judged.jsonl --judge_model google/gemini-3.6-flash --batch_size 5 --sleep_seconds 5 --max_retries 3 --reuse_judgments
```

## Outputs (for reproducing the report tables)

```text
outputs/sft/{config.json, metrics.jsonl, best/, final/}
outputs/dpo_hh/{config.json, metrics.jsonl, best/, final/}
outputs/dpo_uf/{config.json, metrics.jsonl, best/, final/}
outputs/lora_dpo_persona/{config.json, metrics.jsonl, adapter/, split.json}
outputs/generations/{alpaca,hh,persona}/*.json
outputs/eval/*_pairs.jsonl, *_judged.jsonl, *_failures.jsonl, *_summary.json
```
