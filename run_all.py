"""One-command train eval (eval-driven development).

Runs memory + retrieval + actions scoring with the supplied official harness.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def run(cmd: list[str]):
    print("+ " + " ".join(cmd))
    r = subprocess.run(cmd, cwd=ROOT)
    if r.returncode != 0:
        print(f"command failed: {cmd}", file=sys.stderr)


def main():
    out = ROOT / "outputs"
    out.mkdir(exist_ok=True)
    # memory
    run([sys.executable, "main.py", "--questions", "evals/memory_train.jsonl",
         "--output", "outputs/memory_train_answers.jsonl"])
    run([sys.executable, "eval_harness/score_retrieval.py",
         "--gold", "evals/memory_train.jsonl",
         "--answers", "outputs/memory_train_answers.jsonl",
         "--out", "outputs/results_retrieval.json"])
    run([sys.executable, "eval_harness/score_memory.py",
         "--gold", "evals/memory_train.jsonl",
         "--answers", "outputs/memory_train_answers.jsonl",
         "--judge", "none",
         "--out", "outputs/results_memory.json"])
    # actions
    run([sys.executable, "actions.py", "--commands", "evals/actions_train.jsonl",
         "--output", "outputs/actions_train_predictions.jsonl"])
    run([sys.executable, "eval_harness/score_actions.py",
         "--gold", "evals/actions_train.jsonl",
         "--predictions", "outputs/actions_train_predictions.jsonl",
         "--out", "outputs/results_actions.json"])
    # summary
    for name in ("results_retrieval.json", "results_memory.json", "results_actions.json"):
        p = out / name
        if p.exists():
            print(f"\n== {name} ==")
            print(json.dumps(json.loads(p.read_text())["summary"], indent=2)[:2000])


if __name__ == "__main__":
    main()
