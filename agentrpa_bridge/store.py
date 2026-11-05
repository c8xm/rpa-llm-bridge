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


class MemoryStore:
    """进程内实现；行为与 v2.3 一致，单节点默认。"""

    kind = "memory"

    def __init__(self, ttl: float = 900.0):
        self.ttl = ttl
        self._lock = threading.RLock()
        self._hits = {}          # tenant -> deque[ts]
        self._idem = {}          # key -> dict(done, code, payload, ts)

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


def make_store(kind: str, path: str, ttl: float):
    kind = (kind or "memory").lower()
    if kind == "memory":
        return MemoryStore(ttl=ttl)
    if kind == "sqlite":
        return SQLiteStore(path, ttl=ttl)
    raise ValueError(f"未知的 AGENTRPA_STATE_BACKEND：{kind}（可选 memory / sqlite）")
