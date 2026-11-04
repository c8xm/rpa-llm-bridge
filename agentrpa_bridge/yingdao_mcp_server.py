"""把影刀 RPA 暴露成 MCP 服务：Codex / Claude Code / Cursor 可以直接触发流程。

用法（Codex config.toml）：
    [mcp_servers.yingdao]
    command = "python"
    args = ["<PROJECT>\\agentrpa_bridge\\yingdao_mcp_server.py"]
    env = { YINGDAO_ACCESS_KEY_ID = "...", YINGDAO_ACCESS_KEY_SECRET = "...",
            YINGDAO_API_BASE = "https://api.yingdao.com" }

零依赖：手写 JSON-RPC over stdio，只实现 initialize / tools/list / tools/call / ping。
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agentrpa_bridge.yingdao_api import (YingDaoError, YingDaoLocal,  # noqa: E402
                                        YingDaoOpenApi)

PROTOCOL_VERSION = "2025-06-18"

TOOLS = [
    {
        "name": "rpa_list_apps",
        "description": "列出影刀应用（RPA 流程）。先查这个拿到 appId，再决定跑哪个流程。",
        "inputSchema": {"type": "object", "properties": {
            "keyword": {"type": "string", "description": "按名称过滤，可空"}},
            "additionalProperties": False},
    },
    {
        "name": "rpa_app_params",
        "description": "查某个影刀应用的入参定义（参数名、类型、是否必填）。",
        "inputSchema": {"type": "object", "properties": {"app_id": {"type": "string"}},
                        "required": ["app_id"], "additionalProperties": False},
    },
    {
        "name": "rpa_start_job",
        "description": "启动一个影刀应用任务，返回 jobId。参数必须与应用入参定义一致。",
        "inputSchema": {"type": "object", "properties": {
            "app_id": {"type": "string"},
            "params": {"type": "object", "description": "流程入参"},
            "client_id": {"type": "string", "description": "指定机器人，可空"},
        }, "required": ["app_id"], "additionalProperties": False},
    },
    {
        "name": "rpa_query_job",
        "description": "查询影刀任务执行状态与输出。轮询用，不要连续高频调用。",
        "inputSchema": {"type": "object", "properties": {"job_id": {"type": "string"}},
                        "required": ["job_id"], "additionalProperties": False},
    },
    {
        "name": "rpa_clients",
        "description": "列出可调度的机器人（客户端）及其在线状态。",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "rpa_run_local",
        "description": "在本机直接拉起影刀客户端跑应用（仅配置了 YINGDAO_SHADOWBOT 时可用）。",
        "inputSchema": {"type": "object", "properties": {
            "app": {"type": "string", "description": "应用名或应用文件路径"},
            "params": {"type": "object"},
        }, "required": ["app"], "additionalProperties": False},
    },
]


class RpaTools:
    def __init__(self):
        self.api = YingDaoOpenApi()
        self.local = YingDaoLocal()

    def tools(self) -> list:
        available = [t for t in TOOLS if t["name"] != "rpa_run_local" or self.local.available()]
        return available

    def call(self, name: str, args: dict) -> str:
        if name == "rpa_list_apps":
            return self._json(self.api.list_apps(args.get("keyword", "")))
        if name == "rpa_app_params":
            return self._json(self.api.app_params(args["app_id"]))
        if name == "rpa_start_job":
            return self._json(self.api.start_job(args["app_id"], args.get("params"),
                                                args.get("client_id", "")))
        if name == "rpa_query_job":
            return self._json(self.api.query_job(args["job_id"]))
        if name == "rpa_clients":
            return self._json(self.api.clients())
        if name == "rpa_run_local":
            return self._json(self.local.run(args["app"], args.get("params")))
        raise KeyError(name)

    @staticmethod
    def _json(obj) -> str:
        return json.dumps(obj, ensure_ascii=False)[:20000]


def _reply(rid, result=None, error=None):
    msg = {"jsonrpc": "2.0", "id": rid}
    if error is not None:
        msg["error"] = error
    else:
        msg["result"] = result
    sys.stdout.write(json.dumps(msg, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _text_result(text: str, is_error: bool = False) -> dict:
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


def serve(tools: RpaTools):
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        method = msg.get("method")
        rid = msg.get("id")
        if method == "initialize":
            _reply(rid, {"protocolVersion": PROTOCOL_VERSION,
                         "capabilities": {"tools": {"listChanged": False}},
                         "serverInfo": {"name": "yingdao-rpa", "version": "0.2.0"},
                         "instructions": "影刀 RPA 工具面：先 rpa_list_apps 找应用，"
                                         "再 rpa_start_job 触发，rpa_query_job 拉结果。"})
        elif method == "notifications/initialized":
            continue
        elif method == "tools/list":
            _reply(rid, {"tools": tools.tools()})
        elif method == "ping":
            _reply(rid, {})
        elif method == "tools/call":
            params = msg.get("params") or {}
            name = params.get("name")
            args = params.get("arguments") or {}
            try:
                _reply(rid, _text_result(tools.call(name, args)))
            except KeyError:
                _reply(rid, error={"code": -32602, "message": f"未知工具：{name}"})
            except (YingDaoError, OSError, ValueError) as exc:
                _reply(rid, _text_result(f"{type(exc).__name__}: {exc}", is_error=True))
        elif rid is not None:
            _reply(rid, error={"code": -32601, "message": f"不支持的方法：{method}"})


def main():
    if "--selftest" in sys.argv:
        tools = RpaTools()
        print(json.dumps({"tools": [t["name"] for t in tools.tools()],
                          "openapi_configured": bool(tools.api.access_key_id),
                          "local_available": tools.local.available()},
                         ensure_ascii=False))
        return
    serve(RpaTools())


if __name__ == "__main__":
    main()
