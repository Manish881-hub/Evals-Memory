"""Actions CLI (bonus dry-run interface).

Usage:
  python3 actions.py --commands path/to/actions.jsonl --output actions.jsonl
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.actions import parse_command


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--commands", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--data", default="data")
    args = p.parse_args()

    out_p = Path(args.output)
    out_p.parent.mkdir(parents=True, exist_ok=True)
    with open(args.commands) as f, open(out_p, "w") as out:
        for line in f:
            line = line.strip()
            if not line:
                continue
            q = json.loads(line)
            actions = parse_command(q["command"], q.get("as_of", "2026-09-18T09:00:00-07:00"), data_dir=args.data)
            out.write(json.dumps({"id": q["id"], "actions": actions}) + "\n")


if __name__ == "__main__":
    main()
