"""Memory CLI (required interface).

Usage:
  python3 main.py --questions path/to/questions.jsonl --output answers.jsonl
  python3 main.py --questions evals/memory_train.jsonl --output outputs/memory_train_answers.jsonl
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.memory import MemorySystem


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--questions", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--data", default="data")
    args = p.parse_args()

    ms = MemorySystem(data_dir=args.data)
    out_p = Path(args.output)
    out_p.parent.mkdir(parents=True, exist_ok=True)
    with open(args.questions) as f, open(out_p, "w") as out:
        for line in f:
            line = line.strip()
            if not line:
                continue
            q = json.loads(line)
            res = ms.answer_one(q["id"], q["question"], q["as_of"])
            out.write(json.dumps(res) + "\n")


if __name__ == "__main__":
    main()
