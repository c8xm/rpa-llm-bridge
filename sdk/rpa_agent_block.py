# -*- coding: utf-8 -*-
"""影刀 / 企业 RPA 的 Python 代码块片段（单文件、零依赖、Python 3.8+）。

放法：影刀「Python 模块」里粘贴本文件内容，或在流程里用「调用 Python 脚本」指向它，
然后在你自己的代码块里：

    import rpa_agent_block as ab
    text = ab.ask_agent("把这张发票的税额算出来：……", agent="codex", timeout=180)
    影刀变量.答案 = text

两种后端，按环境变量自动选：
  1) 网关模式（推荐，企业多机）
       AGENTRPA_GATEWAY=http://10.0.0.8:8765
       AGENTRPA_TOKEN=<控制台下发的 token>
     网关负责进程池、限速、审计；RPA 侧只发一个 HTTP。
  2) 本地 stdio 模式（单机工位，零部署）
       AGENTRPA_CODEX_CMD=codex
       AGENTRPA_CODEX_ARGS=mcp-server
     RPA 进程直接拉起 codex 的 MCP 服务，一次问答后关掉。

注意：Windows 命令行参数有 8191 字符上限，长文本一律走 HTTP body 或 stdin，不要拼进命令行。
"""

import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


# --------------------------------------------------------------------------
# 网关模式
# --------------------------------------------------------------------------
def ask_via_gateway(prompt, agent="codex", timeout=180, cwd=None, meta=None,
                    gateway=None, token=None, retries=1):
    gateway = (gateway or os.environ.get("AGENTRPA_GATEWAY", "")).rstrip("/")
    token = token or os.environ.get("AGENTRPA_TOKEN", "")
    payload = {"agent": agent, "prompt": prompt, "timeout_s": timeout,
               "meta": meta or {}}
    if cwd:
        payload["cwd"] = cwd
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    last = None
    for attempt in range(retries + 1):
        req = urllib.request.Request(gateway + "/v1/invoke", data=body, method="POST",
                                     headers={"Content-Type": "application/json",
                                              "Authorization": "Bearer " + token})
        try:
            with urllib.request.urlopen(req, timeout=timeout + 30) as resp:
                data = json.loads(resp.read().decode("utf-8", "replace"))
            if data.get("status") != "succeeded":
                raise RuntimeError("网关返回失败：%s" % data)
            return data.get("text", "")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:400]
            last = RuntimeError("HTTP %s: %s" % (exc.code, detail))
            if exc.code in (401, 429, 400):
                break
        except (urllib.error.URLError, OSError, ValueError) as exc:
            last = RuntimeError("连接网关失败：%s" % exc)
        if attempt < retries:
            time.sleep(1.5 * (attempt + 1))
    raise last or RuntimeError("调用失败")


# --------------------------------------------------------------------------
# 本地 stdio 模式（内置最小 MCP 客户端，避免影刀解释器装不了包）
# --------------------------------------------------------------------------
def _argv(cmd, args):
    exe = shutil.which(cmd) or cmd
    argv = [exe] + args
    if os.name == "nt" and exe.lower().endswith((".cmd", ".bat")):
        return ["cmd", "/c"] + argv
    return argv


def ask_via_stdio(prompt, agent="codex", timeout=180, cwd=None):
    cmd = os.environ.get("AGENTRPA_%s_CMD" % agent.upper(), agent)
    args = os.environ.get("AGENTRPA_%s_ARGS" % agent.upper(), "mcp-server").split()
    env = os.environ.copy()
    env.setdefault("NO_COLOR", "1")
    proc = subprocess.Popen(_argv(cmd, args), stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            universal_newlines=True, encoding="utf-8",
                            errors="replace", bufsize=1, cwd=cwd or os.getcwd(), env=env)
    try:
        _rpc(proc, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "shadowbot-block", "version": "0.2.0"}}}, timeout)
        _rpc(proc, {"jsonrpc": "2.0", "method": "notifications/initialized"}, timeout=None)
        tools = _rpc(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list",
                            "params": {}}, timeout)
        name = _pick_tool(tools.get("result", {}).get("tools", []))
        result = _rpc(proc, {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
            "name": name,
            "arguments": {"prompt": prompt, "cwd": cwd or os.getcwd()}}}, timeout)
        text = _text_of(result.get("result", {}))
        return _unwrap(text)
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass


def _rpc(proc, message, timeout):
    if message is not None:
        proc.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
        proc.stdin.flush()
    if timeout is None or "id" not in message:
        return {}
    deadline = time.time() + timeout
    while time.time() < deadline:
        line = proc.stdout.readline()
        if not line:
            err = proc.stderr.read()[-400:] if proc.stderr else ""
            raise RuntimeError("MCP 进程退出：%s" % err)
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue  # banner
        if msg.get("id") == message["id"]:
            if "error" in msg:
                raise RuntimeError("MCP 错误：%s" % msg["error"])
            return msg
    raise RuntimeError("MCP 超时 %ss" % timeout)


