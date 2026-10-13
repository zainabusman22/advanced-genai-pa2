# Instruction Tuning and DPO on Qwen2.5-0.5B: Final Report

> Draft skeleton. Every `TBD` comes from the Kaggle runs: `outputs/*/config.json`, `outputs/*/summary.json`,
> `outputs/eval/*_summary.json` and `report/results_table.md`. Keep the final PDF to 8 pages or fewer.

## 1. Repository, models, and data

* Repository: `TBD (GitHub URL)`
* Base model: `Qwen/Qwen2.5-0.5B`. Persona model: `HuggingFaceTB/SmolLM2-360M-Instruct`.
* Global seed: 42 (data subsampling, DataLoader shuffling, AlpacaEval subset, A/B randomisation).

| Track | Source | Train | Val | Filtering |
|---|---|---|---|---|
| SFT | `HuggingFaceH4/ultrachat_200k` (`train_sft` / `test_sft`) | 30,000 | 1,000 | first user→assistant turn only; templated prompt ≤ 256 tokens |
| HH-RLHF DPO | `Anthropic/hh-rlhf` (`train` / `test`) | 15,000 | 1,000 | single-turn only; identical prompt in chosen/rejected; chosen ≠ rejected; prompt ≤ 256 tokens |
| UltraFeedback DPO | `HuggingFaceH4/ultrafeedback_binarized` (`train_prefs` / `test_prefs`) | 15,000 | 1,000 | 2-message conversations; no score ties; chosen ≠ rejected; prompt ≤ 256 tokens |
| LoRA persona DPO | `data/persona_lora/ramsay_style_preferences.json` | 200 | 20 | random split, seed 42 (`outputs/lora_dpo_persona/split.json`) |

Evaluation sets: `eval_sets/hh_rlhf_eval_300_seed42.json` (provided, 300), `eval_sets/alpaca_eval_300_seed42.json`
(300 prompts of `tatsu-lab/alpaca_eval` sampled with `random.Random(42).sample`), and `eval_sets/persona_style_eval_24.json`
(24 prompts: 6 cooking, 6 coding, 6 general help, 6 safety-sensitive).

Prompt template (SFT, DPO, generation): Qwen ChatML with system prompt "You are a helpful assistant.". The response
is followed by `<|im_end|>` (used as EOS). The persona model uses SmolLM2's own chat template via TRL.

## 2. Experimental configuration

| | SFT | HH-RLHF DPO | UltraFeedback DPO | LoRA-DPO (persona) |
|---|---|---|---|---|
| Init / reference | Qwen2.5-0.5B / – | SFT `best` / frozen SFT `best` (fp16, cuda:1) | SFT `best` / frozen SFT `best` (fp16, cuda:1) | SmolLM2-360M-Instruct / same base, adapter disabled |
| Trainable params | full FT (494M) | full FT | full FT | LoRA r=16, α=32, dropout 0.05, q/k/v/o/gate/up/down |
| Optimizer | AdamW (0.9, 0.95) | AdamW | AdamW | AdamW (TRL default) |
| Peak LR / schedule | 2e-5, 3% warmup + cosine | 1e-6 constant | 1e-6 constant | 5e-5, 10% warmup + cosine |
| Weight decay / clip | 0.1 / 1.0 | 0 / 1.0 | 0 / 1.0 | 0 / 1.0 |
| Batch × accum = eff. | 2 × 16 = 32 | 1 × 32 = 32 | 1 × 32 = 32 | 4 × 4 = 16 |
| Precision | fp32 weights, fp16 autocast, GradScaler | same | same | fp32 weights, fp16 AMP |
| Max length (prompt) | 512 (256) | 512 (256) | 512 (256) | 512 (256) |
| Epochs / optimizer steps | 1 / TBD | 1 / TBD | 1 / TBD | 3 / TBD |
| β | – | 0.1 | 0.1 | 0.1 |
| Checkpoint selection | min val loss | max val reward accuracy | max val reward accuracy | final |

Generation (all compared models): greedy (`do_sample=False`, so temperature and top-p are unused), `max_new_tokens=512`
(256 for persona), `repetition_penalty=1.1`, no length penalty, stop on `<|im_end|>` / `<|endoftext|>`.

SFT loss reduction: mean NLL over all unmasked response tokens in each micro-batch.

## 3. Parameter choices and deviations

| Setting | Guide | Used | Type | Reason / evidence |
|---|---|---|---|---|
| Decoding | greedy | greedy + repetition_penalty 1.1 | quality | TBD: e.g. repetition loops seen in the base/SFT sanity check |
| LoRA config | student choice | r16/α32, all linear | – | small dataset (200 pairs); style needs MLP and attention capacity |
| TBD | | | | |

## 4. Training behaviour

![SFT](figures/sft_loss.png)
![DPO](figures/dpo_curves.png)
![LoRA](figures/lora_dpo_curves.png)

TBD: convergence, best-checkpoint step, reward accuracy and margin trends. At step 0 the policy equals the reference,
so accuracy is 0 and loss is log 2.

## 5. DPO-versus-SFT results

Judge: `google/gemini-3.6-flash` (TBD: confirm), 5 pairs per request, 5 s sleep, 3 retries, blinded A/B with randomised order.

TBD: paste `report/results_table.md`.

## 6. Interpretation and error analysis

TBD: per eval set, say which model won and by how much, then the likely reasons (dataset match, β, steps, reward
diagnostics, verbosity, refusals). Include 2–3 examples of improvement and 1–2 of regression.

## 7. Limitations and reproducibility

Single seed; a single LLM judge (position and verbosity bias: see the A-position rate in the table); 300-prompt eval sets;
fp16 reference vs fp32 policy gives small non-zero step-0 margins; compute-limited single epoch.
Key paths: `outputs/*/config.json`, `outputs/*/metrics.jsonl`, `outputs/generations/`, `outputs/eval/`.
