"""智能体 provider 层：把不同形态的"智能体服务"收敛成同一个 invoke(prompt) 契约。

三种形态，覆盖企业里能落地的全部接法：

1. kind="mcp"    —— 本地 stdio MCP 服务（`codex mcp-server`、`claude mcp serve`、
                    自研 MCP）。进程常驻、会话复用，延迟最低。
2. kind="cli"    —— 无 MCP 时的兜底：`codex exec`、`claude -p`，一次性进程。
3. kind="openai" —— 直接打模型 API（DeepSeek / OpenAI / vLLM / 企业私有网关），
                    适合没有 agent CLI、只需要"补一段文本"的场景。

所有 provider 都返回 dict：
    {"agent", "text", "is_error", "duration_ms", "raw", "meta"}
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from .mcp_client import McpError, McpStdioClient, argv_for, unwrap_agent_text

_SECRET_RE = re.compile(
    r"(sk-[A-Za-z0-9_\-]{8,}|ghp_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{12,}"
    r"|Bearer\s+[A-Za-z0-9._\-]{10,})")


def redact(text: str) -> str:
    if not text:
        return text
    return _SECRET_RE.sub("[REDACTED]", text)


class AgentError(RuntimeError):
    """provider 层统一错误（进程崩溃、HTTP 4xx/5xx、超时）。"""


@dataclass
class AgentSpec:
    name: str
    kind: str = "mcp"                       # mcp | cli | openai
    command: str = ""
    args: list = field(default_factory=list)
    env: dict = field(default_factory=dict)
    cwd: str | None = None
    tool: str = ""                          # kind=mcp 时的工具名
    arg_style: str = "prompt"               # codex | prompt | generic
    arg_template: dict = field(default_factory=dict)
    timeout: float = 300.0
    model: str = ""
    base_url: str = ""
    api_key: str = ""
    headers: dict = field(default_factory=dict)
    description: str = ""

    def public(self) -> dict:
        return {"name": self.name, "kind": self.kind, "tool": self.tool,
                "timeout": self.timeout, "model": self.model,
                "description": self.description}


def _subst(text: str, ctx: dict) -> str:
    """只替换已知占位符，不用 str.format —— 参数里可能带 JSON 花括号。"""
    for key, val in ctx.items():
        text = text.replace("{" + key + "}", str(val))
    return text


def _render(template: dict, ctx: dict) -> dict:
    return {key: (_subst(val, ctx) if isinstance(val, str) else val)
            for key, val in template.items()}


class BaseProvider:
    spec: AgentSpec

    def invoke(self, prompt: str, cwd: str | None = None,
               meta: dict | None = None, timeout: float | None = None) -> dict:
        raise NotImplementedError

    def close(self):
        pass

    def health(self) -> dict:
        return {"name": self.spec.name, "kind": self.spec.kind, "ok": True}


class McpAgentProvider(BaseProvider):
    """常驻 MCP 进程 + 会话复用。"""

    def __init__(self, spec: AgentSpec):
        self.spec = spec
        self._client: McpStdioClient | None = None
        self._lock = threading.RLock()
        self._last_used = 0.0

    def _args_for(self, prompt: str, cwd: str | None, meta: dict) -> dict:
        ctx = {
            "prompt": prompt,
            "cwd": cwd or self.spec.cwd or os.getcwd(),
            "model": self.spec.model,
            "today": time.strftime("%Y-%m-%d"),
        }
        style = self.spec.arg_style
        if self.spec.arg_template:
            return _render(self.spec.arg_template, ctx)
        if style == "codex":
            args = {
                "prompt": prompt,
                "cwd": ctx["cwd"],
                "sandbox": os.environ.get("AGENTRPA_CODEX_SANDBOX", "workspace-write"),
                "approval-policy": "never",
            }
            if self.spec.model:
                args["model"] = self.spec.model
            return args
        return {"prompt": prompt}

    def _connect(self) -> McpStdioClient:
        client = McpStdioClient(self.spec.command, self.spec.args,
                               env=self.spec.env, cwd=self.spec.cwd,
                               timeout=self.spec.timeout, name=f"rpa-bridge/{self.spec.name}")
        client.start()
        if not self.spec.tool:
            tools = client.list_tools()
            self.spec.tool = self._pick_tool(tools)
        return client

    @staticmethod
    def _pick_tool(tools: list) -> str:
        """没显式指定工具时，挑一个最像"一句话进、一段话出"的工具。"""
        names = [t["name"] for t in tools if t.get("name")]
        for preferred in ("codex", "ask", "chat", "run", "task", "agent"):
            if preferred in names:
                return preferred
        return names[0] if names else ""

    def invoke(self, prompt: str, cwd: str | None = None,
               meta: dict | None = None, timeout: float | None = None) -> dict:
        timeout = timeout or self.spec.timeout
        started = time.monotonic()
        with self._lock:
            last_err = None
            for attempt in (1, 2):
                try:
                    if self._client is None:
                        self._client = self._connect()
                    arguments = self._args_for(prompt, cwd, meta or {})
                    out = self._client.call_tool(self.spec.tool, arguments, timeout=timeout)
                    self._last_used = time.monotonic()
                    text = unwrap_agent_text(out.get("text") or "")
                    return {
                        "agent": self.spec.name,
                        "text": text,
                        "is_error": out.get("is_error", False),
                        "duration_ms": int((time.monotonic() - started) * 1000),
                        "raw": {"images": out.get("images"), "structured": out.get("structured")},
                        "meta": meta or {},
                    }
                except (McpError, OSError) as exc:
                    last_err = exc
                    self._reset()
                    if attempt == 1:
                        continue  # 进程死了就重连一次，第二次失败才上抛
            raise AgentError(f"[{self.spec.name}] {last_err}") from last_err

    def _reset(self):
        if self._client:
            try:
                self._client.close()
            except Exception:
                pass
        self._client = None

    def close(self):
        with self._lock:
            self._reset()

    def health(self) -> dict:
        with self._lock:
            alive = bool(self._client and self._client.proc and self._client.proc.poll() is None)
            return {"name": self.spec.name, "kind": "mcp", "ok": True,
                    "session": "alive" if alive else "cold",
                    "tool": self.spec.tool,
                    "server": (self._client.server_info if self._client else {})}


class CliProvider(BaseProvider):
    """一次性 CLI 调用：codex exec / claude -p / 自研脚本。"""

    def __init__(self, spec: AgentSpec):
        self.spec = spec

    def invoke(self, prompt: str, cwd: str | None = None,
               meta: dict | None = None, timeout: float | None = None) -> dict:
        timeout = timeout or self.spec.timeout
        ctx = {"prompt": prompt, "cwd": cwd or self.spec.cwd or os.getcwd(),
               "model": self.spec.model}
        argv = [_subst(a, ctx) for a in self.spec.args]
        use_stdin = not any("{prompt}" in a for a in self.spec.args)
        env = os.environ.copy()
        env.update({k: str(v) for k, v in self.spec.env.items()})
        started = time.monotonic()
        try:
            proc = subprocess.run(
                argv_for(self.spec.command, argv), input=prompt if use_stdin else None,
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=timeout, cwd=cwd or self.spec.cwd, env=env)
        except subprocess.TimeoutExpired as exc:
            raise AgentError(f"[{self.spec.name}] CLI 超时 {timeout}s") from exc
        except OSError as exc:
            raise AgentError(f"[{self.spec.name}] CLI 启动失败：{exc}") from exc
        if proc.returncode != 0:
            raise AgentError(f"[{self.spec.name}] CLI 退出码 {proc.returncode}："
                             f"{redact((proc.stderr or '')[-400:])}")
        text = self._extract(proc.stdout or "")
        return {"agent": self.spec.name, "text": text, "is_error": False,
                "duration_ms": int((time.monotonic() - started) * 1000),
                "raw": {"returncode": proc.returncode}, "meta": meta or {}}

    @staticmethod
    def _extract(stdout: str) -> str:
        stripped = stdout.strip()
        if stripped.startswith("{"):
            try:
                payload = json.loads(stripped)
            except json.JSONDecodeError:
                payload = None
            if isinstance(payload, dict):
                for key in ("result", "text", "content", "output"):
                    val = payload.get(key)
                    if isinstance(val, str):
                        return val
                    if isinstance(val, list):
                        parts = [v.get("text") for v in val if isinstance(v, dict)]
                        if any(parts):
                            return "\n".join(p for p in parts if p)
        return stripped


class OpenAICompatProvider(BaseProvider):
    """OpenAI / DeepSeek / vLLM / 企业私有 OpenAI 兼容网关。"""

    def __init__(self, spec: AgentSpec):
        self.spec = spec
        self.base_url = (spec.base_url or os.environ.get("AGENTRPA_OPENAI_BASE")
                         or "https://api.deepseek.com/v1").rstrip("/")
        self.api_key = spec.api_key or os.environ.get("AGENTRPA_OPENAI_KEY", "")
        self.model = spec.model or os.environ.get("AGENTRPA_OPENAI_MODEL", "")

    def invoke(self, prompt: str, cwd: str | None = None,
               meta: dict | None = None, timeout: float | None = None) -> dict:
        timeout = timeout or self.spec.timeout
        if not self.api_key:
            raise AgentError(f"[{self.spec.name}] 缺少 API key"
                             f"（spec.api_key 或对应环境变量）")
        if not self.model:
            raise AgentError(f"[{self.spec.name}] 缺少 model"
                             f"（豆包填方舟控制台的接入点 id，形如 ep-2026xxxx；"
                             f"DeepSeek 填 deepseek-chat）")
        body = json.dumps({
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
        }).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions", data=body, method="POST",
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {self.api_key}",
                     **self.spec.headers})
        started = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300]
            raise AgentError(f"[{self.spec.name}] HTTP {exc.code}：{redact(detail)}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise AgentError(f"[{self.spec.name}] 连接失败：{exc}") from exc
        try:
            text = payload["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise AgentError(f"[{self.spec.name}] 响应结构异常：{str(payload)[:200]}") from exc
        usage = payload.get("usage") or {}
        return {"agent": self.spec.name, "text": text, "is_error": False,
                "duration_ms": int((time.monotonic() - started) * 1000),
                "raw": {"usage": usage, "model": payload.get("model")}, "meta": meta or {}}


def build_provider(spec: AgentSpec) -> BaseProvider:
    if spec.kind == "mcp":
        return McpAgentProvider(spec)
    if spec.kind == "cli":
        return CliProvider(spec)
    if spec.kind == "openai":
        return OpenAICompatProvider(spec)
    raise AgentError(f"未知 kind：{spec.kind}")


class AgentPool:
    """按名字管理 provider，MCP 会话按 (name, cwd) 复用，空闲超时回收。"""

    def __init__(self, specs: list, idle_ttl: float = 180.0):
        self.specs = {s.name: s for s in specs}
        self.providers = {s.name: build_provider(s) for s in specs}
        self.idle_ttl = idle_ttl
        self._last_used = {}
        self._reaper = threading.Thread(target=self._reap_loop, daemon=True)
        self._stop = threading.Event()
        self._reaper.start()

    def names(self) -> list:
        return sorted(self.specs)

    def snapshot(self) -> list:
        return [self.providers[n].health() for n in self.names()]

    def invoke(self, name: str, prompt: str, cwd: str | None = None,
               meta: dict | None = None, timeout: float | None = None) -> dict:
        provider = self.providers.get(name)
        if not provider:
            raise AgentError(f"未知 agent：{name}（可用：{', '.join(self.names())}）")
        result = provider.invoke(prompt, cwd=cwd, meta=meta, timeout=timeout)
        self._last_used[name] = time.monotonic()
        return result

    def _reap_loop(self):
        while not self._stop.wait(15):
            if self.idle_ttl <= 0:
                continue
            now = time.monotonic()
            for name, provider in list(self.providers.items()):
                if not isinstance(provider, McpAgentProvider):
                    continue
                used = self._last_used.get(name, 0.0)
                if used and now - used > self.idle_ttl:
                    provider.close()
                    self._last_used.pop(name, None)

    def close(self):
        self._stop.set()
        for provider in self.providers.values():
            try:
                provider.close()
            except Exception:
                pass


def default_specs() -> list:
    """从环境变量装配一套默认 agent，企业部署只改 env 不改代码。"""
    specs = []
    codex_cmd = os.environ.get("AGENTRPA_CODEX_CMD", "codex")
    codex_args = os.environ.get("AGENTRPA_CODEX_ARGS", "mcp-server").split()
    specs.append(AgentSpec(
        name="codex", kind="mcp", command=codex_cmd, args=codex_args,
        tool=os.environ.get("AGENTRPA_CODEX_TOOL", ""), arg_style="codex",
        timeout=float(os.environ.get("AGENTRPA_CODEX_TIMEOUT", "600")),
        description="Codex CLI 以 MCP 服务形态提供 codex / codex-reply 工具"))

    claude_cmd = os.environ.get("AGENTRPA_CLAUDE_CMD", "claude")
    claude_args = os.environ.get("AGENTRPA_CLAUDE_ARGS", "mcp serve").split()
    specs.append(AgentSpec(
        name="claude", kind="mcp", command=claude_cmd, args=claude_args,
        tool=os.environ.get("AGENTRPA_CLAUDE_TOOL", ""), arg_style="prompt",
        timeout=float(os.environ.get("AGENTRPA_CLAUDE_TIMEOUT", "600")),
        description="Claude Code 的工具面（注意：它是工具集合，不是一次性问答）"))

    specs.append(AgentSpec(
        name="dsh", kind="cli",
        command=os.environ.get("AGENTRPA_DSH_CMD", "dsh"),
        args=os.environ.get("AGENTRPA_DSH_ARGS", "headless -").split(),
        timeout=float(os.environ.get("AGENTRPA_DSH_TIMEOUT", "900")),
        description="DeepSeek Harness headless：一次性任务，stdout 即最终答案"))

    specs.append(AgentSpec(
        name="doubao", kind="openai",
        base_url=os.environ.get("AGENTRPA_DOUBAO_BASE",
                                "https://ark.cn-beijing.volces.com/api/v3"),
        model=os.environ.get("AGENTRPA_DOUBAO_MODEL", ""),
        api_key=os.environ.get("AGENTRPA_DOUBAO_KEY", ""),
        timeout=float(os.environ.get("AGENTRPA_DOUBAO_TIMEOUT", "120")),
        description="豆包（火山方舟 Ark，OpenAI 兼容，model 填接入点 id）"))

    specs.append(AgentSpec(
        name="llm", kind="openai",
        model=os.environ.get("AGENTRPA_OPENAI_MODEL", "deepseek-chat"),
        base_url=os.environ.get("AGENTRPA_OPENAI_BASE", "https://api.deepseek.com/v1"),
        timeout=float(os.environ.get("AGENTRPA_OPENAI_TIMEOUT", "120")),
        description="OpenAI 兼容接口，兜底纯文本生成"))
    return specs
