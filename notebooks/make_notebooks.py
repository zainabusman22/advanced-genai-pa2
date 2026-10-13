"""Regenerate the Kaggle notebooks in this folder: ``python notebooks/make_notebooks.py``.

Each notebook is a thin driver around ``scripts/*.py`` so that all logic lives
in version-controlled Python files.
"""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent

CLONE = r'''# Private repo: add a GitHub token as a Kaggle secret named GITHUB_TOKEN
# (Add-ons -> Secrets -> Add secret, then tick it for this notebook).
REPO_URL = "https://github.com/zainabusman22/advanced-genai-pa2.git"
REPO = "/kaggle/working/advanced-genai-pa2"

import os, subprocess, sys
if not os.path.exists(REPO):
    from kaggle_secrets import UserSecretsClient
    token = UserSecretsClient().get_secret("GITHUB_TOKEN")
    authed = REPO_URL.replace("https://", f"https://x-access-token:{token}@")
    subprocess.run(["git", "clone", "--depth", "1", authed, REPO], check=True, capture_output=True)
    subprocess.run(["git", "-C", REPO, "remote", "set-url", "origin", REPO_URL], check=True)  # don't keep the token on disk
    del token, authed
os.chdir(REPO)
'''

SETUP = "# ---- Task 1: repository + environment setup (Kaggle, GPU T4 x2) ----\n" + CLONE + r'''!git -C {REPO} log --oneline -1
!python -m pip uninstall -y -q torchao || true
!python -m pip install -q "transformers>=4.43.0,<5" "datasets>=2.18,<5" huggingface_hub accelerate tqdm pyyaml sentencepiece safetensors pytest
!python -m pip install -q -e . --no-deps
!nvidia-smi
'''

VERSIONS = r'''import torch, transformers, datasets
print("torch", torch.__version__, "| transformers", transformers.__version__, "| datasets", datasets.__version__)
print("GPUs:", [torch.cuda.get_device_name(i) for i in range(torch.cuda.device_count())])
assert torch.cuda.device_count() == 2, "Select the GPU T4 x2 accelerator"
'''

FIND = r'''# Locate artifacts from earlier notebooks attached as input datasets.
import glob
def find_dir(pattern):
    hits = sorted(glob.glob(f"/kaggle/input/**/{pattern}", recursive=True))
    if not hits:
        raise FileNotFoundError(f"nothing matching {pattern} under /kaggle/input - attach the previous notebook's output")
    print(pattern, "->", hits[0])
    return os.path.dirname(hits[0])
'''

ZIP = r'''# Archive outputs so they survive the session (then: Save Version -> attach as dataset).
!cd {REPO} && zip -qr /kaggle/working/{name}.zip {paths}
!ls -lh /kaggle/working/*.zip
'''


def nb(cells):
    out = []
    for kind, src in cells:
        out.append({"cell_type": kind, "metadata": {}, "source": src.strip("\n").splitlines(keepends=True),
                    **({"outputs": [], "execution_count": None} if kind == "code" else {})})
    return {"cells": out, "metadata": {"kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
                                       "language_info": {"name": "python"}}, "nbformat": 4, "nbformat_minor": 5}


def md(s):
    return ("markdown", s)


def code(s):
    return ("code", s)


