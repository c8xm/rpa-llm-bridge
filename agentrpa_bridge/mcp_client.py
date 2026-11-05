"""最小 MCP stdio 客户端（纯标准库）。

为什么不直接用官方 mcp SDK：
  1. 影刀 Python 代码块里的解释器版本老、装包难，stdlib 版本可以直接粘进去跑；
  2. 企业网关里每个智能体进程都要常驻，少一个依赖就少一类事故；
  3. MCP 的 stdio 传输就是"行分隔 JSON-RPC"，实现成本很低。

支持：initialize 握手、notifications/initialized、tools/list、tools/call、
服务端发来的请求（ping / roots/list / sampling）就地应答。
线程安全：所有请求串行化（同一进程上的并发调用请用 pool 里的多实例）。
"""
from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import threading
import time

PROTOCOL_VERSION = "2025-06-18"
CLIENT_NAME = "agentrpa-bridge"
_WIN_SHIMS = (".cmd", ".bat")


class McpError(RuntimeError):
    """协议层 / 服务端返回错误。"""


class McpTimeout(McpError):
    """等待响应超时（进程可能已卡死或未完成初始化）。"""


_CMD_META = '&|<>^()%'


def _escape_for_cmd(arg: str) -> str:
    """cmd /c 会二次解析参数：把元字符转义掉，避免 `& calc` 之类被当成新命令。"""
    if arg == "":
        return '""'
    out = []
    in_quotes = False
    for ch in arg:
        if ch == '"':
            in_quotes = not in_quotes
            out.append(ch)
            continue
        if not in_quotes and ch in _CMD_META:
            # 引号内 cmd 不解释 & | < >，但 ^ 和 % 仍特殊，统一转义更稳
            out.append("%" if ch == "%" else "^" + ch)
        else:
            out.append(ch)
    return "".join(out)


def argv_for(command: str, args) -> list:
    """解析可执行文件；Windows 下的 .cmd/.bat shim（npm 装的 codex/claude/dsh 都是）
    必须经 cmd /c 才能被 CreateProcess 执行。"""
    exe = shutil.which(command) or command
    if os.name == "nt" and exe.lower().endswith(_WIN_SHIMS):
        return ["cmd", "/c", exe, *[_escape_for_cmd(str(a)) for a in args]]
    return [exe, *args]


_argv_for = argv_for  # 兼容旧名


class _LineReader(threading.Thread):
    """把子进程 stdout 逐行解析成 JSON 消息，塞进队列。"""

    def __init__(self, stream, sink: "queue.Queue", banners: list):
        super().__init__(daemon=True)
        self._stream = stream
        self._sink = sink
        self._banners = banners

    def run(self):
        try:
            for raw in self._stream:
                line = (raw or "").strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    # 有些服务会往 stdout 打 banner，不能当成协议错误
                    if len(self._banners) < 20:
                        self._banners.append(line[:300])
                    continue
                self._sink.put(msg)
        except Exception as exc:  # 管道被关闭
            self._banners.append(f"reader error: {exc}")
        finally:
            self._sink.put(None)


class _StderrDrain(threading.Thread):
    def __init__(self, stream, tail: list, limit: int = 60):
        super().__init__(daemon=True)
        self._stream = stream
        self._tail = tail
        self._limit = limit

    def run(self):
        try:
            for raw in self._stream:
                self._tail.append((raw or "").rstrip()[:500])
                if len(self._tail) > self._limit:
                    del self._tail[: len(self._tail) - self._limit]
        except Exception:
            pass


def _normalize(result: dict) -> dict:
    """把 tools/call 的 result 收敛成 RPA 好用的结构。"""
    texts, images, resources = [], 0, []
    for block in result.get("content") or []:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text":
            texts.append(block.get("text") or "")
        elif btype == "image":
            images += 1
        elif btype == "resource":
            res = block.get("resource") or {}
            resources.append({"uri": res.get("uri"), "text": res.get("text")})
        else:
            texts.append(json.dumps(block, ensure_ascii=False))
    text = "\n".join(t for t in texts if t)
    return {
        "is_error": bool(result.get("isError")),
        "text": text,
        "images": images,
        "resources": resources,
        "structured": result.get("structuredContent"),
        "raw": result,
    }


