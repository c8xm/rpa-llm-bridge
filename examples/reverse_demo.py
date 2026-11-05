# -*- coding: utf-8 -*-
"""反方向端到端演示：没有影刀系统也能跑。

模拟的是一条真实的链路：
    "智能体"（这里是脚本，换成 Codex/Claude/WorkBuddy 完全等价）
      └─ MCP stdio ─▶ agentrpa_bridge/yingdao_mcp_server.py
                        └─ HTTP ─▶ 假影刀开放 API（examples/mock_yingdao_server.py）

跑法：
    python examples/reverse_demo.py
"""
from __future__ import annotations

import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "examples"))

import mock_yingdao_server  # noqa: E402

from agentrpa_bridge.mcp_client import McpStdioClient  # noqa: E402

SERVER = os.path.join(ROOT, "agentrpa_bridge", "yingdao_mcp_server.py")


def payload(out: dict):
    """MCP 工具返回的是 JSON 文本，这里解出来方便看。"""
    try:
        return json.loads(out["text"])
    except (KeyError, json.JSONDecodeError):
        return out.get("text")


def main():
    httpd = mock_yingdao_server.serve(0)
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    print(f"假影刀开放 API: {base}")

    client = McpStdioClient(sys.executable, [SERVER], timeout=30, env={
        "YINGDAO_API_BASE": base,
        "YINGDAO_ACCESS_KEY_ID": "ak_mock",
        "YINGDAO_ACCESS_KEY_SECRET": "sk_mock",
        "PYTHONUTF8": "1",
    })
    client.start()
    try:
        print(f"已连上 MCP 服务: {client.server_info['server']}")
        print("工具:", [t["name"] for t in client.list_tools()])

        apps = payload(client.call_tool("rpa_list_apps", {}))
        print("\n[1] 应用列表:", [a["name"] for a in apps["data"]["apps"]])
        app_id = apps["data"]["apps"][0]["id"]

        params = payload(client.call_tool("rpa_app_params", {"app_id": app_id}))
        print("[2] 入参定义:", [p["name"] for p in params["data"]["params"]])

        started = payload(client.call_tool("rpa_start_job", {
            "app_id": app_id,
            "params": {"月份": "2026-11", "税号": "91310000XXXX"},
        }))
        job_id = started["data"]["jobId"]
        print(f"[3] 已启动任务: {job_id}")

        for _ in range(20):
            detail = payload(client.call_tool("rpa_query_job", {"job_id": job_id}))["data"]
            print(f"    状态 {detail['status']:<8} 进度 {detail.get('progress', 0)}%")
            if detail["status"] in ("success", "failed", "not_found"):
                break
            time.sleep(0.5)

        print("\n[4] 最终结果:", json.dumps(detail.get("output"), ensure_ascii=False))

        clients = payload(client.call_tool("rpa_clients", {}))
        print("[5] 机器人:", [(c["name"], "在线" if c["online"] else "离线")
                             for c in clients["data"]["clients"]])
        print("\n链路打通：智能体 → MCP → 影刀开放 API → 任务 → 结果。")
    finally:
        client.close()
        httpd.shutdown()
        httpd.server_close()


if __name__ == "__main__":
    main()
