"""Recipe 执行器：顺序执行、重试、人工闸门暂停、断点续跑。"""

from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..config import Settings
from ..recipes.loader import render
from .audit import AuditLog
from .policy import Policy
from .types import RunResult, Step, StepResult


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def step_to_dict(step: Step) -> dict[str, Any]:
    return {
        "id": step.id,
        "action": step.action,
        "driver": step.driver,
        "params": step.params,
        "description": step.description,
        "hitl": step.hitl,
        "on_error": step.on_error,
        "retries": step.retries,
    }


def step_from_dict(data: dict[str, Any]) -> Step:
    return Step(
        id=data["id"],
        action=data["action"],
        driver=data.get("driver", "browser"),
        params=dict(data.get("params") or {}),
        description=data.get("description", ""),
        hitl=bool(data.get("hitl", False)),
        on_error=data.get("on_error", "abort"),
        retries=int(data.get("retries", 0)),
    )


class RecipeRunner:
    def __init__(
        self,
        drivers: dict[str, Any],
        policy: Policy,
        audit: AuditLog,
        settings: Settings,
    ):
        self.drivers = drivers
        self.policy = policy
        self.audit = audit
        self.settings = settings
        self.settings.runs_dir.mkdir(parents=True, exist_ok=True)

    # ---------- 对外 ----------
    async def run(
        self,
        recipe: dict[str, Any],
        variables: dict[str, Any] | None = None,
        auto_approve: bool = False,
    ) -> RunResult:
        run_id = uuid.uuid4().hex[:12]
        merged = {**recipe.get("vars", {}), **(variables or {})}
        result = RunResult(
            run_id=run_id,
            recipe=recipe["name"],
            status="running",
            vars=merged,
            started_at=_now(),
        )
        self.audit.record(
            "run.start", run_id=run_id, recipe=recipe["name"], steps=len(recipe["steps"]),
            vars=merged,
        )
        return await self._loop(recipe["name"], recipe["steps"], result, 0, auto_approve)

    async def resume(self, run_id: str, approve: bool = True) -> RunResult:
        state = self.load_state(run_id)
        if state is None:
            raise FileNotFoundError(f"run 不存在: {run_id}")
        steps = [step_from_dict(s) for s in state["steps"]]
        result = RunResult(
            run_id=state["run_id"],
            recipe=state["recipe"],
            status="running",
            steps=[StepResult(**s) for s in state["results"]],
            vars=state.get("vars", {}),
            started_at=state.get("started_at", _now()),
        )
        start = int(state.get("index", 0))
        self.audit.record("run.resume", run_id=run_id, index=start, approve=approve)
        # 暂停点那一步尚未执行：批准后从该步开始，并带上自动批准标记
        return await self._loop(state["recipe"], steps, result, start, approve)

    # ---------- 内部 ----------
    async def _loop(
        self,
        recipe_name: str,
        steps: list[Step],
        result: RunResult,
        start_index: int,
        auto_approve: bool,
    ) -> RunResult:
        for index in range(start_index, len(steps)):
            step = steps[index]
            decision = self.policy.check_step(step)
            if not decision.allowed and not decision.requires_human:
                self.audit.record(
                    "policy.block", run_id=result.run_id, step_id=step.id, reason=decision.reason
                )
                result.steps.append(StepResult(step.id, "skipped", error=decision.reason))
                if step.on_error == "abort":
                    return self._finish(result, "failed", steps, index)
                continue

            if (decision.requires_human or step.hitl) and not auto_approve:
                self.audit.record(
                    "run.pause",
                    run_id=result.run_id,
                    step_id=step.id,
                    reason=decision.reason or "step requires human approval",
                )
                result.status = "awaiting_human"
                result.pending_step = step.id
                self._save(result, steps, index)
                return result

            outcome = await self._execute_step(step, result.vars)
            result.steps.append(outcome)
            if outcome.status == "failed" and step.on_error == "abort":
                return self._finish(result, "failed", steps, index)

        return self._finish(result, "completed", steps, len(steps))

    async def _execute_step(self, step: Step, variables: dict[str, Any]) -> StepResult:
        params = render(step.params, variables)
        driver = self.drivers.get(step.driver)
        started = time.perf_counter()

        if self.settings.dry_run:
            elapsed = int((time.perf_counter() - started) * 1000)
            self.audit.record("step.dry_run", step_id=step.id, action=step.action, params=params)
            return StepResult(step.id, "ok", output={"dry_run": True, "action": step.action}, elapsed_ms=elapsed)

        if driver is None:
            return StepResult(step.id, "failed", error=f"未注册的驱动: {step.driver}")

        attempts = 1 + max(0, step.retries)
        last_error = ""
        for attempt in range(attempts):
            try:
                output = await driver.execute(step.action, params)
                elapsed = int((time.perf_counter() - started) * 1000)
                self.audit.record(
                    "step.ok", step_id=step.id, action=step.action, driver=step.driver,
                    attempt=attempt + 1, elapsed_ms=elapsed,
                )
                return StepResult(step.id, "ok", output=output, elapsed_ms=elapsed)
            except Exception as exc:  # noqa: BLE001 - 驱动异常统一走结果通道
                last_error = f"{type(exc).__name__}: {exc}"
                self.audit.record(
                    "step.error", step_id=step.id, action=step.action, attempt=attempt + 1,
                    error=last_error,
                )
        elapsed = int((time.perf_counter() - started) * 1000)
        status = "skipped" if step.on_error == "skip" else "failed"
        return StepResult(step.id, status, error=last_error, elapsed_ms=elapsed)

    def _finish(self, result: RunResult, status: str, steps: list[Step], index: int) -> RunResult:
        result.status = status
        result.finished_at = _now()
        self.audit.record(
            "run.finish", run_id=result.run_id, status=status, steps_done=len(result.steps)
        )
        self._save(result, steps, index, done=True)
        return result

    # ---------- 状态持久化 ----------
    def _state_path(self, run_id: str) -> Path:
        return self.settings.runs_dir / f"{run_id}.json"

    def _save(
        self, result: RunResult, steps: list[Step], index: int, done: bool = False
    ) -> None:
        payload = {
            "run_id": result.run_id,
            "recipe": result.recipe,
            "status": result.status,
            "index": index,
            "done": done,
            "vars": result.vars,
            "started_at": result.started_at,
            "finished_at": result.finished_at,
            "steps": [step_to_dict(s) for s in steps],
            "results": [r.to_dict() for r in result.steps],
        }
        path = self._state_path(result.run_id)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def load_state(self, run_id: str) -> dict[str, Any] | None:
        path = self._state_path(run_id)
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def list_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        items = []
        for path in sorted(
            self.settings.runs_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True
        )[:limit]:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            items.append(
                {
                    "run_id": data.get("run_id"),
                    "recipe": data.get("recipe"),
                    "status": data.get("status"),
                    "finished_at": data.get("finished_at"),
                }
            )
        return items
