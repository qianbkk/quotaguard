#!/usr/bin/env python3
"""Claude Code SessionStart hook —— 启动/恢复时注入进度上下文。

读取 PROGRESS.md（Anthropic 官方推荐的进度文件）和 BREAKPOINT.md，
注入到 stderr，让 Claude 看到后能无缝衔接。
"""
import os
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

PROJECT_DIR = Path(os.environ.get("CLAUDE_PROJECT_DIR", "."))
PROGRESS_FILE = PROJECT_DIR / "PROGRESS.md"
BREAKPOINT_FILE = PROJECT_DIR / "BREAKPOINT.md"
RESUME_FILE = PROJECT_DIR / ".claude" / "RESUME.flag"
PAUSE_FILE = PROJECT_DIR / ".claude" / "PAUSE.flag"

parts = []
parts.append("## SessionStart: orchestrator-managed session")
if PAUSE_FILE.exists():
    parts.append("WARNING: PAUSE.flag still exists — should not be here. PreToolUse will block.")
if RESUME_FILE.exists():
    parts.append("Resumed from quota refresh.")
if PROGRESS_FILE.exists():
    parts.append("\n## PROGRESS.md (last 100 lines):\n" +
                 "\n".join(PROGRESS_FILE.read_text(encoding="utf-8", errors="ignore").splitlines()[-100:]))
if BREAKPOINT_FILE.exists():
    parts.append("\n## BREAKPOINT.md (auto-saved at pause):\n" +
                 BREAKPOINT_FILE.read_text(encoding="utf-8", errors="ignore")[:4000])

sys.stderr.write("\n".join(parts)[:8000])
sys.exit(0)
