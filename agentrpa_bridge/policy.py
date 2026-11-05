"""企业护栏：应用白名单、审批清单、单应用并发、幂等窗口。

为什么放在反向 MCP 进程里而不是网关：
  这里的动作是"触发企业 RPA 应用"，属于对内部系统的写操作，
  比模型调用更需要按"应用"维度管，而不是按 tenant。

四条规则：
  1. 白名单外的应用一律拒绝（生产必填，空 = 开发模式放行）；
  2. 审批清单内的应用要求 approved_by（审批人工号/工单号）；
  3. 单应用并发上限 = 该应用可用机器人数，满了直接 busy，不排队；
  4. 同参数在幂等窗口内重复提交，返回同一个 jobId，不重复跑。
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time

TERMINAL_STATUS = {"success", "failed", "error", "cancelled", "not_found"}


class PolicyDenied(RuntimeError):
    """护栏拒绝。code 用于 RPA 侧分支判断。"""

    def __init__(self, code: str, message: str, **detail):
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail

    def as_dict(self) -> dict:
        return {"error": self.code, "message": self.message, **self.detail}


class GuardConfig:
    def __init__(self, allowed_apps=None, approval_apps=None,
                 max_per_app: int = 4, idem_ttl: float = 900.0,
                 max_hold: float = 3600.0):
        self.allowed_apps = {a.strip() for a in (allowed_apps or []) if a and a.strip()}
        self.approval_apps = {a.strip() for a in (approval_apps or []) if a and a.strip()}
        self.max_per_app = max(1, int(max_per_app))
        self.idem_ttl = max(0.0, float(idem_ttl))
        # 没人轮询终态时，槽位最多占这么久；超时自动回收，避免全体 busy
        self.max_hold = max(0.0, float(max_hold))

    @classmethod
    def from_env(cls) -> "GuardConfig":
        def split(name):
            return [x for x in os.environ.get(name, "").split(",") if x.strip()]

        return cls(
            allowed_apps=split("YINGDAO_ALLOWED_APPS"),
            approval_apps=split("YINGDAO_APPROVAL_APPS"),
            max_per_app=int(os.environ.get("YINGDAO_MAX_PER_APP", "4")),
            idem_ttl=float(os.environ.get("YINGDAO_IDEMPOTENCY_TTL", "900")),
            max_hold=float(os.environ.get("YINGDAO_MAX_HOLD", "3600")),
        )


class RpaGuard:
    def __init__(self, config: GuardConfig | None = None, clock=time.monotonic):
        self.config = config or GuardConfig.from_env()
        self._clock = clock
        self._lock = threading.RLock()
        self._holds = {}         # app_id -> [acquire_ts, ...]
        self._idem = {}          # key -> (job_id, ts)
        self._job_app = {}       # job_id -> app_id
        self._stats = {"denied": 0, "deduplicated": 0, "busy": 0,
                       "approved": 0, "expired": 0}

    # ---------- 规则 1：白名单 ----------
    def check_app(self, app_id: str) -> None:
        allowed = self.config.allowed_apps
        if allowed and app_id not in allowed:
            self._bump("denied")
            raise PolicyDenied("app_not_allowed",
                               f"应用 {app_id} 不在白名单内",
                               allowed=sorted(allowed))

    # ---------- 规则 2：审批 ----------
    def requires_approval(self, app_id: str) -> bool:
        return app_id in self.config.approval_apps

    def check_approval(self, app_id: str, approved_by: str) -> None:
        if self.requires_approval(app_id) and not (approved_by or "").strip():
            self._bump("denied")
            raise PolicyDenied(
                "requires_approval",
                f"应用 {app_id} 属于审批清单，必须提供 approved_by（审批人工号或工单号）",
                app_id=app_id)
        if self.requires_approval(app_id):
            self._bump("approved")

    # ---------- 规则 4：幂等 ----------
    def idem_key(self, app_id: str, params: dict) -> str:
        blob = json.dumps({"app": app_id, "params": params or {}},
                          ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]

    def lookup(self, key: str) -> str | None:
        if self.config.idem_ttl <= 0:
            return None
        now = self._clock()
        with self._lock:
            item = self._idem.get(key)
            if not item:
                return None
            job_id, ts = item
            if now - ts > self.config.idem_ttl:
                self._idem.pop(key, None)
                return None
            return job_id

    def commit(self, key: str, job_id: str, app_id: str) -> None:
        with self._lock:
            if self.config.idem_ttl > 0:
                self._idem[key] = (job_id, self._clock())
            self._job_app[job_id] = app_id

    # ---------- 规则 3：并发 ----------
    def _sweep(self) -> None:
        """回收超过 max_hold 的槽位：没人轮询终态时不会永久占坑。"""
        if self.config.max_hold <= 0:
            return
        now = self._clock()
        for app_id in list(self._holds):
            kept = [ts for ts in self._holds[app_id] if now - ts <= self.config.max_hold]
            dropped = len(self._holds[app_id]) - len(kept)
            if dropped:
                self._stats["expired"] = self._stats.get("expired", 0) + dropped
            if kept:
                self._holds[app_id] = kept
            else:
                self._holds.pop(app_id, None)

    def acquire(self, app_id: str) -> None:
        with self._lock:
            self._sweep()
            holds = self._holds.setdefault(app_id, [])
            if len(holds) >= self.config.max_per_app:
                self._bump("busy")
                raise PolicyDenied("busy", f"应用 {app_id} 并发已满（上限 {self.config.max_per_app}）",
                                   app_id=app_id, limit=self.config.max_per_app)
            holds.append(self._clock())

    def release(self, app_id: str) -> None:
        with self._lock:
            holds = self._holds.get(app_id)
            if not holds:
                return
            holds.pop(0)          # 先回收最早占用的那一格
            if not holds:
                self._holds.pop(app_id, None)

    def observe(self, job_id: str, status: str) -> None:
        """轮询到终态时释放并发槽位。"""
        if status not in TERMINAL_STATUS:
            return
        with self._lock:
            app_id = self._job_app.pop(job_id, None)
        if app_id:
            self.release(app_id)

    def mark_duplicate(self) -> None:
        self._bump("deduplicated")

    # ---------- 观测 ----------
    def _bump(self, name: str) -> None:
        with self._lock:
            self._stats[name] = self._stats.get(name, 0) + 1

    def snapshot(self) -> dict:
        with self._lock:
            self._sweep()
            return {
                "whitelist_enforced": bool(self.config.allowed_apps),
                "allowed_apps": sorted(self.config.allowed_apps),
                "approval_apps": sorted(self.config.approval_apps),
                "max_per_app": self.config.max_per_app,
                "max_hold_s": self.config.max_hold,
                "idempotency_ttl_s": self.config.idem_ttl,
                "inflight": {k: len(v) for k, v in self._holds.items()},
                "stats": dict(self._stats),
            }
