"""测试夹具：临时 Settings、假驱动。"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentrpa.config import Settings
from agentrpa.core.audit import AuditLog
from agentrpa.core.policy import Policy
from agentrpa.core.types import Step


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    s = Settings(
        artifacts_dir=tmp_path / "artifacts",
        audit_dir=tmp_path / "audit",
        runs_dir=tmp_path / "runs",
        recipes_dir=tmp_path / "recipes",
        profile_dir=tmp_path / "profile",
        action_delay_ms=0,
    )
    s.ensure_dirs()
    return s


@pytest.fixture
def policy() -> Policy:
    return Policy(allowed_domains=["example.com", "jd.com"], allowed_apps=["notepad.exe"])


@pytest.fixture
def audit(settings: Settings) -> AuditLog:
    return AuditLog(settings.audit_dir)


class FakeDriver:
    """记录调用，并可注入失败。"""

    def __init__(self, fail_on: set[str] | None = None):
        self.calls: list[tuple[str, dict]] = []
        self.fail_on = fail_on or set()

    async def execute(self, action: str, params: dict):
        self.calls.append((action, params))
        if action in self.fail_on:
            raise RuntimeError(f"boom:{action}")
        return {"ok": True, "action": action, "params": params}


def make_step(step_id: str = "s1", action: str = "ping", **kwargs) -> Step:
    return Step(id=step_id, action=action, params=kwargs.pop("params", {}), **kwargs)
