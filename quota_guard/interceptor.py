"""quota_guard.interceptor —— PreToolUse hook 入口脚本。

被 Claude Code 在每次工具调用前调用，逻辑：
  1. 读 quota_state.json（monitor 写入）
  2. 判断 is_critical → exit 2（阻断）
  3. fail-open：读不到 state 时放行（监控故障不阻断任务）

这是 Claude Code 优雅暂停的核心机制。
"""
from __future__ import annotations
import json
import os
import sys
import time
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass


DEFAULT_STATE_FILE = "~/.quotaguard/quota_state.json"
DEFAULT_PAUSE_FILE = "~/.quotaguard/PAUSE.flag"


def main() -> int:
    state_file = Path(os.environ.get("QUOTAGUARD_STATE", DEFAULT_STATE_FILE)).expanduser()
    pause_file = Path(os.environ.get("QUOTAGUARD_PAUSE", DEFAULT_PAUSE_FILE)).expanduser()

    # 防止无限循环：Claude Code 自身标志
    # （PreToolUse 在某些重试路径上会再触发一次，需要放行）
    try:
        hook_input = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        hook_input = {}
    if hook_input.get("stop_hook_active"):
        return 0

    # 哨兵文件检查（最快路径）
    if pause_file.exists():
        refresh_in = ""
        if state_file.exists():
            try:
                state = json.loads(state_file.read_text(encoding="utf-8"))
                end_at = state.get("window_end_at")
                if end_at:
                    remain = end_at - time.time()
                    if remain > 0:
                        refresh_in = f" 约 {remain / 60:.0f} 分钟后刷新"
            except Exception:
                pass
        sys.stderr.write(
            f"[QuotaGuard] 配额临界，工具调用被阻断{refresh_in}。\n"
            f"建议：1) 完成当前单元  2) 写入 PROGRESS.md  3) 退出 Claude Code  \n"
            f"orchestrator 会在刷新后自动重启 claude --continue。\n"
        )
        return 2  # 阻断

    # 读 state 详细判断
    if not state_file.exists():
        # 监控没启动，fail-open
        return 0

    try:
        state = json.loads(state_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0  # 解析失败，fail-open

    if state.get("is_critical"):
        end_at = state.get("window_end_at")
        remain = ""
        if end_at and end_at > time.time():
            remain = f" 约 {(end_at - time.time()) / 60:.0f} 分钟后刷新"
        sys.stderr.write(
            f"[QuotaGuard] 配额临界（{state.get('remains_pct', '?')}%），阻断工具调用{remain}。\n"
        )
        return 2

    return 0  # 健康：放行


if __name__ == "__main__":
    sys.exit(main())