def _pick_tool(tools):
    names = [t.get("name") for t in tools if t.get("name")]
    for preferred in ("codex", "ask", "chat", "run", "agent"):
        if preferred in names:
            return preferred
    if not names:
        raise RuntimeError("该 MCP 服务没有暴露工具")
    return names[0]


def _text_of(result):
    parts = []
    for block in result.get("content") or []:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text") or "")
    return "\n".join(parts)


def _unwrap(text):
    stripped = (text or "").strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        try:
            data = json.loads(stripped)
        except ValueError:
            return text
        if isinstance(data, dict) and isinstance(data.get("content"), str):
            return data["content"]
    return text


# --------------------------------------------------------------------------
# 统一入口
# --------------------------------------------------------------------------
def ask_agent(prompt, agent="codex", timeout=180, cwd=None, meta=None):
    """影刀代码块里只调这一个函数。失败抛异常，交给流程的异常处理块。"""
    if os.environ.get("AGENTRPA_GATEWAY"):
        return ask_via_gateway(prompt, agent=agent, timeout=timeout, cwd=cwd, meta=meta)
    return ask_via_stdio(prompt, agent=agent, timeout=timeout, cwd=cwd)


def ask_agent_async(prompt, callback=None, agent="codex", timeout=180, meta=None):
    """影刀里不想卡住主流程时用；回调里拿到结果再写变量/发消息。"""

    def runner():
        try:
            text = ask_agent(prompt, agent=agent, timeout=timeout, meta=meta)
            if callback:
                callback(text, None)
        except Exception as exc:  # noqa: BLE001
            if callback:
                callback(None, exc)
            else:
                raise

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    return thread


# --------------------------------------------------------------------------
# 进度流（SSE）：长任务时让影刀界面能显示"第几步"
# --------------------------------------------------------------------------
def ask_agent_stream(prompt, on_event=None, agent="codex", timeout=600, meta=None,
                     gateway=None, token=None):
    """提交异步任务并读取 SSE 进度，返回最终文本。

    on_event(dict) 会依次收到 {"stage": "queued|running|text|tool_call|final|...", "seq": n}
    只支持网关模式（本地 stdio 没有事件流）。
    """
    gateway = (gateway or os.environ.get("AGENTRPA_GATEWAY", "")).rstrip("/")
    token = token or os.environ.get("AGENTRPA_TOKEN", "")
    if not gateway:
        raise RuntimeError("ask_agent_stream 需要 AGENTRPA_GATEWAY")

    body = json.dumps({"agent": agent, "prompt": prompt, "timeout_s": timeout,
                       "meta": meta or {}}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(gateway + "/v1/jobs", data=body, method="POST",
                                 headers={"Content-Type": "application/json",
                                          "Authorization": "Bearer " + token})
    with urllib.request.urlopen(req, timeout=30) as resp:
        run_id = json.loads(resp.read().decode("utf-8", "replace"))["run_id"]

    url = "%s/v1/jobs/%s/events" % (gateway, run_id)
    stream_req = urllib.request.Request(url, headers={"Authorization": "Bearer " + token})
    try:
        with urllib.request.urlopen(stream_req, timeout=timeout + 30) as resp:
            for raw in resp:
                line = raw.decode("utf-8", "replace").rstrip("\r\n")
                if not line.startswith("data: "):
                    if line.startswith("event: end"):
                        break
                    continue
                try:
                    event = json.loads(line[6:])
                except ValueError:
                    continue
                if on_event:
                    on_event(event)
    except (urllib.error.URLError, OSError):
        pass  # 流断了还能用下面的轮询兜底

    # 取最终结果（流里只带进度，不带正文）
    for _ in range(int(timeout) + 10):
        poll = urllib.request.Request("%s/v1/jobs/%s" % (gateway, run_id),
                                      headers={"Authorization": "Bearer " + token})
        with urllib.request.urlopen(poll, timeout=30) as resp:
            job = json.loads(resp.read().decode("utf-8", "replace"))
        if job.get("status") in ("succeeded", "failed"):
            result = job.get("result") or {}
            if job["status"] == "failed":
                raise RuntimeError(job.get("error") or "任务失败")
            return result.get("text", "")
        time.sleep(1)
    raise RuntimeError("任务 %s 等待超时" % run_id)


if __name__ == "__main__":
    # 影刀调试用：python rpa_agent_block.py "你的问题"
    question = " ".join(sys.argv[1:]) or "用一句话说明什么是 RPA"
    print(ask_agent(question))
