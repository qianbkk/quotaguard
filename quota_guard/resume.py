"""quota_guard.resume —— 恢复管理器（orchestrator）。

复用 pause_minimax orchestrator 思想：
  1. 启动 monitor 后台
  2. 启动 claude --continue 前台
  3. claude 退出 → 检查 PAUSE.flag
  4. 等待 monitor 写 RESUME.flag（end_time 跳变金标准）
  5. 清理 → 重启 claude --continue

与 pause_minimax orchestrator 的差异：
  - 用 quota_state.json（结构化）替代多个散文件
  - 用精确 window_end_at 时间（智能等待间隔）
  - 启动子进程用 shlex.split（修复 Windows 路径空格问题）
"""
from __future__ import annotations
import argparse
import os
import shlex
import signal
import subprocess
import sys
import time
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

from .state import StateFile


def log(msg: str) -> None:
    print(f"[orchestrator] {msg}", flush=True)


def wait_for_resume(state_file: StateFile, timeout: float = 0, poll: float = 5.0) -> bool:
    """阻塞等待 RESUME.flag 出现。"""
    t0 = time.time()
    while True:
        if state_file.resume_path.exists():
            return True
        if timeout and (time.time() - t0) > timeout:
            return False
        # 智能 sleep：距离 refresh 越近越频繁
        if state_file.state_path.exists():
            try:
                import json
                state = json.loads(state_file.state_path.read_text(encoding="utf-8"))
                end_at = state.get("window_end_at", 0)
                if end_at and end_at > time.time():
                    sec_to_refresh = end_at - time.time()
                    if sec_to_refresh > 300:
                        time.sleep(min(60.0, poll))   # 距离 > 5min：60s
                    elif sec_to_refresh > 0:
                        time.sleep(min(15.0, poll))   # 距离 ≤ 5min：15s
                    else:
                        time.sleep(poll)              # 已过 refresh 时间：5s 探测
                    continue
            except Exception:
                pass
        time.sleep(poll)
    return False


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="quota_guard resume")
    ap.add_argument("--monitor-cmd", required=True, help="后台 monitor 命令")
    ap.add_argument("--claude-cmd", default="claude --continue", help="Claude 启动命令（第 2 轮+）")
    ap.add_argument("--initial-cmd", default=None, help="第 1 轮命令（含 -p）")
    ap.add_argument("--state-file", default="./.quotaguard/quota_state.json")
    ap.add_argument("--pause-file", default="./.quotaguard/PAUSE.flag")
    ap.add_argument("--resume-file", default="./.quotaguard/RESUME.flag")
    ap.add_argument("--max-rounds", type=int, default=50)
    ap.add_argument("--wait-timeout", type=float, default=0,
                    help="等待 RESUME 最长秒数（0=无限）")
    ap.add_argument("--clean-on-start", action="store_true")
    args = ap.parse_args()

    state_file = StateFile(
        state_path=Path(args.state_file).expanduser(),
        pause_path=Path(args.pause_file).expanduser(),
        resume_path=Path(args.resume_file).expanduser(),
    )

    project_dir = Path.cwd().resolve()
    # shlex.quote 包裹路径（处理 Windows 反斜杠 + 空格）
    quoted = shlex.quote(str(project_dir))
    args.monitor_cmd = args.monitor_cmd.replace("{project_dir}", quoted)
    args.claude_cmd = args.claude_cmd.replace("{project_dir}", quoted)
    if args.initial_cmd:
        args.initial_cmd = args.initial_cmd.replace("{project_dir}", quoted)

    # 兼容旧的 \{path\} 占位符（防止 shlex 拆碎反斜杠）
    # 把 \{path\} 替换为 Path-style 路径
    forward = str(project_dir).replace("\\", "/")
    args.monitor_cmd = args.monitor_cmd.replace("\\\\", "/").replace("\\", "/")
    args.claude_cmd = args.claude_cmd.replace("\\\\", "/").replace("\\", "/")
    if args.initial_cmd:
        args.initial_cmd = args.initial_cmd.replace("\\\\", "/").replace("\\", "/")

    log(f"project_dir = {project_dir}")
    log(f"claude_cmd  = {args.claude_cmd}")
    log(f"monitor_cmd = {args.monitor_cmd}")

    if args.clean_on_start:
        for f in [state_file.pause_path, state_file.resume_path]:
            try:
                f.unlink()
            except FileNotFoundError:
                pass

    # 1) 后台启动 monitor
    log(">> launching monitor (background)")
    monitor_args = shlex.split(args.monitor_cmd)
    log(f"   monitor argv = {monitor_args}")
    monitor = subprocess.Popen(
        monitor_args, shell=False, stdin=subprocess.DEVNULL,
        stdout=sys.stdout, stderr=sys.stderr,
    )
    log(f"   monitor pid = {monitor.pid}")

    try:
        for round_num in range(1, args.max_rounds + 1):
            log(f"=== ROUND {round_num} ===")

            # 清理本轮信号
            for f in [state_file.pause_path, state_file.resume_path]:
                try:
                    f.unlink()
                except FileNotFoundError:
                    pass

            # 启动 claude
            if round_num == 1 and args.initial_cmd:
                cmd = args.initial_cmd
            else:
                cmd = args.claude_cmd
            log(f">> launching claude: {cmd[:120]}")
            claude_args = shlex.split(cmd)
            t0 = time.time()
            try:
                rc = subprocess.call(claude_args, shell=False, stdin=sys.stdin,
                                     stdout=sys.stdout, stderr=sys.stderr)
            except KeyboardInterrupt:
                log("interrupted by user")
                monitor.terminate()
                return 130
            elapsed = time.time() - t0
            log(f"claude exited rc={rc} after {elapsed:.0f}s")

            if monitor.poll() is not None:
                log(f"!! monitor died rc={monitor.returncode}")
                break

            if not state_file.pause_path.exists():
                log("no PAUSE.flag → claude exited cleanly, DONE")
                break

            log("PAUSE.flag detected, waiting for RESUME.flag...")
            ok = wait_for_resume(state_file, timeout=args.wait_timeout)
            if not ok:
                log("RESUME did not appear within timeout, EXIT")
                return 1
            log("++ RESUME.flag detected, next round: claude --continue")

    finally:
        log("cleaning up monitor...")
        monitor.terminate()
        try:
            monitor.wait(timeout=5)
        except subprocess.TimeoutExpired:
            monitor.kill()

    log("ALL DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
