"""quota_guard.monitor —— 自适应监控守护进程。

核心策略（结合 AI QuotaGuard + PESS + 我 V3）：
1. 双模式频率：梯度（按 % 阈值）+ 速率（按 burn_rate 预测耗尽时间），取两者中更短
2. 真刷新判定：end_time 跳变（agent-2 调研金标准）
3. 状态文件：quota_state.json（结构化）+ PAUSE.flag（哨兵）
4. Burn rate 跟踪：指数移动平均（EMA）平滑
"""
from __future__ import annotations
import argparse
import json
import os
import signal
import sys
import time
from pathlib import Path
from typing import Optional

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

from .state import QuotaState, QuotaSnapshot, StateFile
from .providers import MinMaxProvider



# ---- 启动期卫生：清代理 + 强制 utf-8 stdout -------------------------------
for _k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
          "http_proxy", "https_proxy", "all_proxy", "SOCKS_PROXY"):
    os.environ.pop(_k, None)


# ---- 频率策略 ------------------------------------------------------------

# 梯度模式（按 % 阈值）——简单可靠
GRADIENT_INTERVALS = [
    (70, 300),   # > 70%：5 min
    (40, 120),   # > 40%：2 min
    (20, 60),    # > 20%：1 min
    (10, 20),    # > 10%：20s
    (0, 5),      # ≤ 10%：5s（紧急）
]


def interval_gradient(remains_pct: float) -> int:
    """按梯度策略返回间隔（秒）。"""
    for threshold, sec in GRADIENT_INTERVALS:
        if remains_pct > threshold:
            return sec
    return 5


# 速率模式（按 burn_rate 预测耗尽时间）——更智能
def interval_rate(burn_rate_per_min: float, remains_pct: float) -> int:
    """按消耗速率返回间隔（秒）。"""
    if burn_rate_per_min <= 0.01:
        return 120  # 不知道消耗速率时保守 2 min
    minutes_to_empty = remains_pct / burn_rate_per_min
    if minutes_to_empty > 60:      return 300   # > 1h：5 min
    elif minutes_to_empty > 10:    return 60    # > 10 min：1 min
    elif minutes_to_empty > 2:     return 15    # > 2 min：15 s
    else:                          return 5     # 危险：5 s


def compute_interval(state: QuotaState) -> int:
    """双模式叠加：取 min。"""
    g = interval_gradient(state.remains_pct)
    r = interval_rate(state.burn_rate_per_min, state.remains_pct)
    return min(g, r)


# ---- 监控主循环 ----------------------------------------------------------

def log(msg: str, on_log=None) -> None:
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    if on_log:
        on_log(line)