NOTEBOOKS = {
    "01_setup_data_sft.ipynb": [
        md("# 01 - Setup, data preparation, baseline generation, SFT\nAccelerator: **GPU T4 x2**, Internet **on**."),
        code(SETUP), code(VERSIONS),
        md("## Public + own adapter tests"),
        code("!PYTHONPATH=. python -m pytest -q tests/"),
        md("## Data (UltraChat SFT, HH-RLHF, UltraFeedback, AlpacaEval 300 / seed 42)"),
        code("!python scripts/prepare_data.py\n!wc -l data/processed/*.jsonl\n!ls -l eval_sets/"),
        md("## Task 2: baseline generation (Qwen2.5-0.5B base) on GPU 1 while SFT trains on GPU 0"),
        code('''import subprocess
base_log = open("/kaggle/working/base_gen.log", "w")
base_proc = subprocess.Popen(
    "python scripts/generate.py --model_path Qwen/Qwen2.5-0.5B --device cuda:1 "
    "--eval_set eval_sets/alpaca_eval_300_seed42.json --generator_name qwen2.5-0.5b-base "
    "--dataset_name alpaca_eval --output_path outputs/generations/alpaca/base.json && "
    "python scripts/generate.py --model_path Qwen/Qwen2.5-0.5B --device cuda:1 "
    "--eval_set eval_sets/hh_rlhf_eval_300_seed42.json --generator_name qwen2.5-0.5b-base "
    "--output_path outputs/generations/hh/base.json",
    shell=True, stdout=base_log, stderr=subprocess.STDOUT)'''),
        md("## Task 3: SFT (30k train / 1k val)"),
        code("!python scripts/train_sft.py --output_dir outputs/sft --device cuda:0"),
        code('''base_proc.wait(); print("base generation exit code", base_proc.returncode)
!tail -3 /kaggle/working/base_gen.log
!python scripts/plot_metrics.py --sft outputs/sft/metrics.jsonl --out_dir report/figures'''),
        md("## Sanity check the SFT checkpoint on a few prompts"),
        code("!python scripts/generate.py --model_path outputs/sft/best --eval_set eval_sets/alpaca_eval_300_seed42.json "
             "--generator_name sft-sanity --output_path /tmp/x.json --limit 4"),
        code(ZIP.replace("{name}", "sft_run_outputs").replace(
            "{paths}", "outputs/sft data/processed eval_sets outputs/generations report/figures")),
    ],
    "02_dpo_hh.ipynb": [
        md("# 02 - From-scratch DPO on HH-RLHF\nAttach the output of notebook 01 as an input dataset."),
        code(SETUP), code(VERSIONS), code(FIND),
        code('''SFT_BEST = find_dir("sft/best/config.json")
DATA = find_dir("data/processed/hh_train.jsonl")'''),
        code('''!python scripts/train_dpo.py --sft_path {SFT_BEST} \\
    --train_path {DATA}/hh_train.jsonl --val_path {DATA}/hh_val.jsonl \\
    --output_dir outputs/dpo_hh --beta 0.1 --lr 1e-6 --grad_accum 32'''),
        code(ZIP.replace("{name}", "dpo_hh_run_outputs").replace("{paths}", "outputs/dpo_hh")),
    ],
    "03_dpo_uf.ipynb": [
        md("# 03 - From-scratch DPO on UltraFeedback\nAttach the output of notebook 01 as an input dataset."),
        code(SETUP), code(VERSIONS), code(FIND),
        code('''SFT_BEST = find_dir("sft/best/config.json")
DATA = find_dir("data/processed/uf_train.jsonl")'''),
        code('''!python scripts/train_dpo.py --sft_path {SFT_BEST} \\
    --train_path {DATA}/uf_train.jsonl --val_path {DATA}/uf_val.jsonl \\
    --output_dir outputs/dpo_uf --beta 0.1 --lr 1e-6 --grad_accum 32'''),
        code(ZIP.replace("{name}", "dpo_uf_run_outputs").replace("{paths}", "outputs/dpo_uf")),
    ],
    "04_lora_persona.ipynb": [
        md("# 04 - Idiot Sandwich: TRL DPOTrainer + LoRA on SmolLM2-360M-Instruct"),
        code(SETUP), code('!python -m pip install -q "trl>=0.19,<0.24" "peft>=0.13"'), code(VERSIONS),
        code("!python scripts/train_lora_dpo.py --output_dir outputs/lora_dpo_persona"),
        md("## Generate persona-eval outputs: instruct model (baseline) vs LoRA-DPO"),
        code('''!python scripts/generate.py --model_path HuggingFaceTB/SmolLM2-360M-Instruct --template tokenizer \\
    --eval_set eval_sets/persona_style_eval_24.json --generator_name smollm2-360m-instruct \\
    --output_path outputs/generations/persona/instruct.json --max_new_tokens 256
!python scripts/generate.py --model_path HuggingFaceTB/SmolLM2-360M-Instruct --template tokenizer \\
    --adapter_path outputs/lora_dpo_persona/adapter --eval_set eval_sets/persona_style_eval_24.json \\
    --generator_name smollm2-360m-lora-dpo-persona --output_path outputs/generations/persona/lora_dpo.json --max_new_tokens 256
!python scripts/build_pairs.py --model_path outputs/generations/persona/lora_dpo.json \\
    --baseline_path outputs/generations/persona/instruct.json --eval_kind persona \\
    --output_path outputs/eval/persona_lora_vs_instruct_pairs.jsonl
!python scripts/plot_metrics.py --lora outputs/lora_dpo_persona/metrics.jsonl --out_dir report/figures'''),
        code('''import json
for a, b in zip(json.load(open("outputs/generations/persona/instruct.json"))[:6],
                json.load(open("outputs/generations/persona/lora_dpo.json"))[:6]):
    print("PROMPT:", a["instruction"]); print("  INSTRUCT:", a["output"][:300]); print("  LORA-DPO:", b["output"][:300]); print()'''),
        code(ZIP.replace("{name}", "lora_persona_outputs").replace(
            "{paths}", "outputs/lora_dpo_persona outputs/generations/persona outputs/eval report/figures")),
    ],
    "05_generate_eval_outputs.ipynb": [
        md("# 05 - Task 6: generation for DPO-vs-SFT comparisons\nAttach outputs of notebooks 01, 02 and 03. "
           "Greedy decoding, repetition_penalty 1.1, max_new_tokens 512, identical for every model."),
        code(SETUP), code(VERSIONS), code(FIND),
        code('''SFT_BEST = find_dir("sft/best/config.json")
HH_BEST = find_dir("dpo_hh/best/config.json")
UF_BEST = find_dir("dpo_uf/best/config.json")
BASE_GENS = find_dir("generations/alpaca/base.json")  # baseline generations from notebook 01
!mkdir -p outputs/generations && cp -rn {BASE_GENS}/../* outputs/generations/ && ls -R outputs/generations'''),
        md("Sanity check (2-5 prompts) before the full run"),
        code('''!python scripts/generate.py --model_path {UF_BEST} --eval_set eval_sets/alpaca_eval_300_seed42.json --generator_name s --output_path /tmp/s.json --limit 3
!python scripts/generate.py --model_path {HH_BEST} --eval_set eval_sets/hh_rlhf_eval_300_seed42.json --generator_name s --output_path /tmp/s.json --limit 3'''),
        md("Full generation: two GPUs in parallel"),
        code('''import subprocess
G = "python scripts/generate.py"
gpu0 = (f"{G} --device cuda:0 --model_path {SFT_BEST} --eval_set eval_sets/alpaca_eval_300_seed42.json --generator_name qwen2.5-0.5b-sft --dataset_name alpaca_eval --output_path outputs/generations/alpaca/sft.json && "
        f"{G} --device cuda:0 --model_path {UF_BEST} --eval_set eval_sets/alpaca_eval_300_seed42.json --generator_name qwen2.5-0.5b-dpo-uf --dataset_name alpaca_eval --output_path outputs/generations/alpaca/dpo_uf.json")
gpu1 = (f"{G} --device cuda:1 --model_path {SFT_BEST} --eval_set eval_sets/hh_rlhf_eval_300_seed42.json --generator_name qwen2.5-0.5b-sft --output_path outputs/generations/hh/sft.json && "
        f"{G} --device cuda:1 --model_path {HH_BEST} --eval_set eval_sets/hh_rlhf_eval_300_seed42.json --generator_name qwen2.5-0.5b-dpo-hh --output_path outputs/generations/hh/dpo_hh.json")
procs = [subprocess.Popen(c, shell=True, stdout=open(f"/kaggle/working/gen{i}.log", "w"), stderr=subprocess.STDOUT) for i, c in enumerate([gpu0, gpu1])]
print([p.wait() for p in procs])
!tail -2 /kaggle/working/gen0.log /kaggle/working/gen1.log'''),
        md("Build blinded pairwise files (randomised A/B, hidden identity metadata)"),
        code('''!python scripts/build_pairs.py --model_path outputs/generations/alpaca/dpo_uf.json --baseline_path outputs/generations/alpaca/sft.json --eval_kind general --output_path outputs/eval/uf_dpo_vs_sft_pairs.jsonl
!python scripts/build_pairs.py --model_path outputs/generations/hh/dpo_hh.json --baseline_path outputs/generations/hh/sft.json --eval_kind safety --output_path outputs/eval/hh_dpo_vs_sft_pairs.jsonl
!python scripts/build_pairs.py --model_path outputs/generations/alpaca/sft.json --baseline_path outputs/generations/alpaca/base.json --eval_kind general --output_path outputs/eval/sft_vs_base_alpaca_pairs.jsonl'''),
        code(ZIP.replace("{name}", "generation_outputs").replace("{paths}", "outputs/generations outputs/eval")),
    ],
    "06_judge_kaggle.ipynb": [
        md("# 06 - Task 7: pairwise judging with Kaggle models\nCreate this notebook as a **utility benchmark** "
           "notebook so `kaggle_benchmarks` model-proxy calls are available. Attach outputs of notebooks 04 and 05. "
           "No GPU needed. Judging only reads saved pair files."),
        code(CLONE + '''import glob, shutil
import kaggle_benchmarks as kbench
print(list(kbench.llms.keys()) if hasattr(kbench.llms, "keys") else kbench.llms)'''),
        code('''# Copy saved pair files (and any earlier judgments, so reruns resume) into the repo.
os.makedirs("outputs/eval", exist_ok=True)
for f in glob.glob("/kaggle/input/**/outputs/eval/*.jsonl", recursive=True):
    dst = os.path.join("outputs/eval", os.path.basename(f))
    if not os.path.exists(dst):
        shutil.copy(f, dst)
!ls -l outputs/eval'''),
        code('''JUDGE = "google/gemini-3.6-flash"   # record the model actually used in the report
for name in ["uf_dpo_vs_sft", "hh_dpo_vs_sft", "persona_lora_vs_instruct", "sft_vs_base_alpaca"]:
    if not os.path.exists(f"outputs/eval/{name}_pairs.jsonl"):
        print("missing", name); continue
    !python scripts/judge_pairwise_kaggle.py --pairs_path outputs/eval/{name}_pairs.jsonl \\
        --output_path outputs/eval/{name}_judged.jsonl --judge_model {JUDGE} \\
        --batch_size 5 --sleep_seconds 5 --max_retries 3 --reuse_judgments'''),
        code('''!python scripts/summarize_results.py --eval_dir outputs/eval --out report/results_table.md
!cat report/results_table.md'''),
        code(ZIP.replace("{name}", "judge_outputs").replace("{paths}", "outputs/eval report/results_table.md")),
    ],
}


if __name__ == "__main__":
    for name, cells in NOTEBOOKS.items():
        (HERE / name).write_text(json.dumps(nb(cells), indent=1), encoding="utf-8")
        print("wrote", name)
