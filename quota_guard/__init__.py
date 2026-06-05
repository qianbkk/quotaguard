"""QuotaGuard —— MiniMax 配额守护 + 长任务自动暂停/恢复。

四层架构：
  1. monitor.py：自适应监控（双模式频率）
  2. interceptor.py + proxy.py：拦截层
  3. resume.py：恢复管理器
  4. cli.py + start_quotaguard.*：启动器

公共 API：
    from quota_guard import QuotaState, StateFile, MinMaxProvider
"""
from .state import QuotaState, QuotaSnapshot, StateFile
from .providers import MinMaxProvider

__version__ = "0.1.0"

__all__ = ["QuotaState", "QuotaSnapshot", "StateFile", "MinMaxProvider"]
