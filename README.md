# PA2: SFT + DPO on Qwen2.5-0.5B

Zainab Usman (27100409), Advanced GenAI, PA2 ([assignment manual](docs/assignment_manual.pdf)).

Post-training pipeline for a small language model:

1. **SFT** of `Qwen/Qwen2.5-0.5B` on 30k UltraChat examples, with my own training loop.
2. **From-scratch DPO** (own loss and loop) on 15k HH-RLHF pairs and, separately, on 15k UltraFeedback pairs.
3. **TRL `DPOTrainer` + LoRA** persona tuning of `HuggingFaceTB/SmolLM2-360M-Instruct`.
4. Fixed-prompt generation, blinded pairwise comparison files, and resumable LLM-as-judge evaluation (`google/gemini-3.6-flash` via Kaggle).

## Where to find each deliverable

| Manual item | Location |
|---|---|
| Final report (PDF, 7 pages) | [`report/report 27100409.pdf`](report/report%2027100409.pdf) (LaTeX source: [`report/latex/report.tex`](report/latex/report.tex)) |
| Root-level grading adapter | [`submission_adapter.py`](submission_adapter.py), a thin wrapper around [`alignment/core.py`](alignment/core.py) |
| Public adapter tests + my own tests | [`tests/`](tests/) (all 35 pass, see [Running the tests](#running-the-tests)) |
| Training plots for SFT and DPO | [`report/figures/`](report/figures/) |
| Evaluation summary, DPO vs SFT | [`report/results_table.md`](report/results_table.md) and Section 5 of the report |
| Task 1: setup | [`notebooks/00_data_cpu.ipynb`](notebooks/00_data_cpu.ipynb), [`notebooks/01_setup_data_sft.ipynb`](notebooks/01_setup_data_sft.ipynb) |
| Task 2: baseline generation | [`scripts/generate.py`](scripts/generate.py), outputs in [`results/generations/`](results/generations/) (`base.json`) |
| Task 3: SFT | [`scripts/train_sft.py`](scripts/train_sft.py), run record in [`results/runs/sft/`](results/runs/sft/) |
| Task 4: from-scratch DPO | [`scripts/train_dpo.py`](scripts/train_dpo.py), run records in [`results/runs/`](results/runs/) (`dpo_hh`, `dpo_hh_lr5e-6`, `dpo_uf`) |
| Task 5: TRL + LoRA persona DPO | [`scripts/train_lora_dpo.py`](scripts/train_lora_dpo.py), run records in [`results/runs/`](results/runs/) (`lora_v1`, `lora_v2`, `lora_v3`) |
| Task 6: generation and blinded pairs | [`scripts/generate.py`](scripts/generate.py), [`scripts/build_pairs.py`](scripts/build_pairs.py), outputs in [`results/eval/`](results/eval/) (`*_pairs.jsonl`) |
| Task 7: judging | [`scripts/judge_pairwise_kaggle.py`](scripts/judge_pairwise_kaggle.py), [`scripts/run_all_judging.py`](scripts/run_all_judging.py), outputs in [`results/eval/`](results/eval/) |
| Task 8: report | [`report/`](report/) |

## Results at a glance

Judge: `google/gemini-3.6-flash`. N = 300 prompts per comparison (24 for persona). The win rate below excludes ties. Full tables, including tie-adjusted rates and per-category results, are in the report.

| Comparison | Evaluation set | W / T / L | Win rate |
|---|---|---|---|
| SFT vs base | AlpacaEval | 226 / 10 / 64 | 77.9% |
| UltraFeedback DPO vs SFT | AlpacaEval | 113 / 77 / 110 | 50.7% |
| HH-RLHF DPO (LR 1e-6) vs SFT | Synthetic HH set | 134 / 70 / 96 | 58.3% |
| HH-RLHF DPO (LR 5e-6) vs SFT | Synthetic HH set | 171 / 21 / 108 | 61.3% |
| Persona LoRA-DPO v3 vs instruct (combined criterion) | Persona set | 15 / 3 / 6 | 71.4% |

## Repository layout

```text
alignment/
  core.py              batching, response-only log-probs, SFT loss, DPO loss   <- graded math
  prompting.py         ChatML prompt template shared by SFT, DPO and generation
  training.py          model, optimizer and mixed-precision helpers
  utils.py             I/O, seeding, LR schedule, checkpoint saving
submission_adapter.py  grading interface (wraps alignment/core.py)
scripts/
  prepare_data.py      download, filter and subsample all datasets (seed 42)
  train_sft.py         Task 3: SFT training loop
  train_dpo.py         Task 4: from-scratch DPO (used for HH-RLHF and UltraFeedback)
  train_lora_dpo.py    Task 5: TRL DPOTrainer + PEFT LoRA
  generate.py          Tasks 2 and 6: greedy, resumable generation
  build_pairs.py       Task 6: blinded pairwise files with randomized A/B order
  judge_pairwise_kaggle.py  Task 7: batched, retrying, resumable judge
  run_all_judging.py   Task 7: runs every judging job through the Kaggle Model Proxy
  summarize_results.py judge summaries -> report/results_table.md
  analyze_results.py   lengths, refusal rates and win/loss examples -> report/analysis/
  plot_metrics.py      training curves -> report/figures/
notebooks/             Kaggle notebooks that ran every stage (see below)
data/persona_lora/     provided persona preference data
eval_sets/             fixed evaluation prompts (AlpacaEval 300 / seed 42, safety set, persona set)
tests/                 public adapter smoke tests + my edge-case and cross-check tests
results/               logged outputs of every run (see "Results files")
report/                final report PDF, LaTeX source, figures, results table, per-comparison analyses
docs/                  assignment manual
```

## Design choices that matter for grading

* **Prompt format** (SFT, DPO and evaluation): Qwen ChatML,
  `<|im_start|>system\nYou are a helpful assistant.<|im_end|>\n<|im_start|>user\n{instruction}<|im_end|>\n<|im_start|>assistant\n`,
  with the response followed by EOS. The tokenizer's EOS is set to `<|im_end|>`.
* **Batching**: prompt and response are tokenized separately (`add_special_tokens=False`). EOS is appended
  to the response *before* right-truncation to `max_length`, and rows are right-padded to the longest row.
  Masks are built from true lengths, so tokenizers with `pad_token == eos_token` are handled correctly.
  `labels` are unshifted, with `-100` on prompt and padding positions.
* **Log-probs**: summed over response tokens only (shifted next-token prediction). Half-precision logits
  are upcast to float32; float32 and float64 inputs keep their dtype.
* **SFT loss reduction**: mean NLL over all unmasked response tokens in the batch.
* **DPO loss**: `mean(-logsigmoid(beta * ((pi_c - pi_r) - (ref_c - ref_r))))`, with the reference forward
  pass under `torch.no_grad()`.

## Running the tests

From the repository root. The tests run on CPU and need PyTorch, which `requirements.txt` does not pin, as the manual specifies:

```bash
pip install torch -r requirements.txt
PYTHONPATH=. python -m pytest -q tests/
```

The public smoke tests are in [`tests/test_public_adapter_smoke.py`](tests/test_public_adapter_smoke.py). My own tests are [`tests/test_impl_edge_cases.py`](tests/test_impl_edge_cases.py) (truncation, padding and tokenizer edge cases) and [`tests/test_reference_crosscheck.py`](tests/test_reference_crosscheck.py) (randomized checks against independent reference implementations and Hugging Face's built-in loss; the Qwen checks are skipped if the tokenizer can't be downloaded).

## How the runs were produced (Kaggle, GPU T4 x2)

Each notebook attaches the outputs of earlier notebooks as Kaggle inputs.

| Notebook | What it does | Inputs |
|---|---|---|
| `00_data_cpu` | setup, tests, data preparation (CPU only) | - |
| `01_setup_data_sft` | base-model generations and SFT | 00 |
| `02_dpo_hh` | HH-RLHF DPO, LR 1e-6 (policy on GPU 0, frozen FP32 reference on GPU 1) | 00, 01 |
| `03_dpo_uf` | UltraFeedback DPO, LR 1e-6 | 00, 01 |
| `04_lora_persona`, `04b_…`, `04c_…` | LoRA persona DPO v1, v2, v3, plus persona generations and pairs | - |
| `05_generate_eval_outputs` | SFT and DPO generations, blinded pairs, cross-evaluations | 00 to 03 |
| `02b_dpo_hh_lr5e-6` | HH-RLHF DPO at LR 5e-6, plus its generations and pairs | 00, 01, 05 |

Judging was run with [`scripts/run_all_judging.py`](scripts/run_all_judging.py) through the Kaggle Model Proxy
(`kaggle benchmarks auth`). [`notebooks/06_judge_kaggle.ipynb`](notebooks/06_judge_kaggle.ipynb) and
[`notebooks/06_judge_paste_cell.py`](notebooks/06_judge_paste_cell.py) do the same inside a Kaggle Benchmarks notebook.
Model checkpoints are stored in the Kaggle notebook outputs (`pa2-01-sft`, `pa2-02-dpo-hh`,
`pa2-02b-dpo-hh-lr5e-6`, `pa2-03-dpo-uf`, `pa2-04-lora`, `pa2-04b-lora-v2`, `pa2-04c-lora-v3`).

## Results files

Everything needed to check the report's tables and figures is committed:

```text
results/data_stats.json              subset sizes and filter statistics (seed 42)
results/runs/<run>/                  config.json, metrics.jsonl, summary.json for every training run
results/generations/{alpaca,hh,persona}/*.json   all evaluated model outputs (greedy decoding)
results/eval/*_pairs.jsonl           blinded A/B pair files (model identity only in "hidden")
results/eval/*_judged.jsonl          per-pair verdicts and reasons
results/eval/*_summary.json          win/tie/loss/unknown/failure counts per comparison
results/eval/*_failures.jsonl        judge failures logged before retry
report/results_table.md              all comparisons in one table
report/analysis/*.md                 lengths, refusal rates and win/loss examples per comparison
report/figures/*.png                 SFT, DPO and LoRA-DPO training curves
```

Running the scripts locally writes checkpoints and intermediate files to `outputs/`, which is not committed.
