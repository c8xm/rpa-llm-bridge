"""驱动抽象：action 名字统一，底层能力各自实现。"""

from __future__ import annotations

from typing import Any

from ..config import Settings
from ..core.audit import AuditLog
from ..core.policy import Policy


class DriverError(RuntimeError):
    pass


class PolicyBlocked(DriverError):
    """驱动在执行期做出的策略拒绝，可携带人工接管标记。"""

    def __init__(self, reason: str, requires_human: bool = False):
        super().__init__(reason)
        self.requires_human = requires_human


class BaseDriver:
    name = "base"

    def __init__(self, settings: Settings, policy: Policy, audit: AuditLog):
        self.settings = settings
        self.policy = policy
        self.audit = audit

    # 生命周期
    async def start(self) -> dict[str, Any]:
        return {"driver": self.name, "started": True}

    async def close(self) -> dict[str, Any]:
        return {"driver": self.name, "closed": True}

    async def health(self) -> dict[str, Any]:
        return {"driver": self.name, "ok": True}

    # 能力声明，供 MCP 客户端发现
    def capabilities(self) -> list[str]:
        return []

    async def execute(self, action: str, params: dict[str, Any]) -> Any:
        raise DriverError(f"{self.name} 不支持 action: {action}")
