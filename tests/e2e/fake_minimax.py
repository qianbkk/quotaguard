#!/usr/bin/env python3
"""Fake MiniMax API server —— 端到端仿真用。

支持 end_time 跳变（agent-2 调研的金标准）。
"""
import argparse
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer


class FakeMiniMax:
    FIVE_HOURS_MS = 5 * 3600 * 1000

    def __init__(self, decline, floor, refresh, boost):
        self.decline = decline
        self.floor = floor
        self.refresh = refresh
        self.boost = boost
        self.start = time.time()
        self.lock = threading.Lock()
        self.remains_pct = 100
        self.window_start = time.time()
        self.window_end = self.window_start + self.FIVE_HOURS_MS / 1000
        self.just_refreshed = True

    def snapshot(self):
        with self.lock:
            if self.refresh and self.remains_pct <= self.floor and (time.time() - self.start) >= self.refresh:
                self.just_refreshed = True
                self.start = time.time()
                self.window_start = self.window_end
                self.window_end = self.window_start + self.FIVE_HOURS_MS / 1000

            if getattr(self, "just_refreshed", False):
                self.remains_pct = 100
                self.just_refreshed = False
                status = 3
                remains_time = self.FIVE_HOURS_MS
            else:
                self.remains_pct = max(self.floor, self.remains_pct - self.decline)
                if self.remains_pct <= self.floor:
                    status = 1
                    remains_time = 0
                else:
                    status = 1
                    remains_time = max(0, (self.window_end - time.time()) * 1000)

            return {
                "base_resp": {"status_code": 0, "status_msg": "success"},
                "model_remains": [{
                    "model_name": "general",
                    "start_time": int(self.window_start * 1000),
                    "end_time": int(self.window_end * 1000),
                    "remains_time": int(remains_time),
                    "current_interval_status": status,
                    "current_interval_remaining_percent": self.remains_pct,
                    "interval_boost_permille": self.boost,
                    "current_weekly_remaining_percent": 100,
                    "weekly_boost_permille": self.boost,
                }],
            }


fake = None


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        snap = fake.snapshot()
        body = json.dumps(snap, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass


def main():
    global fake
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=18765)
    ap.add_argument("--decline", type=int, default=60)
    ap.add_argument("--floor", type=int, default=0)
    ap.add_argument("--refresh", type=float, default=0)
    ap.add_argument("--boost", type=int, default=0)
    args = ap.parse_args()
    fake = FakeMiniMax(args.decline, args.floor, args.refresh, args.boost)
    srv = HTTPServer(("127.0.0.1", args.port), Handler)
    print(f"[fake-minimax] 127.0.0.1:{args.port} decline={args.decline} refresh={args.refresh}s")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
