# Task 7 judge: paste this whole file into ONE cell of a Kaggle Benchmarks notebook
# (kaggle.com/benchmarks/tasks/new). Attach as inputs: the dataset
# "advanced-genai-pa2-code" and the notebooks pa2-05-generate, pa2-04-lora,
# pa2-04b-lora-v2, pa2-04c-lora-v3 and pa2-02b-dpo-hh-lr5e-6.
#
# Run first with TEST = True (judges 5 pairs, about 10 seconds), then set TEST = False and run again.
TEST = True
JUDGE = "google/gemini-3.6-flash"  # if this is not available, the error lists the models that are

import glob, os, shutil, subprocess, sys

REPO = "/kaggle/working/advanced-genai-pa2"
if not os.path.exists(REPO):
    code = [os.path.dirname(f) for f in glob.glob("/kaggle/input/**/submission_adapter.py", recursive=True)
            if "advanced-genai-pa2-code" in f]
    assert code, "Attach the dataset 'advanced-genai-pa2-code' (Add Input -> Datasets -> Your Datasets)"
    shutil.copytree(code[0], REPO)
os.chdir(REPO)
print(open("CODE_VERSION.txt").read())

# Collect pair files (and earlier judgments, so a rerun resumes instead of re-judging).
os.makedirs("outputs/eval", exist_ok=True)
for f in glob.glob("/kaggle/input/**/outputs/eval/*.jsonl", recursive=True):
    dst = os.path.join("outputs/eval", os.path.basename(f))
    if not os.path.exists(dst):
        shutil.copy(f, dst)
print(sorted(os.listdir("outputs/eval")))

import kaggle_benchmarks as kbench
try:
    print("available judge models:", sorted(kbench.llms.keys()))
except Exception as exc:
    print("could not list models:", exc)


def run(cmd):
    print(">>", cmd)
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    print(r.stdout[-3000:], r.stderr[-3000:])
    return r.returncode


# The judge script imports kaggle_benchmarks in its own process; run it in-process instead so it
# shares this notebook's benchmark session.
sys.path.insert(0, REPO)
import importlib
judge = importlib.import_module("scripts.judge_pairwise_kaggle")


def judge_pairs(pairs, out, kind=None, limit=-1, out_dir="outputs/eval"):
    path = f"outputs/eval/{pairs}_pairs.jsonl"
    if not os.path.exists(path):
        print("missing", path)
        return
    argv = ["judge", "--pairs_path", path, "--output_path", f"{out_dir}/{out}_judged.jsonl",
            "--judge_model", JUDGE, "--batch_size", "5", "--sleep_seconds", "5", "--max_retries", "3",
            "--reuse_judgments"]
    if kind:
        argv += ["--eval_kind", kind]
    if limit > 0:
        argv += ["--limit", str(limit)]
    sys.argv = argv
    judge.main()


if TEST:
    os.makedirs("outputs/eval_test", exist_ok=True)
    judge_pairs("uf_dpo_vs_sft", "uf_dpo_vs_sft", limit=5, out_dir="outputs/eval_test")
    print(open("outputs/eval_test/uf_dpo_vs_sft_judged.jsonl").read()[:3000])
else:
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
    for pairs, out, kind in JOBS:
        judge_pairs(pairs, out, kind)
        run(f"python scripts/analyze_results.py --judged outputs/eval/{out}_judged.jsonl "
            f"--pairs outputs/eval/{pairs}_pairs.jsonl --out report/analysis/{out}.md")
    run("python scripts/summarize_results.py --eval_dir outputs/eval --out report/results_table.md")
    print(open("report/results_table.md").read())
    run("zip -qr /kaggle/working/judge_outputs.zip outputs/eval report/results_table.md report/analysis")