def unwrap_agent_text(text: str) -> str:
    """Codex 之类的服务会把最终答案再包一层 JSON，这里拆掉外壳。

    形如 {"threadId": "...", "content": "答案"} 或 {"content": [{"type":"text","text":"..."}]}
    """
    if not text:
        return text
    stripped = text.strip()
    if not (stripped.startswith("{") and stripped.endswith("}")):
        return text
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        return text
    if not isinstance(payload, dict):
        return text
    inner = payload.get("content")
    if isinstance(inner, str):
        return inner
    if isinstance(inner, list):
        parts = []
        for item in inner:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
            elif isinstance(item, str):
                parts.append(item)
        if parts:
            return "\n".join(parts)
    for key in ("result", "output", "answer", "text"):
        if isinstance(payload.get(key), str):
            return payload[key]
    return text


class McpStdioClient:
    """一条到 MCP stdio 服务的连接。

    >>> c = McpStdioClient("codex", ["mcp-server"]).start()
    >>> c.list_tools()[0]["name"]
    'codex'
    >>> c.call_tool("codex", {"prompt": "1+1"})["text"]
    """

    def __init__(self, command: str, args=(), env: dict | None = None,
                 cwd: str | None = None, timeout: float = 60.0,
                 name: str = CLIENT_NAME):
        self.command = command
        self.args = list(args)
        self.extra_env = dict(env or {})
        self.cwd = cwd
        self.timeout = timeout
        self.client_name = name
        self.proc: subprocess.Popen | None = None
        self.server_info: dict = {}
        self.banners: list = []
        self.stderr_tail: list = []
        self._q: "queue.Queue" = queue.Queue()
        self._lock = threading.RLock()
        self._next_id = 0

    # ---------- 生命周期 ----------
    def start(self) -> "McpStdioClient":
        if self.proc and self.proc.poll() is None:
            return self
        argv = _argv_for(self.command, self.args)
        env = os.environ.copy()
        env.update({k: str(v) for k, v in self.extra_env.items()})
        # 服务端如果是 Node，强制非交互、UTF-8
        env.setdefault("NO_COLOR", "1")
        env.setdefault("PYTHONIOENCODING", "utf-8")
        self.proc = subprocess.Popen(
            argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, encoding="utf-8",
            errors="replace", bufsize=1, cwd=self.cwd, env=env,
        )
        _LineReader(self.proc.stdout, self._q, self.banners).start()
        _StderrDrain(self.proc.stderr, self.stderr_tail).start()

        init = self._request("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"roots": {"listChanged": False}},
            "clientInfo": {"name": self.client_name, "version": "0.2.0"},
        }, timeout=max(self.timeout, 30.0))
        self.server_info = {
            "server": init.get("serverInfo") or {},
            "protocol": init.get("protocolVersion"),
            "capabilities": sorted((init.get("capabilities") or {}).keys()),
            "instructions": (init.get("instructions") or "")[:500],
        }
        self._notify("notifications/initialized")
        return self

    def close(self, grace: float = 2.0):
        proc = self.proc
        self.proc = None
        if not proc:
            return
        try:
            if proc.stdin:
                proc.stdin.close()
        except Exception:
            pass
        try:
            proc.terminate()
        except Exception:
            pass
        try:
            proc.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
            except Exception:
                pass
        try:
            proc.wait(timeout=grace)
        except Exception:
            pass
        for stream in (proc.stdout, proc.stderr):
            try:
                if stream:
                    stream.close()
            except Exception:
                pass

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.close()

    # ---------- 协议 ----------
    def _send(self, payload: dict):
        proc = self.proc
        if not proc or proc.poll() is not None or not proc.stdin:
            raise McpError(f"进程不可用：{self.command}（退出码 {None if not proc else proc.poll()}）"
                           f"\nstderr: {' | '.join(self.stderr_tail[-5:])}")
        proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
        proc.stdin.flush()

    def _notify(self, method: str, params: dict | None = None):
        body = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            body["params"] = params
        self._send(body)

    def _next_message(self, deadline: float) -> dict:
        remain = deadline - time.monotonic()
        if remain <= 0:
            raise McpTimeout(
                f"{self.command} 响应超时 {self.timeout}s；"
                f"stderr: {' | '.join(self.stderr_tail[-3:]) or '（空）'}")
        try:
            msg = self._q.get(timeout=remain)
        except queue.Empty:
            raise McpTimeout(
                f"{self.command} 响应超时 {self.timeout}s；"
                f"stderr: {' | '.join(self.stderr_tail[-3:]) or '（空）'}") from None
        if msg is None:
            # 读到 EOF：等进程真正退出再报错，否则拿不到退出码和 stderr
            code = None
            if self.proc:
                try:
                    code = self.proc.wait(timeout=1.5)
                except subprocess.TimeoutExpired:
                    code = self.proc.poll()
            time.sleep(0.05)  # 给 stderr 采集线程一点时间
            raise McpError(
                f"{self.command} 已关闭 stdout（进程退出码 {code}）；"
                f"stderr: {' | '.join(self.stderr_tail[-5:]) or '（空）'}"
                + (f"；stdout 非协议输出: {self.banners[:3]}" if self.banners else ""))
        return msg

    def _answer_server_request(self, msg: dict):
        """服务端反向请求（ping / roots/list / sampling）：能答就答，不能答就回错。"""
        method = msg.get("method") or ""
        rid = msg.get("id")
        if rid is None:
            return  # 通知，忽略
        if method == "ping":
            reply = {"jsonrpc": "2.0", "id": rid, "result": {}}
        elif method == "roots/list":
            reply = {"jsonrpc": "2.0", "id": rid, "result": {"roots": []}}
        else:
            reply = {"jsonrpc": "2.0", "id": rid,
                     "error": {"code": -32601, "message": f"client does not support {method}"}}
        try:
            self._send(reply)
        except McpError:
            pass

    def _request(self, method: str, params: dict | None = None,
                 timeout: float | None = None) -> dict:
        timeout = self.timeout if timeout is None else timeout
        with self._lock:
            self._next_id += 1
            rid = self._next_id
            payload = {"jsonrpc": "2.0", "id": rid, "method": method}
            if params is not None:
                payload["params"] = params
            self._send(payload)
            deadline = time.monotonic() + timeout
            while True:
                msg = self._next_message(deadline)
                if "method" in msg and "id" in msg and ("result" not in msg):
                    self._answer_server_request(msg)
                    continue
                if msg.get("id") != rid:
                    continue  # 迟到的旧响应，丢弃
                if "error" in msg:
                    err = msg["error"]
                    raise McpError(f"{method} 失败：{err.get('message') or err}"
                                   f"（code={err.get('code')}）")
                return msg.get("result") or {}

    # ---------- 业务 API ----------
    def list_tools(self) -> list:
        result = self._request("tools/list", {})
        tools = result.get("tools") or []
        return [{"name": t.get("name"), "description": (t.get("description") or "")[:300],
                 "schema": t.get("inputSchema") or {}} for t in tools]

    def call_tool(self, name: str, arguments: dict | None = None,
                  timeout: float | None = None) -> dict:
        result = self._request("tools/call",
                               {"name": name, "arguments": arguments or {}},
                               timeout=timeout)
        return _normalize(result)

    def ask(self, name: str, prompt: str, timeout: float | None = None,
            extra: dict | None = None) -> dict:
        """给"一句话进、一段话出"的智能体工具用的便捷封装。"""
        args = {"prompt": prompt}
        if extra:
            args.update(extra)
        out = self.call_tool(name, args, timeout=timeout)
        out["text"] = unwrap_agent_text(out.get("text") or "")
        return out
