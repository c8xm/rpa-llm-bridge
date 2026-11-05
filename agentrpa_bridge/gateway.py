"""企业侧 HTTP 网关：让影刀 / UiPath / Power Automate / 自研调度都能用 HTTP 调智能体。

为什么不是"每个流程自己起一个 codex 进程"：
  · 凭证一处托管，流程里不出现 API key；
  · 进程池复用，冷启动只付一次；
  · 并发闸门 + 限速，防止某个流程把订阅额度打穿；
  · 统一审计：谁（tenant）、哪个流程（meta.flow）、输入指纹、耗时、结果长度；
  · 一个 /healthz 就能接进企业监控。

纯标准库（http.server + threading + urllib），无第三方依赖，Windows 上可用任务计划或
NSSM 直接注册成服务。

接口：
  POST /v1/invoke       同步调用（适合影刀"调用模块"节点，内部已设超时）
  POST /v1/jobs         异步提交，立即返回 run_id
  GET  /v1/jobs/{id}    查任务
  GET  /v1/agents       可用的 agent 与其工具
  GET  /healthz         存活 + 会话状态
  GET  /metrics         Prometheus 文本格式
鉴权：Authorization: Bearer <token>，token→tenant 映射来自 AGENTRPA_TOKENS。
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import threading
import time
import uuid
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .providers import AgentError, AgentPool, default_specs, redact
from .store import make_store

MAX_BODY = 2 * 1024 * 1024
# 超限时最多再丢弃这么多字节，让客户端能读到 413，而不是连接被重置
DRAIN_CAP = 16 * 1024 * 1024


class PayloadTooLarge(ValueError):
    """请求体超过 MAX_BODY；调用方必须关闭连接，不能继续 keep-alive。"""


# ----------------------------- 配置 -----------------------------
@dataclass
class GatewayConfig:
    host: str = "127.0.0.1"
    port: int = 8765
    tokens: dict = field(default_factory=dict)       # token -> tenant
    allow_anon_localhost: bool = True
    max_concurrency: int = 4
    queue_timeout: float = 30.0
    rate_limit_per_min: int = 60
    idle_ttl: float = 180.0
    audit_dir: str = "artifacts/audit"
    job_ttl: float = 3600.0
    idempotency_ttl: float = 900.0
    state_backend: str = "memory"                     # memory | sqlite
    state_path: str = "artifacts/bridge-state.db"
    state_url: str = "redis://127.0.0.1:6379/0"        # 仅 backend=redis 时使用

    @classmethod
    def from_env(cls) -> "GatewayConfig":
        tokens = {}
        for item in os.environ.get("AGENTRPA_TOKENS", "").split(","):
            if ":" in item:
                tenant, token = item.split(":", 1)
                tokens[token.strip()] = tenant.strip()
        return cls(
            host=os.environ.get("AGENTRPA_HOST", "127.0.0.1"),
            port=int(os.environ.get("AGENTRPA_PORT", "8765")),
            tokens=tokens,
            allow_anon_localhost=os.environ.get("AGENTRPA_ALLOW_ANON", "0") == "1",
            max_concurrency=int(os.environ.get("AGENTRPA_MAX_CONCURRENCY", "4")),
            queue_timeout=float(os.environ.get("AGENTRPA_QUEUE_TIMEOUT", "30")),
            rate_limit_per_min=int(os.environ.get("AGENTRPA_RATE_LIMIT", "60")),
            idle_ttl=float(os.environ.get("AGENTRPA_IDLE_TTL", "180")),
            audit_dir=os.environ.get("AGENTRPA_AUDIT_DIR", "artifacts/audit"),
            job_ttl=float(os.environ.get("AGENTRPA_JOB_TTL", "3600")),
            idempotency_ttl=float(os.environ.get("AGENTRPA_IDEMPOTENCY_TTL", "900")),
            state_backend=os.environ.get("AGENTRPA_STATE_BACKEND", "memory"),
            state_path=os.environ.get("AGENTRPA_STATE_PATH", "artifacts/bridge-state.db"),
            state_url=os.environ.get("AGENTRPA_REDIS_URL", "redis://127.0.0.1:6379/0"),
        )


# ----------------------------- 审计 -----------------------------
class AuditLog:
    def __init__(self, directory: str):
        self.directory = directory
        os.makedirs(directory, exist_ok=True)
        self._lock = threading.Lock()

    def write(self, record: dict):
        record = dict(record)
        record.setdefault("ts", time.strftime("%Y-%m-%dT%H:%M:%S%z"))
        for key in ("prompt_head", "error"):
            if isinstance(record.get(key), str):
                record[key] = redact(record[key])[:400]
        line = json.dumps(record, ensure_ascii=False)
        path = os.path.join(self.directory, f"agent-{time.strftime('%Y%m%d')}.jsonl")
        with self._lock:
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")


# ----------------------------- 指标与限速 -----------------------------
class Metrics:
    def __init__(self):
        self._lock = threading.Lock()
        self.counters = {}          # key -> int
        self.started = time.time()
        self.active = 0
        self.duration_sum = {}      # agent -> ms
        self.duration_count = {}

    def inc(self, key: str, n: int = 1):
        with self._lock:
            self.counters[key] = self.counters.get(key, 0) + n

    def observe(self, agent: str, ms: int):
        with self._lock:
            self.duration_sum[agent] = self.duration_sum.get(agent, 0) + ms
            self.duration_count[agent] = self.duration_count.get(agent, 0) + 1

    def render(self, extra: dict) -> str:
        lines = ["# HELP agentrpa_uptime_seconds 网关运行时长",
                 "# TYPE agentrpa_uptime_seconds gauge",
                 f"agentrpa_uptime_seconds {time.time() - self.started:.0f}",
                 "# TYPE agentrpa_inflight_requests gauge",
                 f"agentrpa_inflight_requests {self.active}"]
        with self._lock:
            for key, val in sorted(self.counters.items()):
                lines.append(f"agentrpa_{key} {val}")
            for agent, total in sorted(self.duration_sum.items()):
                count = self.duration_count.get(agent) or 1
                lines.append(f'agentrpa_agent_avg_ms{{agent="{agent}"}} {total / count:.0f}')
        for key, val in extra.items():
            lines.append(f"agentrpa_{key} {val}")
        return "\n".join(lines) + "\n"


def _idem_key(key: tuple) -> str:
    return f"{key[0]}\x00{key[1]}"



# ----------------------------- 网关状态 -----------------------------
class GatewayState:
    def __init__(self, config: GatewayConfig, pool: AgentPool | None = None):
        self.config = config
        self.pool = pool or AgentPool(default_specs(), idle_ttl=config.idle_ttl)
        self.audit = AuditLog(config.audit_dir)
        self.metrics = Metrics()
        self.store = make_store(config.state_backend, config.state_path,
                                config.idempotency_ttl, config.state_url)
        self.semaphore = threading.BoundedSemaphore(max(1, config.max_concurrency))
        self.jobs = {}
        self._jobs_lock = threading.Lock()
        self._job_cond = threading.Condition(self._jobs_lock)

    # ---- 幂等（交给 store，可跨进程）----
    def idem_begin(self, key: tuple) -> tuple[str, dict | None]:
        return self.store.idem_begin(_idem_key(key))

    def idem_finish(self, key: tuple, code: int, payload: dict) -> None:
        self.store.idem_finish(_idem_key(key), code, payload)

    def idem_get(self, key: tuple):
        return self.store.idem_get(_idem_key(key))

    # ---- 任务表 ----
    def new_job(self) -> str:
        run_id = uuid.uuid4().hex[:16]
        with self._jobs_lock:
            self.jobs[run_id] = {"run_id": run_id, "status": "queued",
                                 "created": time.time(), "result": None, "error": None,
                                 "seq": 0, "events": [], "done": False}
            event = self.job_emit_locked(run_id, {"stage": "queued"})
            self._gc_jobs()
        self._persist(run_id, event)
        return run_id

    def _gc_jobs(self):
        now = time.time()
        for rid in [r for r, j in self.jobs.items()
                    if now - j["created"] > self.config.job_ttl]:
            self.jobs.pop(rid, None)

    def set_job(self, run_id: str, **fields):
        event = None
        with self._jobs_lock:
            job = self.jobs.get(run_id)
            if not job:
                return
            job.update(fields)
            if fields.get("status") in ("succeeded", "failed", "cancelled"):
                job["done"] = True
                event = self.job_emit_locked(run_id, {"stage": job["status"]})
            self._job_cond.notify_all()   # Condition 必须在持锁时 notify
        self._persist(run_id, event)

    def get_job(self, run_id: str):
        with self._jobs_lock:
            job = self.jobs.get(run_id)
            if job:
                return dict(job)
        events, done = self.store.job_read(run_id, 0)
        if not events:
            return None
        return {"run_id": run_id, "status": "recovered", "persisted": True,
                "seq": events[-1].get("seq", 0), "events": events, "done": done}

    # ---- 作业事件（SSE 用）----
    def job_emit(self, run_id: str, event: dict):
        with self._jobs_lock:
            stored = self.job_emit_locked(run_id, dict(event))
            self._job_cond.notify_all()
        self._persist(run_id, stored)

    def _persist(self, run_id: str, event: dict | None):
        if not event:
            return
        try:
            self.store.job_append(run_id, event)
        except Exception:                     # 持久化失败不该拖垮调用链
            self.metrics.inc("job_persist_errors_total")

    def job_emit_locked(self, run_id: str, event: dict):
        """必须在持有 _jobs_lock 时调用（new_job / set_job 内部用）。"""
        job = self.jobs.get(run_id)
        if not job:
            return None
        job["seq"] += 1
        event = {**event, "seq": job["seq"], "ts": time.time()}
        job["events"].append(event)
        if len(job["events"]) > 200:          # 只留最近 200 条，防长任务撑爆内存
            del job["events"][:-200]
        return event

    def job_events(self, run_id: str, after: int, timeout: float = 10.0):
        """等到有新事件或作业结束，返回 (事件列表, 是否结束)。"""
        with self._job_cond:
            job = self.jobs.get(run_id)
            if job is None:
                return self.store.job_read(run_id, after)   # 重启后从 store 回放
            if job["seq"] <= after and not job["done"]:
                self._job_cond.wait(timeout)
            job = self.jobs.get(run_id) or {}
            events = [e for e in job.get("events", []) if e["seq"] > after]
            return events, bool(job.get("done"))

    def close(self):
        try:
            self.pool.close()
        finally:
            self.store.close()


# ----------------------------- 路由 -----------------------------
def _make_handler(state: GatewayState):
    class Handler(BaseHTTPRequestHandler):
        server_version = "agentrpa-bridge/0.2"
        protocol_version = "HTTP/1.1"

        # ---- 基础工具 ----
        def log_message(self, fmt, *args):
            return

        def _send(self, code: int, payload, ctype="application/json"):
            body = payload if isinstance(payload, bytes) else json.dumps(
                payload, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype + "; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Request-Id", getattr(self, "request_id", "-"))
            if self.close_connection:
                self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)

        def _drain(self, length: int, chunk: int = 256 * 1024):
            """把 body 读掉丢弃，避免 keep-alive 上残留字节被当成下一个请求。"""
            remaining = length
            while remaining > 0:
                try:
                    blob = self.rfile.read(min(chunk, remaining))
                except (OSError, ValueError):
                    return
                if not blob:
                    return
                remaining -= len(blob)

        def _read_json(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0:
                return {}
            if length > MAX_BODY:
                # 先尽量把 body 读掉：否则客户端在写的时候就被 RST，看不到 413
                self._drain(min(length, DRAIN_CAP))
                self.close_connection = True
                raise PayloadTooLarge(f"请求体 {length} 字节，超过上限 {MAX_BODY}")
            raw = self.rfile.read(length)
            try:
                return json.loads(raw.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                self.close_connection = True
                raise ValueError(f"请求体不是合法 JSON：{exc}")

        def _tenant(self):
            header = self.headers.get("Authorization") or ""
            token = header[7:].strip() if header.lower().startswith("bearer ") else ""
            if token:
                # 没配 tokens 时不再"任意 token 放行"，必须逐个常量时间比对
                for known, tenant in state.config.tokens.items():
                    if secrets.compare_digest(token, known):
                        return tenant
                return None
            if state.config.allow_anon_localhost and \
                    self.client_address[0] in ("127.0.0.1", "::1"):
                return "localhost"
            return None

        def _reject(self, code: int, payload: dict):
            """拒绝请求前先把 body 读掉并关闭连接，否则客户端可能在写 body 时被 RST。"""
            length = int(self.headers.get("Content-Length") or 0)
            if length > 0:
                self._drain(min(length, DRAIN_CAP))
            self.close_connection = True
            self._send(code, payload)

        def _guard(self):
            self.request_id = uuid.uuid4().hex[:12]
            tenant = self._tenant()
            if tenant is None:
                state.metrics.inc("requests_total")
                state.metrics.inc("auth_failures_total")
                self._reject(401, {"error": "unauthorized", "request_id": self.request_id})
                return None
            self.tenant = tenant
            state.metrics.inc("requests_total")
            if not state.store.allow(tenant, state.config.rate_limit_per_min):
                state.metrics.inc("rate_limited_total")
                self._reject(429, {"error": "rate limited",
                                   "limit_per_min": state.config.rate_limit_per_min,
                                   "request_id": self.request_id})
                return None
            return tenant

        # ---- GET ----
        def do_GET(self):
            path = self.path.split("?")[0].rstrip("/") or "/"
            if path == "/healthz":
                self._send(200, {"status": "ok", "uptime_s": int(time.time() - state.metrics.started),
                                 "agents": state.pool.snapshot(),
                                 "inflight": state.metrics.active})
                return
            if self._guard() is None:
                return
            if path == "/v1/agents":
                self._send(200, {"agents": [state.pool.specs[n].public() for n in state.pool.names()],
                                 "sessions": state.pool.snapshot()})
            elif path.startswith("/v1/jobs/") and path.endswith("/events"):
                run_id = path.split("/")[-2]
                query = self.path.partition("?")[2]
                after = 0
                for pair in query.split("&"):
                    if pair.startswith("since="):
                        try:
                            after = int(pair[6:])
                        except ValueError:
                            after = 0
                try:
                    after = max(after, int(self.headers.get("Last-Event-ID") or 0))
                except ValueError:
                    pass
                self._stream_job(run_id, after)
            elif path.startswith("/v1/jobs/"):
                job = state.get_job(path.rsplit("/", 1)[-1])
                if not job:
                    self._send(404, {"error": "job not found"})
                else:
                    self._send(200, job)
            elif path == "/metrics":
                self._send(200, state.metrics.render(
                    {"configured_agents": len(state.pool.names())}).encode("utf-8"),
                    ctype="text/plain")
            else:
                self._send(404, {"error": "not found"})

        def _sse(self, event: str, data: dict):
            payload = (f"id: {data.get('seq', 0)}\n"
                       f"event: {event}\n"
                       f"data: {json.dumps(data, ensure_ascii=False)}\n\n")
            self.wfile.write(payload.encode("utf-8"))
            self.wfile.flush()

        def _stream_job(self, run_id: str, after: int):
            """把作业事件按 SSE 推给客户端；支持 Last-Event-ID / since 续传。"""
            job = state.get_job(run_id)
            if not job:
                self._send(404, {"error": "job not found"})
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Accel-Buffering", "no")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            last = after
            try:
                while True:
                    events, done = state.job_events(run_id, last, timeout=10.0)
                    for event in events:
                        last = event["seq"]
                        self._sse("progress", event)
                    if done and not events:
                        self._sse("end", {"seq": last})
                        return
                    if not events and not done:
                        self._sse("ping", {"seq": last})   # 心跳，防代理断连
            except (BrokenPipeError, ConnectionResetError, OSError):
                return

        # ---- POST ----
        def do_POST(self):
            path = self.path.split("?")[0].rstrip("/") or "/"
            tenant = self._guard()
            if tenant is None:
                return
            try:
                body = self._read_json()
            except PayloadTooLarge as exc:
                self._send(413, {"error": "payload too large", "detail": str(exc),
                                 "request_id": self.request_id})
                return
            except ValueError as exc:
                self._send(400, {"error": str(exc), "request_id": self.request_id})
                return
            if path not in ("/v1/invoke", "/v1/jobs"):
                self._send(404, {"error": "not found"})
                return
            agent = (body.get("agent") or "codex").strip()
            prompt = body.get("prompt") or body.get("text") or ""
            if not prompt:
                self._send(400, {"error": "缺少 prompt"})
                return
            cwd = body.get("cwd")
            timeout = body.get("timeout_s")
            meta = body.get("meta") or {}
            meta.setdefault("tenant", tenant)
            if path == "/v1/jobs":
                run_id = state.new_job()
                threading.Thread(
                    target=_invoke_once,
                    args=(state, run_id, agent, prompt, cwd, timeout, meta, run_id, None),
                    daemon=True).start()
                self._send(202, {"run_id": run_id, "status": "queued"})
                return

            # 幂等：RPA 超时重试时，同一个 Idempotency-Key 不会把 agent 跑两遍
            raw_key = (self.headers.get("Idempotency-Key") or "").strip()
            idem_key = (tenant, raw_key) if raw_key else None
            if idem_key:
                verdict, cached, code = state.idem_begin(idem_key)
                if verdict == "done":
                    payload = dict(cached or {})
                    payload["idempotent_replay"] = True
                    self._send(code or 200, payload)
                    return
                if verdict == "inflight":
                    deadline = time.monotonic() + state.config.queue_timeout
                    while time.monotonic() < deadline:
                        time.sleep(0.05)
                        hit = state.idem_get(idem_key)
                        if hit and hit["done"]:
                            payload = dict(hit["payload"])
                            payload["idempotent_replay"] = True
                            self._send(hit["code"] or 200, payload)
                            return
                    self._reject(409, {"error": "duplicate request in flight",
                                       "request_id": self.request_id})
                    return

            captured = {}

            def _send_and_capture(code, payload):
                captured["code"] = code
                captured["payload"] = payload
                self._send(code, payload)

            try:
                _invoke_once(state, self.request_id, agent, prompt, cwd, timeout, meta,
                             None, _send_and_capture)
            finally:
                if idem_key:
                    state.idem_finish(idem_key, captured.get("code", 502),
                                      captured.get("payload", {}))

    return Handler


def _invoke_once(state: GatewayState, request_id: str, agent: str, prompt: str,
                 cwd, timeout, meta: dict, job_id: str | None, send):
    """同步/异步共用的执行体。send 为 None 时结果只写进任务表。"""
    if not state.semaphore.acquire(timeout=state.config.queue_timeout):
        state.metrics.inc("queue_timeout_total")
        if job_id:
            state.set_job(job_id, status="failed", error="busy", finished=time.time())
        else:
            send(503, {"error": "busy", "detail": "并发已满且排队超时"})
        return
    state.metrics.active += 1
    started = time.time()
    try:
        emit = (lambda event: state.job_emit(job_id, event)) if job_id else None
        if emit:
            emit({"stage": "running", "agent": agent})
        result = state.pool.invoke(agent, prompt, cwd=cwd, meta=meta,
                                   timeout=float(timeout) if timeout else None,
                                   on_event=emit)
        state.metrics.observe(agent, result["duration_ms"])
        state.metrics.inc("invoke_ok_total")
        payload = {"run_id": job_id, "status": "succeeded", "agent": result["agent"],
                   "text": result["text"], "duration_ms": result["duration_ms"],
                   "request_id": request_id}
        state.audit.write({"event": "invoke", "tenant": meta.get("tenant"),
                           "flow": meta.get("flow"), "agent": agent,
                           "prompt_sha256": _sha(prompt), "prompt_head": redact(prompt[:200]),
                           "status": "succeeded", "text_len": len(result["text"]),
                           "duration_ms": result["duration_ms"],
                           "request_id": request_id, "job": job_id})
        if job_id:
            state.set_job(job_id, status="succeeded", result=payload, finished=time.time())
        else:
            send(200, payload)
    except AgentError as exc:
        state.metrics.inc("invoke_error_total")
        msg = redact(str(exc))
        state.audit.write({"event": "invoke", "tenant": meta.get("tenant"),
                           "flow": meta.get("flow"), "agent": agent,
                           "prompt_sha256": _sha(prompt), "prompt_head": redact(prompt[:200]),
                           "status": "failed", "error": msg,
                           "duration_ms": int((time.time() - started) * 1000),
                           "request_id": request_id, "job": job_id})
        if job_id:
            state.set_job(job_id, status="failed", error=msg, finished=time.time())
        else:
            send(502, {"error": "agent failed", "detail": msg, "request_id": request_id})
    finally:
        state.metrics.active -= 1
        state.semaphore.release()


def _sha(text: str) -> str:
    import hashlib
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


# ----------------------------- 启动 -----------------------------
def create_server(config: GatewayConfig, pool: AgentPool | None = None):
    state = GatewayState(config, pool)
    handler = _make_handler(state)
    httpd = ThreadingHTTPServer((config.host, config.port), handler)
    httpd.daemon_threads = True
    httpd.state = state
    return httpd


def main(argv=None):
    parser = argparse.ArgumentParser(description="agentrpa-bridge HTTP 网关")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args(argv)
    config = GatewayConfig.from_env()
    if args.host:
        config.host = args.host
    if args.port:
        config.port = args.port
    if not config.tokens and not config.allow_anon_localhost:
        raise SystemExit("没有配置 AGENTRPA_TOKENS，且关闭了本地匿名访问")
    httpd = create_server(config)
    print(f"agentrpa-bridge gateway on http://{config.host}:{config.port} "
          f"(agents: {', '.join(httpd.state.pool.names())})")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.state.close()
        httpd.server_close()


if __name__ == "__main__":
    main()
