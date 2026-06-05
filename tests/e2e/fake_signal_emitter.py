#!/usr/bin/env python3
"""Fake signal emitter —— 模拟 monitor 写 PAUSE/RESUME 信号（不真调 API）。"""
import argparse
import json
import sys
import time
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--state-file", default=None, help="完整 state 路径（与 --base-dir 二选一）")
    ap.add_argument("--pause-file", default=None)
    ap.add_argument("--resume-file", default=None)
    ap.add_argument("--base-dir", default=None, help="基础目录，自动拼 quota_state.json / PAUSE.flag / RESUME.flag")
    ap.add_argument("--pause-after", type=int, default=10)
    ap.add_argument("--resume-after", type=int, default=10)
    ap.add_argument("--max-cycles", type=int, default=10)
    ap.add_argument("--duration", type=int, default=0)
    args = ap.parse_args()

    if args.base_dir:
        base = Path(args.base_dir)
        base.mkdir(parents=True, exist_ok=True)
        state_path = base / "quota_state.json"
        pause_path = base / "PAUSE.flag"
        resume_path = base / "RESUME.flag"
    else:
        state_path = Path(args.state_file)
        pause_path = Path(args.pause_file)
        resume_path = Path(args.resume_file)
    state_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"[fake-emitter] state={state_path} pause={pause_path.name} resume={resume_path.name}")
    start = time.time()
    r = 0
    while r < args.max_cycles:
        r += 1
        print(f"[fake-emitter] cycle {r}: wait {args.pause_after}s before PAUSE")
        time.sleep(args.pause_after)
        if args.duration and (time.time() - start) > args.duration:
            break
        # 写 state（模拟 quota critical）
        state_path.write_text(json.dumps({
            "remains_pct": 0.0,
            "is_critical": True,
            "window_end_at": time.time() + 10,
        }, ensure_ascii=False), encoding="utf-8")
        # 写 PAUSE
        pause_path.write_text(json.dumps({"ts": time.time(), "reason": "fake"}, ensure_ascii=False), encoding="utf-8")
        print(f"[fake-emitter] ++ wrote PAUSE")
        print(f"[fake-emitter] cycle {r}: wait {args.resume_after}s before RESUME")
        time.sleep(args.resume_after)
        if args.duration and (time.time() - start) > args.duration:
            break
        # 删 PAUSE + 写 state 健康 + 写 RESUME
        pause_path.unlink(missing_ok=True)
        state_path.write_text(json.dumps({
            "remains_pct": 100.0,
            "is_critical": False,
            "window_end_at": time.time() + 5 * 3600,
        }, ensure_ascii=False), encoding="utf-8")
        resume_path.write_text(json.dumps({"ts": time.time()}, ensure_ascii=False), encoding="utf-8")
        print(f"[fake-emitter] ++ wrote RESUME, cleared PAUSE")
    print(f"[fake-emitter] DONE")


if __name__ == "__main__":
    main()
