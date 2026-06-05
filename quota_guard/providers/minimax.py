"""quota_guard.providers.minimax —— MiniMax 适配器。

复用 A6.1/PESS QuotaProvider 接口的轻量化版本（不依赖 dataclass QuotaModel/QuotaStatus，
直接返回 QuotaSnapshot 供 QuotaState 构造）。
"""
from __future__ import annotations
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests

from ..state import QuotaSnapshot

# 与 PESS / minimax_quick.py 一致的 key 清洗规则
_KEY_GARBAGE = re.compile(r"[\s​-‍﻿\"'`‘’“”\"]")


def _sanitize_key(raw: str) -> str:
    return _KEY_GARBAGE.sub("", raw).strip()


class MinMaxProvider:
    name = "minimax"
    ENDPOINT = "https://api.minimaxi.com/v1/token_plan/remains"

    def __init__(self, api_key: Optional[str] = None, endpoint: str = ENDPOINT, timeout: int = 10):
        if api_key is None:
            api_key = os.getenv("MINIMAX_API_KEY", "")
        if not api_key:
            raise ValueError("MINIMAX_API_KEY not set")
        self.api_key = _sanitize_key(api_key)
        self.endpoint = endpoint
        self.timeout = timeout

    @classmethod
    def from_env(cls, env_file: Optional[Path] = None) -> "MinMaxProvider":
        if env_file and Path(env_file).exists():
            try:
                from dotenv import load_dotenv
                load_dotenv(env_file, override=True)
            except ImportError:
                pass
        return cls()

    def query(self) -> QuotaSnapshot:
        """单次查询，返回 QuotaSnapshot。错误时 error 字段填值，不 throw。"""
        fetched_at = datetime.now(timezone.utc).isoformat()
        try:
            r = requests.get(
                self.endpoint,
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                timeout=self.timeout,
            )
            data = r.json()
        except requests.RequestException as e:
            return QuotaSnapshot(fetched_at=fetched_at, error=f"network: {e}")
        except ValueError as e:
            return QuotaSnapshot(fetched_at=fetched_at, error=f"decode: {e}")

        if not isinstance(data, dict):
            return QuotaSnapshot(fetched_at=fetched_at, error="non-dict payload")

        base = data.get("base_resp", {}) or {}
        if base.get("status_code") != 0:
            return QuotaSnapshot(
                fetched_at=fetched_at,
                error=f"api status_code={base.get('status_code')} {base.get('status_msg', '')}",
            )

        for m in (data.get("model_remains") or []):
            if m.get("model_name") == "general":
                return QuotaSnapshot(
                    fetched_at=fetched_at,
                    provider=self.name,
                    model_name="general",
                    remains_pct=m.get("current_interval_remaining_percent"),
                    remains_time_ms=m.get("remains_time"),
                    end_time_ms=m.get("end_time"),
                    start_time_ms=m.get("start_time"),
                    status=m.get("current_interval_status"),
                    boost_permille=m.get("interval_boost_permille"),
                    weekly_pct=m.get("current_weekly_remaining_percent"),
                )
        return QuotaSnapshot(fetched_at=fetched_at, error="no 'general' model in response")
