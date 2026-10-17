"""Run every pairwise judging job through the Kaggle Model Proxy (Task 7).

This is how the submitted judgments were produced: locally, with Kaggle-issued
Model Proxy credentials (`kaggle benchmarks auth`), calling google/gemini-3.6-flash
via the kaggle_benchmarks library - the same models and proxy a Kaggle Benchmarks
notebook uses. Credentials last one hour, so they are refreshed before every job,
and each job runs in a fresh process. Resumable (--reuse_judgments).

  pip install kaggle kaggle-benchmarks      # plus a Kaggle API token in ~/.kaggle
  python scripts/run_all_judging.py [--test]

Inside a Kaggle Benchmarks notebook, notebooks/06_judge_paste_cell.py does the same.
"""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PY = sys.executable
KAGGLE = shutil.which("kaggle") or str(Path(sys.executable).with_name("kaggle"))
ENV_FILE = Path(tempfile.gettempdir()) / "kbench_proxy.env"  # outside the repo; never committed
JUDGE = "google/gemini-3.6-flash"
EVAL = REPO / "results" / "eval"

JOBS = [("uf_dpo_vs_sft", "uf_dpo_vs_sft", None),
        ("hh_dpo_vs_sft", "hh_dpo_vs_sft", None),
        ("hh_dpo_lr5e-6_vs_sft", "hh_dpo_lr5e-6_vs_sft", None),
        ("persona_lora_vs_instruct", "persona_lora_vs_instruct", None),
        ("persona_lora_vs_instruct", "persona_lora_vs_instruct_style", "persona_style"),
        ("persona_lora_vs_instruct", "persona_lora_vs_instruct_helpful", "helpful_safe"),
        ("persona_lora_v2_vs_instruct", "persona_lora_v2_vs_instruct", None),
        ("persona_lora_v2_vs_instruct", "persona_lora_v2_vs_instruct_style", "persona_style"),
        ("persona_lora_v2_vs_instruct", "persona_lora_v2_vs_instruct_helpful", "helpful_safe"),
        ("persona_lora_v3_vs_instruct", "persona_lora_v3_vs_instruct", None),
        ("persona_lora_v3_vs_instruct", "persona_lora_v3_vs_instruct_style", "persona_style"),
        ("persona_lora_v3_vs_instruct", "persona_lora_v3_vs_instruct_helpful", "helpful_safe"),
        ("sft_vs_base_alpaca", "sft_vs_base_alpaca", None),
        ("xhh_dpo_vs_sft_alpaca", "xhh_dpo_vs_sft_alpaca", None),
        ("xhh_dpo_lr5e-6_vs_sft_alpaca", "xhh_dpo_lr5e-6_vs_sft_alpaca", None),
        ("xuf_dpo_vs_sft_hh", "xuf_dpo_vs_sft_hh", None)]


def fresh_env():
    subprocess.run([KAGGLE, "b", "auth", "-y", "--env-file", str(ENV_FILE)], capture_output=True, check=True)
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8", LLM_DEFAULT=JUDGE, LLMS_AVAILABLE=JUDGE,
               BENCHMARK_CONSOLE_QUIET="True", INTERACTIVE_UI="False")
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k] = v.strip().strip('"').strip("'")
    return env


def judge(pairs, out, kind, env, limit=None, out_dir=EVAL):
    cmd = [PY, "scripts/judge_pairwise_kaggle.py", "--pairs_path", str(EVAL / f"{pairs}_pairs.jsonl"),
           "--output_path", str(out_dir / f"{out}_judged.jsonl"), "--judge_model", JUDGE, "--batch_size", "5",
           "--sleep_seconds", "5", "--max_retries", "3", "--reuse_judgments", "--retry_unresolved"]
    if kind:
        cmd += ["--eval_kind", kind]
    if limit:
        cmd += ["--limit", str(limit)]
    r = subprocess.run(cmd, cwd=REPO, env=env, capture_output=True, text=True, encoding="utf-8")
    lines = [l for l in r.stdout.splitlines() if not l.startswith(("\U0001f464", "\U0001f916"))]
    print("\n".join(lines[-40:]), r.stderr[-2000:] if r.returncode else "", flush=True)
    return r.returncode


if __name__ == "__main__":
    if "--test" in sys.argv:
        test_dir = Path(tempfile.gettempdir()) / "judge_live_test"
        test_dir.mkdir(exist_ok=True)
        sys.exit(judge("uf_dpo_vs_sft", "uf_dpo_vs_sft", None, fresh_env(), limit=10, out_dir=test_dir))
    for pairs, out, kind in JOBS:
        print(f"===== {out} ({kind or 'default criteria'})", flush=True)
        rc = judge(pairs, out, kind, fresh_env())
        if rc == 0:
            subprocess.run([PY, "scripts/analyze_results.py", "--judged", str(EVAL / f"{out}_judged.jsonl"),
                            "--pairs", str(EVAL / f"{pairs}_pairs.jsonl"), "--out", f"report/analysis/{out}.md"],
                           cwd=REPO, env=dict(os.environ, PYTHONUTF8="1"))
    subprocess.run([PY, "scripts/summarize_results.py", "--eval_dir", str(EVAL), "--out", "report/results_table.md"],
                   cwd=REPO, env=dict(os.environ, PYTHONUTF8="1"))
    print("ALL JUDGING DONE", flush=True)
