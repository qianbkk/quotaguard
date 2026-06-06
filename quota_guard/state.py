"""quota_guard.state —— 状态数据 + 哨兵文件管理。

设计原则（结合 AI QuotaGuard 设计 + PESS SignalFile + 我之前的 V3 monitor）：
- state.json：完整结构化状态（消费 AI 可提取，监控可写入，恢复可读取）
- PAUSE.flag：哨兵文件（存在=STOP，缺失=GO）—— 最快的跨进程通信
- before/after snapshot：审计追踪
"""
from __future__ import annotations
import json
import time
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class QuotaSnapshot:
    """某次轮询得到的完整 API 响应快照。"""
    fetched_at: str                       # ISO-8601 UTC
    provider: str = "minimax"
    model_name: str = "general"
    remains_pct: Optional[float] = None   # 5h 桶剩余百分比（0-100）
    remains_time_ms: Optional[int] = None # 距 5h 重置的毫秒数
    end_time_ms: Optional[int] = None     # 5h 桶固定结束时间戳
    start_time_ms: Optional[int] = None
    status: Optional[int] = None          # 1=consuming / 3=full
    boost_permille: Optional[int] = None  # 双倍活动：2000
    weekly_pct: Optional[float] = None
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class QuotaState:
    """全局配额状态 —— 由 monitor 写入，被 hook/proxy/orchestrator 读取。"""
    # 当前值
    remains_pct: float = 100.0
    remains_time_ms: int = 0
    weekly_pct: float = 100.0
    boost: float = 1.0
    model_name: str = "general"

    # 阈值判断
    low_threshold: float = 15.0
    critical_threshold: float = 3.0
    is_low: bool = False
    is_critical: bool = False

    # burn rate（消耗速率）
    burn_rate_per_min: float = 0.0        # tokens/分钟（数值无单位，仅作参考）
    estimated_empty_at: Optional[float] = None  # Unix timestamp
    samples: List[Dict[str, Any]] = field(default_factory=list)  # 最近 10 次采样

    # 窗口信息
    window_start_at: Optional[float] = None
    window_end_at: Optional[float] = None
    end_time_ms: Optional[int] = None

    # 监控元数据
    last_check_at: float = 0.0
    consecutive_low_count: int = 0
    consecutive_critical_count: int = 0

    # 真刷新判定（agent-2 金标准）
    last_end_time_before_stop: Optional[int] = None  # STOP 触发时的 end_time_ms
    refresh_confirmed: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_snapshot(cls, snap: QuotaSnapshot,
                      low_threshold: float = 15.0,
                      critical_threshold: float = 3.0) -> "QuotaState":
        s = cls()
        s.low_threshold = low_threshold
        s.critical_threshold = critical_threshold
        s.remains_pct = snap.remains_pct or 0.0
        s.remains_time_ms = snap.remains_time_ms or 0
        s.weekly_pct = snap.weekly_pct or 100.0
        s.boost = (snap.boost_permille or 1000) / 1000.0
        s.model_name = snap.model_name
        s.end_time_ms = snap.end_time_ms
        if snap.end_time_ms:
            s.window_end_at = snap.end_time_ms / 1000.0
        if snap.start_time_ms:
            s.window_start_at = snap.start_time_ms / 1000.0
        s.last_check_at = time.time()
        s.is_low = s.remains_pct < low_threshold
        s.is_critical = s.remains_pct <= critical_threshold
        if s.is_low:
            s.consecutive_low_count += 1
        if s.is_critical:
            s.consecutive_critical_count += 1
        return s

    def update_burn_rate(self, alpha: float = 0.3) -> None:
        """指数移动平均 burn rate（per minute），基于 samples 列表。"""
        if len(self.samples) < 2:
            self.burn_rate_per_min = 0.0
            return
        # 取最近 5 个采样计算线性回归斜率
        recent = self.samples[-5:]
        if len(recent) < 2:
            return
        t0 = recent[0]["at"]
        p0 = recent[0]["pct"]
        t1 = recent[-1]["at"]
        p1 = recent[-1]["pct"]
        dt_min = (t1 - t0) / 60.0
        if dt_min <= 0:
            return
        # %/min（消耗为正）
        new_rate = max(0.0, (p0 - p1) / dt_min)
        # EMA 平滑
        if self.burn_rate_per_min == 0:
            self.burn_rate_per_min = new_rate
        else:
            self.burn_rate_per_min = alpha * new_rate + (1 - alpha) * self.burn_rate_per_min
        # 预计耗尽时间
        if self.burn_rate_per_min > 0.01:
            self.estimated_empty_at = time.time() + (self.remains_pct / self.burn_rate_per_min) * 60.0

    def record_sample(self) -> None:
        """记录一次采样（保留最近 10 个）。"""
        self.samples.append({
            "at": time.time(),
            "pct": self.remains_pct,
            "is_critical": self.is_critical,
        })
        if len(self.samples) > 10:
            self.samples = self.samples[-10:]
        self.update_burn_rate()

    def is_real_refresh(self, drift_ms: int = 60_000) -> bool:
        """真刷新判定（agent-2 金标准）：end_time 跳 ≥ 5h - drift。"""
        if self.refresh_confirmed or not self.end_time_ms or not self.last_end_time_before_stop:
            return False
        delta = self.end_time_ms - self.last_end_time_before_stop
        if delta >= 5 * 3600 * 1000 - drift_ms and self.remains_pct >= self.critical_threshold:
            self.refresh_confirmed = True
            return True
        return False

    def mark_stop(self) -> None:
        """记录 STOP 触发时刻的 end_time，用于后续真刷新判定。"""
        if self.end_time_ms:
            self.last_end_time_before_stop = self.end_time_ms
        self.refresh_confirmed = False


class StateFile:
    """quota_state.json 读写 + 哨兵文件管理。"""
    def __init__(self, state_path: Path, pause_path: Path, resume_path: Optional[Path] = None):
        self.state_path = Path(state_path)
        self.pause_path = Path(pause_path)
        self.resume_path = Path(resume_path) if resume_path else self.pause_path.parent / "RESUME.flag"

    def write_state(self, state: QuotaState) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        # 原子写：先写 .tmp 再 rename
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(state.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(self.state_path)

    def read_state(self) -> Optional[QuotaState]:
        if not self.state_path.exists():
            return None
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            return QuotaState(**data)
        except (OSError, json.JSONDecodeError, TypeError):
            return None

    def write_pause(self, reason: str = "quota critical") -> None:
        self.pause_path.parent.mkdir(parents=True, exist_ok=True)
        self.pause_path.write_text(
            json.dumps({
                "ts": time.time(),
                "reason": reason,
            }, ensure_ascii=False),
            encoding="utf-8",
        )

    def clear_pause(self) -> bool:
        if self.pause_path.exists():
            self.pause_path.unlink()
            return True
        return False

    def is_paused(self) -> bool:
        return self.pause_path.exists()

    def write_resume(self, refreshed_at: float) -> None:
        self.resume_path.parent.mkdir(parents=True, exist_ok=True)
        self.resume_path.write_text(
            json.dumps({"refreshed_at": refreshed_at, "ts": time.time()}, ensure_ascii=False),
            encoding="utf-8",
        )

    def clear_resume(self) -> bool:
        if self.resume_path.exists():
            self.resume_path.unlink()
            return True
        return False
