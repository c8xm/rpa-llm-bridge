"""共享状态后端：限速与幂等。

单机默认用内存；多进程/多节点共用一台机器（或共享盘）时切 SQLite。
接口只有四个动作，换成 Redis 只需再实现一个类。

    AGENTRPA_STATE_BACKEND=memory | sqlite
    AGENTRPA_STATE_PATH=artifacts/bridge-state.db
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from collections import deque

RATE_WINDOW = 60.0
TERMINAL_STAGES = {"succeeded", "failed", "cancelled"}


class MemoryStore:
    """进程内实现；行为与 v2.3 一致，单节点默认。"""

    kind = "memory"

    def __init__(self, ttl: float = 900.0):
        self.ttl = ttl
        self._lock = threading.RLock()
        self._hits = {}          # tenant -> deque[ts]
        self._idem = {}          # key -> dict(done, code, payload, ts)
        self._jobs = {}          # run_id -> [event]（仅本进程可见）
        self._jobs_done = set()

    # ---- 限速 ----
    def allow(self, tenant: str, limit: int, window: float = RATE_WINDOW) -> bool:
        if limit <= 0:
            return True
        now = time.monotonic()
        with self._lock:
            bucket = self._hits.setdefault(tenant, deque())
            while bucket and now - bucket[0] > window:
                bucket.popleft()
            if len(bucket) >= limit:
                return False
            bucket.append(now)
            return True

    # ---- 幂等 ----
    def idem_begin(self, key: str) -> tuple[str, dict | None, int]:
        with self._lock:
            self._sweep()
            entry = self._idem.get(key)
            if entry:
                if entry["done"]:
                    return "done", entry["payload"], entry["code"]
                return "inflight", None, 0
            self._idem[key] = {"done": False, "code": 0, "payload": {}, "ts": time.time()}
            return "new", None, 0

    def idem_finish(self, key: str, code: int, payload: dict) -> None:
        with self._lock:
            if key in self._idem:
                self._idem[key].update({"done": True, "code": code,
                                        "payload": payload, "ts": time.time()})

    def idem_get(self, key: str) -> dict | None:
        with self._lock:
            entry = self._idem.get(key)
            if not entry:
                return None
            return {"done": entry["done"], "code": entry["code"],
                    "payload": entry["payload"]}

    def _sweep(self) -> None:
        if self.ttl <= 0:
            self._idem.clear()
            return
        now = time.time()
        for k in [k for k, v in self._idem.items() if now - v["ts"] > self.ttl]:
            self._idem.pop(k, None)

    # ---- 作业事件（进程内，重启即丢）----
    def job_append(self, run_id: str, event: dict) -> None:
        with self._lock:
            entry = self._jobs.setdefault(run_id, [])
            entry.append(event)
            if len(entry) > 200:
                del entry[:-200]
            if event.get("stage") in TERMINAL_STAGES:
                self._jobs_done.add(run_id)

    def job_read(self, run_id: str, after: int = 0):
        with self._lock:
            events = list(self._jobs.get(run_id, []))
            done = run_id in self._jobs_done      # 看整体，不看切片
        return [e for e in events if e.get("seq", 0) > after], done

    def close(self) -> None:
        pass


class SQLiteStore:
    """SQLite 实现：同机多进程共享；跨机请把库放到共享盘或换 Redis。

    - 限速：一张 (tenant, ts) 表，60 秒滑动窗口；
    - 幂等：一张 (key, done, code, payload, ts) 表，靠 BEGIN IMMEDIATE 保证跨进程互斥。
    """

    kind = "sqlite"

    def __init__(self, path: str, ttl: float = 900.0):
        self.ttl = ttl
        self.path = path
        directory = os.path.dirname(os.path.abspath(path))
        if directory:
            os.makedirs(directory, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, timeout=5.0, isolation_level=None,
                                     check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("CREATE TABLE IF NOT EXISTS rate (tenant TEXT, ts REAL)")
        self._conn.execute("CREATE INDEX IF NOT EXISTS rate_idx ON rate(tenant, ts)")
        self._conn.execute("CREATE TABLE IF NOT EXISTS idem ("
                           "k TEXT PRIMARY KEY, done INTEGER, code INTEGER,"
                           "payload TEXT, ts REAL)")
        self._conn.execute("CREATE TABLE IF NOT EXISTS job_event ("
                           "run_id TEXT, seq INTEGER, ts REAL, data TEXT,"
                           "PRIMARY KEY (run_id, seq))")

    # ---- 限速 ----
    def allow(self, tenant: str, limit: int, window: float = RATE_WINDOW) -> bool:
        if limit <= 0:
            return True
        now = time.time()
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                self._conn.execute("DELETE FROM rate WHERE ts < ?", (now - window,))
                count = self._conn.execute(
                    "SELECT COUNT(*) FROM rate WHERE tenant = ?", (tenant,)).fetchone()[0]
                if count >= limit:
                    self._conn.execute("COMMIT")
                    return False
                self._conn.execute("INSERT INTO rate (tenant, ts) VALUES (?, ?)",
                                   (tenant, now))
                self._conn.execute("COMMIT")
                return True
            except sqlite3.Error:
                try:
                    self._conn.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise

    # ---- 幂等 ----
    def idem_begin(self, key: str) -> tuple[str, dict | None, int]:
        now = time.time()
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                row = self._conn.execute(
                    "SELECT done, payload, ts, code FROM idem WHERE k = ?", (key,)).fetchone()
                if row is None:
                    self._conn.execute(
                        "INSERT INTO idem (k, done, code, payload, ts) VALUES (?, 0, 0, '{}', ?)",
                        (key, now))
                    self._conn.execute("COMMIT")
                    return "new", None, 0
                done, payload, ts, code = row
                if self.ttl > 0 and now - ts > self.ttl:
                    self._conn.execute("DELETE FROM idem WHERE k = ?", (key,))
                    self._conn.execute(
                        "INSERT INTO idem (k, done, code, payload, ts) VALUES (?, 0, 0, '{}', ?)",
                        (key, now))
                    self._conn.execute("COMMIT")
                    return "new", None, 0
                self._conn.execute("COMMIT")
                if done:
                    return "done", json.loads(payload or "{}"), code or 200
                return "inflight", None, 0
            except sqlite3.Error:
                try:
                    self._conn.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise

    def idem_finish(self, key: str, code: int, payload: dict) -> None:
        blob = json.dumps(payload, ensure_ascii=False)
        with self._lock:
            with self._conn:
                self._conn.execute(
                    "INSERT INTO idem (k, done, code, payload, ts) VALUES (?, 1, ?, ?, ?) "
                    "ON CONFLICT(k) DO UPDATE SET done=1, code=excluded.code, "
                    "payload=excluded.payload, ts=excluded.ts",
                    (key, code, blob, time.time()))

    def idem_get(self, key: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT done, code, payload FROM idem WHERE k = ?", (key,)).fetchone()
        if not row:
            return None
        done, code, payload = row
        return {"done": bool(done), "code": code, "payload": json.loads(payload or "{}")}

    # ---- 作业事件（落盘，重启可回放）----
    def job_append(self, run_id: str, event: dict) -> None:
        seq = int(event.get("seq") or 0)
        with self._lock:
            with self._conn:
                self._conn.execute(
                    "INSERT OR REPLACE INTO job_event (run_id, seq, ts, data) "
                    "VALUES (?, ?, ?, ?)",
                    (run_id, seq, float(event.get("ts") or time.time()),
                     json.dumps(event, ensure_ascii=False)))
                self._conn.execute(
                    "DELETE FROM job_event WHERE run_id = ? AND seq <= ?",
                    (run_id, seq - 200))

    def job_read(self, run_id: str, after: int = 0):
        with self._lock:
            rows = self._conn.execute(
                "SELECT data FROM job_event WHERE run_id = ? AND seq > ? "
                "ORDER BY seq", (run_id, after)).fetchall()
        events = [json.loads(row[0]) for row in rows]
        # done 看整体最后一条：否则 after 之后的切片为空时会永远等不到 end
        with self._lock:
            last = self._conn.execute(
                "SELECT data FROM job_event WHERE run_id = ? ORDER BY seq DESC LIMIT 1",
                (run_id,)).fetchone()
        done = bool(last) and json.loads(last[0]).get("stage") in TERMINAL_STAGES
        return events, done

    def sweep(self) -> None:
        if self.ttl <= 0:
            return
        with self._lock:
            with self._conn:
                self._conn.execute("DELETE FROM idem WHERE done = 1 AND ts < ?",
                                   (time.time() - self.ttl,))
                self._conn.execute("DELETE FROM rate WHERE ts < ?",
                                   (time.time() - RATE_WINDOW * 2,))

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except sqlite3.Error:
                pass


class _Resp:
    """极简 Redis 客户端（RESP2）：只支持本模块用到的命令，零第三方依赖。"""

    def __init__(self, host="127.0.0.1", port=6379, db=0, password="", tls=False,
                 timeout=5.0):
        import ssl
        self.addr = (host, port)
        self.db = db
        self.password = password
        self.tls = tls
        self.timeout = timeout
        self._sock = None
        self._fp = None
        self._ssl = ssl

    def connect(self):
        import socket  # noqa: F401  (便于测试替换)
        raw = socket.create_connection(self.addr, timeout=self.timeout)
        if self.tls:
            raw = self._ssl.create_default_context().wrap_socket(raw,
                                                                 server_hostname=self.addr[0])
        self._sock = raw
        self._fp = raw.makefile("rb")
        if self.password:
            self.cmd("AUTH", self.password)
        if self.db:
            self.cmd("SELECT", str(self.db))
        return self

    def close(self):
        for obj in (self._fp, self._sock):
            try:
                if obj:
                    obj.close()
            except OSError:
                pass
        self._fp = self._sock = None

    def _ensure(self):
        if self._sock is None:
            self.connect()

    def _encode(self, args):
        out = [f"*{len(args)}\r\n".encode()]
        for arg in args:
            blob = str(arg).encode()
            out.append(f"${len(blob)}\r\n".encode() + blob + b"\r\n")
        return b"".join(out)

    def _read(self):
        line = self._fp.readline()
        if not line:
            raise ConnectionError("redis 连接已关闭")
        kind, body = line[:1], line[1:-2]
        if kind == b"+":
            return body.decode()
        if kind == b"-":
            raise RuntimeError(body.decode())
        if kind == b":":
            return int(body)
        if kind == b"$":
            size = int(body)
            if size == -1:
                return None
            data = self._fp.read(size + 2)[:-2]
            return data.decode("utf-8", "replace")
        if kind == b"*":
            count = int(body)
            if count == -1:
                return None
            return [self._read() for _ in range(count)]
        raise RuntimeError(f"无法解析的 RESP 响应：{line!r}")

    def cmd(self, *args):
        self._ensure()
        try:
            self._sock.sendall(self._encode(args))
            return self._read()
        except (ConnectionError, OSError):
            self.close()
            raise


class RedisStore:
    """Redis 实现：多机共享限速、幂等与作业事件。

    - 限速：固定窗口（INCR + PEXPIRE），比内存/SQLite 的滑动窗口更省往返；
    - 幂等：SET NX PX 抢占，值为 JSON（done/code/payload）；
    - 作业事件：每个作业一个 List，LTRIM 保留最近 200 条，终态另置 done 键。
    """

    kind = "redis"
    PREFIX = "rpa:"

    def __init__(self, url: str = "redis://127.0.0.1:6379/0", ttl: float = 900.0,
                 timeout: float = 5.0):
        self.ttl = ttl
        self.url = url
        self.client = _Resp(**self._parse(url), timeout=timeout)

    @staticmethod
    def _parse(url: str) -> dict:
        from urllib.parse import urlparse
        parsed = urlparse(url if "://" in url else f"redis://{url}")
        return {
            "host": parsed.hostname or "127.0.0.1",
            "port": parsed.port or 6379,
            "db": int((parsed.path or "/0").strip("/") or 0),
            "password": parsed.password or "",
            "tls": parsed.scheme == "rediss",
        }

    def _key(self, suffix: str) -> str:
        return f"{self.PREFIX}{suffix}"

    # ---- 限速 ----
    def allow(self, tenant: str, limit: int, window: float = RATE_WINDOW) -> bool:
        if limit <= 0:
            return True
        bucket = int(time.time() // window)
        key = self._key(f"rl:{tenant}:{bucket}")
        count = self.client.cmd("INCR", key)
        if count == 1:
            self.client.cmd("PEXPIRE", key, int(window * 1000))
        return int(count) <= limit

    # ---- 幂等 ----
    def idem_begin(self, key: str) -> tuple[str, dict | None, int]:
        full = self._key(f"idem:{key}")
        pending = json.dumps({"done": False, "code": 0, "payload": {}})
        args = ["SET", full, pending, "NX"]
        if self.ttl > 0:
            args += ["PX", int(self.ttl * 1000)]
        if self.client.cmd(*args):
            return "new", None, 0
        raw = self.client.cmd("GET", full)
        if not raw:
            return "new", None, 0
        entry = json.loads(raw)
        if entry.get("done"):
            return "done", entry.get("payload") or {}, entry.get("code") or 200
        return "inflight", None, 0

    def idem_finish(self, key: str, code: int, payload: dict) -> None:
        full = self._key(f"idem:{key}")
        blob = json.dumps({"done": True, "code": code, "payload": payload},
                          ensure_ascii=False)
        if self.ttl > 0:
            self.client.cmd("SET", full, blob, "PX", int(self.ttl * 1000))
        else:
            self.client.cmd("SET", full, blob)

    def idem_get(self, key: str) -> dict | None:
        raw = self.client.cmd("GET", self._key(f"idem:{key}"))
        if not raw:
            return None
        entry = json.loads(raw)
        return {"done": bool(entry.get("done")), "code": entry.get("code") or 0,
                "payload": entry.get("payload") or {}}

    # ---- 作业事件 ----
    def job_append(self, run_id: str, event: dict) -> None:
        key = self._key(f"job:{run_id}")
        self.client.cmd("RPUSH", key, json.dumps(event, ensure_ascii=False))
        self.client.cmd("LTRIM", key, -200, -1)
        self.client.cmd("PEXPIRE", key, 86400 * 1000)
        if event.get("stage") in TERMINAL_STAGES:
            self.client.cmd("SET", self._key(f"job:{run_id}:done"), "1",
                            "PX", 86400 * 1000)

    def job_read(self, run_id: str, after: int = 0):
        raw = self.client.cmd("LRANGE", self._key(f"job:{run_id}"), 0, -1) or []
        events = [json.loads(item) for item in raw]
        events = [e for e in events if e.get("seq", 0) > after]
        done = bool(self.client.cmd("EXISTS", self._key(f"job:{run_id}:done")))
        return events, done

    def sweep(self) -> None:
        return

    def close(self) -> None:
        self.client.close()


def make_store(kind: str, path: str, ttl: float, url: str = ""):
    kind = (kind or "memory").lower()
    if kind == "memory":
        return MemoryStore(ttl=ttl)
    if kind == "sqlite":
        return SQLiteStore(path, ttl=ttl)
    if kind == "redis":
        return RedisStore(url or os.environ.get("AGENTRPA_REDIS_URL",
                                                "redis://127.0.0.1:6379/0"), ttl=ttl)
    raise ValueError(f"未知的 AGENTRPA_STATE_BACKEND：{kind}（可选 memory / sqlite / redis）")
