# -*- coding: utf-8 -*-
"""本地假影刀开放 API：没有影子 RPA 环境时，用它把"智能体 → RPA"整条链路跑通。

它按 `agentrpa_bridge/yingdao_api.py` 里的 `DEFAULT_PATHS` 实现同样的路由：

    POST /oapi/dispatch/v1/app/list        列应用
    POST /oapi/dispatch/v1/app/param/list  查应用入参
    POST /oapi/dispatch/v1/job/start       启动任务
    GET  /oapi/dispatch/v1/job/detail      查任务（约 1.5 秒后变 success）
    POST /oapi/dispatch/v1/client/list     机器人在线状态

启动：
    python examples/mock_yingdao_server.py --port 8767
然后：
    $env:YINGDAO_API_BASE = "http://127.0.0.1:8767"
    $env:YINGDAO_ACCESS_KEY_ID = "ak_mock"; $env:YINGDAO_ACCESS_KEY_SECRET = "sk_mock"
    python agentrpa_bridge/yingdao_mcp_server.py --selftest
"""
from __future__ import annotations

import argparse
import json
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

APPS = [
    {"id": "app_invoice", "name": "发票批量下载", "description": "登录税局 → 下载当月发票 → 归档"},
    {"id": "app_reconcile", "name": "银行对账单核对", "description": "下载流水 → 与 ERP 比对 → 出差异表"},
]

PARAMS = {
    "app_invoice": [
        {"name": "月份", "type": "string", "required": True, "example": "2026-11"},
        {"name": "税号", "type": "string", "required": True, "example": "91310000XXXX"},
    ],
    "app_reconcile": [
        {"name": "账户", "type": "string", "required": True},
        {"name": "起始日期", "type": "string", "required": False},
    ],
}

CLIENTS = [
    {"id": "bot-01", "name": "财务机器人-01", "online": True, "busy": False},
    {"id": "bot-02", "name": "财务机器人-02", "online": True, "busy": True},
]


class State:
    def __init__(self):
        self.lock = threading.Lock()
        self.jobs = {}
        self.started = time.time()

    def start_job(self, app_id: str, params: dict, client_id: str) -> dict:
        job_id = "job_" + uuid.uuid4().hex[:10]
        with self.lock:
            self.jobs[job_id] = {"app_id": app_id, "params": params, "client_id": client_id,
                                 "started_at": time.time()}
        return {"jobId": job_id, "status": "queued"}

    def query_job(self, job_id: str) -> dict:
        with self.lock:
            job = self.jobs.get(job_id)
        if not job:
            return {"jobId": job_id, "status": "not_found"}
        elapsed = time.time() - job["started_at"]
        if elapsed < 0.4:
            return {"jobId": job_id, "status": "queued", "progress": 0}
        if elapsed < 1.2:
            return {"jobId": job_id, "status": "running", "progress": 45}
        return {
            "jobId": job_id,
            "status": "success",
            "progress": 100,
            "duration_ms": int(elapsed * 1000),
            "appId": job["app_id"],
            "output": {
                "rows": 128 if job["app_id"] == "app_invoice" else 37,
                "file": r"D:\rpa\out\%s_%s.xlsx" % (job["app_id"], job_id),
                "params": job["params"],
            },
        }


STATE = State()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        return

    def _send(self, obj, code=200):
        raw = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}

    def do_POST(self):
        path = self.path.split("?")[0]
        body = self._body()
        if not self.headers.get("x-access-key-id"):
            self._send({"code": 401, "message": "missing x-access-key-id"}, 401)
            return
        if path.endswith("/app/list"):
            keyword = (body.get("keyword") or "").strip()
            apps = [a for a in APPS if not keyword or keyword in a["name"] or keyword in a["id"]]
            self._send({"code": 0, "data": {"apps": apps, "total": len(apps)}})
        elif path.endswith("/app/param/list"):
            app_id = body.get("appId") or ""
            self._send({"code": 0, "data": {"appId": app_id,
                                            "params": PARAMS.get(app_id, [])}})
        elif path.endswith("/job/start"):
            app_id = body.get("appId") or ""
            if app_id not in PARAMS:
                self._send({"code": 4004, "message": "应用不存在或未授权"})
                return
            self._send({"code": 0, "data": STATE.start_job(
                app_id, body.get("params") or {}, body.get("clientId") or "bot-01")})
        elif path.endswith("/client/list"):
            self._send({"code": 0, "data": {"clients": CLIENTS}})
        else:
            self._send({"code": 404, "message": "not found"}, 404)

    def do_GET(self):
        path, _, query = self.path.partition("?")
        params = {}
        for pair in query.split("&"):
            if "=" in pair:
                key, val = pair.split("=", 1)
                params[key] = val
        if path.endswith("/job/detail"):
            self._send({"code": 0, "data": STATE.query_job(params.get("jobId", ""))})
        else:
            self._send({"code": 404, "message": "not found"}, 404)


def serve(port: int = 0):
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def main():
    parser = argparse.ArgumentParser(description="本地假影刀开放 API")
    parser.add_argument("--port", type=int, default=8767)
    args = parser.parse_args()
    httpd = serve(args.port)
    print(f"假影刀开放 API: http://127.0.0.1:{httpd.server_address[1]}  "
          f"apps={[a['id'] for a in APPS]}")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        httpd.shutdown()


if __name__ == "__main__":
    main()
