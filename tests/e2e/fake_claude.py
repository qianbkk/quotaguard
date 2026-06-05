#!/usr/bin/env python3
"""Fake Claude —— 仿真 Claude Code CLI 行为（读 PROGRESS.md、检测 PAUSE、写断点）。"""
import argparse
import sys
import time
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

PAUSE_FILE = Path(".quotaguard/PAUSE.flag")
PROGRESS_FILE = Path("PROGRESS.md")


def log(msg):
    print(f"[fake-claude] {msg}", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tool-sleep", type=int, default=5)
    ap.add_argument("--rounds", type=int, default=8)
    args = ap.parse_args()

    log("started")
    if PROGRESS_FILE.exists():
        log(f"--continue: read {len(PROGRESS_FILE.read_text().splitlines())} lines from PROGRESS.md")
    for i in range(1, args.rounds + 1):
        log(f"tool #{i} (sleep {args.tool_sleep}s)")
        for _ in range(args.tool_sleep):
            if PAUSE_FILE.exists():
                log(f"!! PAUSE.flag detected at tool #{i}, /exit")
                with open(PROGRESS_FILE, "a") as f:
                    f.write(f"\n## tool #{i} paused at {time.strftime('%H:%M:%S')}\n")
                return 0
            time.sleep(1)
        log(f"  tool #{i} done")
    log("all tools done, /exit")
    return 0


if __name__ == "__main__":
    sys.exit(main())
