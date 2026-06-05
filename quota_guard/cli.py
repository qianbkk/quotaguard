"""quota_guard.cli —— 统一 CLI 入口。

子命令：
  status    打印当前 quota_state
  check     单次查询
  monitor   后台守护监控
  proxy     启动本地代理（其他 Agent 用）
  resume    恢复管理器（orchestrator）
"""
from __future__ import annotations
import argparse
import json
import os
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

for _k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
          "http_proxy", "https_proxy", "all_proxy"):
    os.environ.pop(_k, None)

from . import monitor, proxy, resume
from .state import StateFile
from .providers import MinMaxProvider


def cmd_status(args: argparse.Namespace) -> int:
    sf = StateFile(
        state_path=Path(args.state_file).expanduser(),
        pause_path=Path(args.pause_file).expanduser(),
    )
    state = sf.read_state()
    if not state:
        print("[status] no state file. Run 'quota_guard monitor' first.", file=sys.stderr)
        return 1
    print(json.dumps(state.to_dict(), ensure_ascii=False, indent=2))
    if sf.is_paused():
        print("\n[status] PAUSE.flag exists (quota critical)", file=sys.stderr)
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    env_file = Path(args.env) if args.env and Path(args.env).exists() else None
    p = MinMaxProvider.from_env(env_file=env_file)
    snap = p.query()
    if snap.error:
        print(f"ERROR: {snap.error}", file=sys.stderr)
        return 1
    print(json.dumps(snap.to_dict(), ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="quota_guard")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_status = sub.add_parser("status", help="打印当前状态")
    p_status.add_argument("--state-file", default="~/.quotaguard/quota_state.json")
    p_status.add_argument("--pause-file", default="~/.quotaguard/PAUSE.flag")
    p_status.set_defaults(func=cmd_status)

    p_check = sub.add_parser("check", help="单次查询")
    p_check.add_argument("--env", default=None)
    p_check.set_defaults(func=cmd_check)

    # 复用 monitor / proxy / resume 的子命令
    p_monitor = sub.add_parser("monitor", help="启动监控守护进程")
    p_monitor.add_argument("--env", default=None)
    p_monitor.add_argument("--state-file", default="~/.quotaguard/quota_state.json")
    p_monitor.add_argument("--pause-file", default="~/.quotaguard/PAUSE.flag")
    p_monitor.add_argument("--resume-file", default="~/.quotaguard/RESUME.flag")
    p_monitor.add_argument("--low", type=float, default=15.0)
    p_monitor.add_argument("--critical", type=float, default=5.0)
    p_monitor.set_defaults(func=lambda a: monitor.cmd_monitor(a))

    p_proxy = sub.add_parser("proxy", help="启动本地代理")
    p_proxy.add_argument("--host", default="127.0.0.1")
    p_proxy.add_argument("--port", type=int, default=8080)
    p_proxy.add_argument("--state-file", default="~/.quotaguard/quota_state.json")
    p_proxy.add_argument("--real-base", default="https://api.minimaxi.com")
    p_proxy.set_defaults(func=lambda a: proxy.main.__wrapped__() if hasattr(proxy.main, '__wrapped__') else None)

    p_resume = sub.add_parser("resume", help="恢复管理器")
    p_resume.add_argument("--monitor-cmd", required=True)
    p_resume.add_argument("--claude-cmd", default="claude --continue")
    p_resume.add_argument("--initial-cmd", default=None)
    p_resume.add_argument("--state-file", default="~/.quotaguard/quota_state.json")
    p_resume.add_argument("--pause-file", default="~/.quotaguard/PAUSE.flag")
    p_resume.add_argument("--resume-file", default="~/.quotaguard/RESUME.flag")
    p_resume.add_argument("--max-rounds", type=int, default=50)
    p_resume.add_argument("--wait-timeout", type=float, default=0)
    p_resume.add_argument("--clean-on-start", action="store_true")
    p_resume.set_defaults(func=lambda a: _run_resume(a))

    def _run_resume(a):
        argv = [
            "--monitor-cmd", a.monitor_cmd,
            "--claude-cmd", a.claude_cmd,
            "--state-file", a.state_file,
            "--pause-file", a.pause_file,
            "--resume-file", a.resume_file,
            "--max-rounds", str(a.max_rounds),
            "--wait-timeout", str(a.wait_timeout),
        ]
        if a.initial_cmd:
            argv += ["--initial-cmd", a.initial_cmd]
        if a.clean_on_start:
            argv += ["--clean-on-start"]
        return resume.main(argv)

    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    sys.exit(main())
