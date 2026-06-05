#!/usr/bin/env python3
"""Claude Code PreToolUse hook —— 检测 PAUSE.flag 优雅阻塞。

行为：
- 读 $CLAUDE_PROJECT_DIR/.claude/PAUSE.flag（不存在 → 放行）
- 存在 → 读元数据，stderr 输出给 Claude，exit 2 阻塞当前工具
- Claude 看到 stderr 后会自己写 PROGRESS.md / BREAKPOINT.md 然后退出

这是 Claude Code 官方机制（code.claude.com/docs/en/hooks）：
- PreToolUse exit 2 = 阻塞工具调用，stderr 反馈给模型
- 模型自主决定写断点 + 退出（不需要外部 kill）
"""
import os
import sys
import json
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

PROJECT_DIR = Path(os.environ.get("CLAUDE_PROJECT_DIR", "."))
PAUSE_FILE = PROJECT_DIR / ".claude" / "PAUSE.flag"
RESUME_FILE = PROJECT_DIR / ".claude" / "RESUME.flag"
BREAKPOINT_FILE = PROJECT_DIR / "BREAKPOINT.md"
PROGRESS_FILE = PROJECT_DIR / "PROGRESS.md"

if PAUSE_FILE.exists():
    try:
        meta = json.loads(PAUSE_FILE.read_text(encoding="utf-8"))
    except Exception:
        meta = {"triggered_at": "?", "reason": "?"}
    triggered = meta.get("triggered_at", "?")
    reason = meta.get("reason", "MiniMax quota low")
    remains = meta.get("remains_percent", "?")
    sys.stderr.write(
        f"PAUSED at {triggered}: {reason} (remains={remains}%)\n"
        f"ACTION REQUIRED:\n"
        f"  1) Read BREAKPOINT.md to understand the auto-saved state\n"
        f"  2) Append a short note to PROGRESS.md about what you JUST did before this PreToolUse\n"
        f"  3) Exit gracefully (type /exit or press Ctrl+D twice)\n"
        f"The orchestrator will auto-resume you when quota is restored.\n"
    )
    sys.exit(2)

# 检查 RESUME 标志（已恢复但 PreToolUse 还没跑到）
if RESUME_FILE.exists():
    try:
        meta = json.loads(RESUME_FILE.read_text(encoding="utf-8"))
    except Exception:
        meta = {}
    sys.stderr.write(
        f"RESUMED at {meta.get('resumed_at', '?')}: quota restored "
        f"(remains={meta.get('remains_percent', '?')}%, "
        f"end_time={meta.get('remains_time_ms', '?')}ms)\n"
    )

sys.exit(0)