def run_monitor(
    provider: MinMaxProvider,
    state_file: StateFile,
    low_threshold: float = 15.0,
    critical_threshold: float = 5.0,
    on_log=None,
) -> int:
    """阻塞运行。Ctrl+C / SIGTERM 退出。"""
    log(f"=== QuotaGuard monitor start (provider={provider.name}) ===", on_log)
    log(f"policy: gradient={GRADIENT_INTERVALS} | low<{low_threshold}% critical<={critical_threshold}%", on_log)
    log(f"state file: {state_file.state_path}", on_log)
    log(f"pause file: {state_file.pause_path}", on_log)

    # 启动时清陈旧哨兵
    if state_file.clear_pause():
        log("cleared stale pause file", on_log)

    state = state_file.read_state() or QuotaState()
    state.low_threshold = low_threshold
    state.critical_threshold = critical_threshold
    state.consecutive_low_count = 0
    state.consecutive_critical_count = 0

    running = True
    def stop(*_):
        nonlocal running
        running = False
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    while running:
        snap = provider.query()
        if snap.error:
            log(f"ERR query: {snap.error}", on_log)
            # 查询失败不触发 STOP（fail-safe），保守用 60s
            time.sleep(60)
            continue

        # 构造新 state
        new_state = QuotaState.from_snapshot(snap, low_threshold, critical_threshold)
        # 保留 burn rate 累积
        new_state.burn_rate_per_min = state.burn_rate_per_min
        new_state.samples = state.samples
        new_state.window_start_at = state.window_start_at
        new_state.estimated_empty_at = state.estimated_empty_at
        # end_time 跳变检查：保留 STOP 触发时的 end_time
        if state.last_end_time_before_stop:
            new_state.last_end_time_before_stop = state.last_end_time_before_stop
        state = new_state
        state.record_sample()
        # 写状态文件（原子）
        state_file.write_state(state)

        # 决定下一次间隔
        interval = compute_interval(state)
        band = "🟢" if state.remains_pct >= state.low_threshold else ("🟡" if state.remains_pct > state.critical_threshold else "🔴")
        empty_str = f"  empty~{(state.estimated_empty_at - time.time())/60:.0f}min" if state.estimated_empty_at else ""
        burn_str = f"  burn={state.burn_rate_per_min:.1f}%/min" if state.burn_rate_per_min > 0.01 else ""
        log(f"{band} {state.remains_pct:5.1f}%  boost=×{state.boost}  next={interval}s{empty_str}{burn_str}", on_log)

        # 临界判断 + STOP 信号
        if state.remains_pct <= critical_threshold:
            if not state_file.is_paused():
                state.mark_stop()
                state_file.write_pause(reason=f"5h remaining {state.remains_pct}% <= {critical_threshold}%")
                # 在 PAUSE.flag 里同时写触发时刻、剩余%、阈值（让 hook 显示完整）
                from pathlib import Path as _P
                _P(state_file.pause_path).write_text(
                    json.dumps({
                        "triggered_at": time.time(),
                        "reason": f"5h remaining {state.remains_pct}% <= {critical_threshold}%",
                        "remains_percent": state.remains_pct,
                        "threshold": critical_threshold,
                        "end_time_ms": state.end_time_ms,
                    }, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                log(f"🛑 STOP emitted (≤ {critical_threshold}%)", on_log)
                # 重置 critical 计数（避免重复写）
            # 已经 STOP：检查是否真刷新
            if state.is_real_refresh():
                state_file.clear_pause()
                state_file.write_resume(time.time())
                log(f"✅ REAL REFRESH confirmed (end_time jumped), pause cleared", on_log)
        else:
            # 健康：清掉 pause（兜底，万一上次异常退出）
            if state_file.is_paused():
                state_file.clear_pause()
                log("⚠️  pause cleared (quota recovered above critical)", on_log)

        # 分片 sleep（响应 SIGTERM）
        for _ in range(interval):
            if not running:
                break
            time.sleep(1)

    log("monitor stopped", on_log)
    return 0


# ---- CLI ----------------------------------------------------------------

def cmd_monitor(args: argparse.Namespace) -> int:
    env_file = Path(args.env) if args.env else Path.cwd() / ".env"
    provider = MinMaxProvider.from_env(env_file=env_file if env_file.exists() else None)

    state_file = StateFile(
        state_path=Path(args.state_file).expanduser(),
        pause_path=Path(args.pause_file).expanduser(),
        resume_path=Path(args.resume_file).expanduser() if args.resume_file else None,
    )

    return run_monitor(
        provider=provider,
        state_file=state_file,
        low_threshold=args.low,
        critical_threshold=args.critical,
    )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="quota_guard monitor", description="QuotaGuard 自适应监控守护进程")
    p.add_argument("--env", default=None, help=".env 路径")
    p.add_argument("--state-file", default="~/.quotaguard/quota_state.json", help="状态文件路径")
    p.add_argument("--pause-file", default="~/.quotaguard/PAUSE.flag", help="哨兵文件路径")
    p.add_argument("--resume-file", default="~/.quotaguard/RESUME.flag", help="恢复信号文件")
    p.add_argument("--low", type=float, default=15.0, help="警告阈值（%）")
    p.add_argument("--critical", type=float, default=5.0, help="硬中断阈值（%）")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return cmd_monitor(args)


if __name__ == "__main__":
    sys.exit(main())
