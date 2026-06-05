"""quota_guard.proxy —— 本地 API 代理，给不支持 Hook 的 Agent（Codex、Cursor 等）用。

行为：
  - 读 quota_state.json
  - 配额健康 → 透明转发到真实 MiniMax API
  - 配额临界 → 返 429 + Retry-After（让 agent 自己重试或停）
  - 状态文件不可读 → 透明转发（fail-open）

启动：
    python -m quota_guard proxy --port 8080
    # Agent 配置：ANTHROPIC_BASE_URL=http://127.0.0.1:8080
"""
from __future__ import annotations
import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Optional

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

# 避免在 import requests 前有代理
for _k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
          "http_proxy", "https_proxy", "all_proxy"):
    os.environ.pop(_k, None)

import requests
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

DEFAULT_STATE_FILE = "~/.quotaguard/quota_state.json"
REAL_BASE = "https://api.minimaxi.com"


def is_critical(state_path: Path) -> tuple[bool, Optional[int]]:
    """检查配额是否临界；返 (is_critical, refresh_in_seconds)。"""
    if not state_path.exists():
        return False, None
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        return False, None
    if not state.get("is_critical"):
        return False, None
    end_at = state.get("window_end_at")
    refresh_in = None
    if end_at and end_at > time.time():
        refresh_in = int(end_at - time.time())
    return True, refresh_in


def build_app(state_file: Path, real_base: str = REAL_BASE) -> FastAPI:
    app = FastAPI(title="QuotaGuard Proxy")

    @app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
    async def proxy(path: str, request: Request):
        # 配额检查
        critical, refresh_in = is_critical(state_file)
        if critical:
            retry_after = max(0, refresh_in) if refresh_in else 60
            return JSONResponse(
                status_code=429,
                headers={"Retry-After": str(retry_after)},
                content={
                    "error": {
                        "type": "quota_exhausted",
                        "message": f"MiniMax quota critical, retry after {retry_after}s",
                        "retry_after": retry_after,
                    }
                },
            )

        # 透明转发
        target_url = f"{real_base}/{path}"
        if request.url.query:
            target_url += f"?{request.url.query}"

        headers = dict(request.headers)
        # 移除 hop-by-hop 头
        for h in ("host", "content-length", "connection"):
            headers.pop(h, None)

        body = await request.body()
        try:
            resp = requests.request(
                method=request.method,
                url=target_url,
                headers=headers,
                data=body,
                timeout=300,
                stream=False,
            )
            return Response(
                content=resp.content,
                status_code=resp.status_code,
                headers={k: v for k, v in resp.headers.items()
                         if k.lower() not in ("content-encoding", "transfer-encoding", "connection")},
            )
        except requests.RequestException as e:
            return JSONResponse(
                status_code=502,
                content={"error": {"type": "proxy_error", "message": str(e)}},
            )

    @app.get("/quotaguard/status")
    async def status():
        if state_file.exists():
            try:
                return json.loads(state_file.read_text(encoding="utf-8"))
            except Exception as e:
                return {"error": str(e)}
        return {"error": "no state file"}

    return app


def main() -> int:
    ap = argparse.ArgumentParser(prog="quota_guard proxy")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--state-file", default=DEFAULT_STATE_FILE)
    ap.add_argument("--real-base", default=REAL_BASE)
    args = ap.parse_args()

    state_path = Path(args.state_file).expanduser()
    app = build_app(state_path, args.real_base)

    import uvicorn
    print(f"[QuotaGuard proxy] listening on http://{args.host}:{args.port}", flush=True)
    print(f"[QuotaGuard proxy] state file: {state_path}", flush=True)
    print(f"[QuotaGuard proxy] real API: {args.real_base}", flush=True)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
