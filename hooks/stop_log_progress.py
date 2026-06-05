#!/usr/bin/env python3
"""Claude Code Stop hook —— session 结束时追加一行到 PROGRESS.md。

让 orchestrator 在外层能看到 Claude 何时退出（自然完成 / 被 PreToolUse 阻塞 / 用户主动）。
"""
import os
import sys
import time
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

PROJECT_DIR = Path(os.environ.get("CLAUDE_PROJECT_DIR", "."))
PROGRESS_FILE = PROJECT_DIR / "PROGRESS.md"

PROGRESS_FILE.parent.mkdir(parents=True, exist_ok=True)
ts = time.strftime("%Y-%m-%d %H:%M:%S")
with open(PROGRESS_FILE, "a", encoding="utf-8") as f:
    f.write(f"\n## {ts} session ended (orchestrator will auto-resume if PAUSE.flag was cleared)\n")
sys.exit(0)
