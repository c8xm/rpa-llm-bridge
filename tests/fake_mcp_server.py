"""测试用假 MCP 服务：实现 initialize / tools/list / tools/call 三个方法。

工具：
  echo    —— 原样返回 {"prompt": ...}
  agent   —— 模拟 Codex：返回 {"threadId": "...", "content": "<prompt> 的答案"}
  boom    —— 返回 isError=True
  sleep   —— 睡 N 秒（测超时）
"""
import json
import sys
import time

TOOLS = [
    {"name": "echo", "description": "回显 prompt",
     "inputSchema": {"type": "object", "properties": {"prompt": {"type": "string"}}}},
    {"name": "agent", "description": "模拟一次性问答",
     "inputSchema": {"type": "object", "properties": {"prompt": {"type": "string"}}}},
    {"name": "boom", "description": "总是失败", "inputSchema": {"type": "object"}},
    {"name": "sleep", "description": "睡一会儿",
     "inputSchema": {"type": "object", "properties": {"seconds": {"type": "number"}}}},
]


def reply(rid, result):
    print(json.dumps({"jsonrpc": "2.0", "id": rid, "result": result}), flush=True)


def main():
    # 模拟有些服务会往 stdout 打 banner
    print("fake-mcp 0.1 ready", flush=True)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        msg = json.loads(line)
        method = msg.get("method")
        rid = msg.get("id")
        if method == "initialize":
            reply(rid, {"protocolVersion": "2025-06-18",
                        "capabilities": {"tools": {"listChanged": False}},
                        "serverInfo": {"name": "fake-mcp", "version": "0.1"}})
        elif method == "notifications/initialized":
            continue
        elif method == "tools/list":
            reply(rid, {"tools": TOOLS})
        elif method == "tools/call":
            name = (msg.get("params") or {}).get("name")
            args = (msg.get("params") or {}).get("arguments") or {}
            if name == "echo":
                reply(rid, {"content": [{"type": "text", "text": args.get("prompt", "")}]})
            elif name == "agent":
                inner = json.dumps({"threadId": "th_1",
                                    "content": f"答案是：{args.get('prompt', '')}"},
                                   ensure_ascii=False)
                reply(rid, {"content": [{"type": "text", "text": inner}]})
            elif name == "boom":
                reply(rid, {"isError": True,
                            "content": [{"type": "text", "text": "policy denied"}]})
            elif name == "sleep":
                time.sleep(float(args.get("seconds", 1)))
                reply(rid, {"content": [{"type": "text", "text": "done"}]})
            else:
                print(json.dumps({"jsonrpc": "2.0", "id": rid,
                                  "error": {"code": -32601, "message": "unknown tool"}}),
                      flush=True)
        elif rid is not None:
            print(json.dumps({"jsonrpc": "2.0", "id": rid,
                              "error": {"code": -32601, "message": "unknown method"}}),
                  flush=True)


if __name__ == "__main__":
    main()
