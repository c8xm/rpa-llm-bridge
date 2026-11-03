"""贯穿全链路的运行时类型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Step:
    """Recipe 中的一步。执行单位，不关心底层是浏览器还是桌面应用。"""

    id: str
    action: str
    driver: str = "browser"
    params: dict[str, Any] = field(default_factory=dict)
    description: str = ""
    hitl: bool = False
    on_error: str = "abort"  # abort | skip | retry
    retries: int = 0


@dataclass
class StepResult:
    step_id: str
    status: str  # ok | failed | skipped | awaiting_human
    output: Any = None
    error: str | None = None
    elapsed_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "status": self.status,
            "output": self.output,
            "error": self.error,
            "elapsed_ms": self.elapsed_ms,
        }


@dataclass
class RunResult:
    run_id: str
    recipe: str
    status: str  # completed | failed | awaiting_human | running
    steps: list[StepResult] = field(default_factory=list)
    pending_step: str | None = None
    vars: dict[str, Any] = field(default_factory=dict)
    started_at: str = ""
    finished_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "recipe": self.recipe,
            "status": self.status,
            "steps": [s.to_dict() for s in self.steps],
            "pending_step": self.pending_step,
            "vars": self.vars,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


@dataclass
class Decision:
    """策略闸门的判定结果。"""

    allowed: bool
    reason: str = ""
    requires_human: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"allowed": self.allowed, "reason": self.reason, "requires_human": self.requires_human}
