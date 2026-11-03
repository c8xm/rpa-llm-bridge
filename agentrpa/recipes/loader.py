"""Recipe 加载与校验：声明式 YAML，可版本化、可 diff、可复用。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from ..core.types import Step

REQUIRED_KEYS = ("name", "steps")
ALLOWED_ON_ERROR = {"abort", "skip", "retry"}


class RecipeError(ValueError):
    pass


def parse_recipe(data: dict[str, Any], source: str = "<inline>") -> dict[str, Any]:
    if not isinstance(data, dict):
        raise RecipeError(f"{source}: recipe 必须是 mapping")
    for key in REQUIRED_KEYS:
        if key not in data:
            raise RecipeError(f"{source}: 缺少字段 {key}")
    steps_raw = data["steps"]
    if not isinstance(steps_raw, list) or not steps_raw:
        raise RecipeError(f"{source}: steps 必须是非空列表")

    steps: list[Step] = []
    seen: set[str] = set()
    for index, raw in enumerate(steps_raw):
        if not isinstance(raw, dict):
            raise RecipeError(f"{source}: 第 {index} 步必须是 mapping")
        action = raw.get("action")
        if not action:
            raise RecipeError(f"{source}: 第 {index} 步缺少 action")
        step_id = str(raw.get("id") or f"step{index + 1}")
        if step_id in seen:
            raise RecipeError(f"{source}: 重复的 step id: {step_id}")
        seen.add(step_id)
        on_error = str(raw.get("on_error", "abort"))
        if on_error not in ALLOWED_ON_ERROR:
            raise RecipeError(f"{source}: on_error 只能是 {sorted(ALLOWED_ON_ERROR)}")
        params = {
            k: v
            for k, v in raw.items()
            if k not in {"id", "action", "driver", "hitl", "on_error", "retries", "description"}
        }
        steps.append(
            Step(
                id=step_id,
                action=str(action),
                driver=str(raw.get("driver", data.get("driver", "browser"))),
                params=params,
                description=str(raw.get("description", "")),
                hitl=bool(raw.get("hitl", False)),
                on_error=on_error,
                retries=int(raw.get("retries", 0)),
            )
        )

    return {
        "name": str(data["name"]),
        "description": str(data.get("description", "")),
        "driver": str(data.get("driver", "browser")),
        "vars": dict(data.get("vars") or {}),
        "steps": steps,
        "source": source,
    }


def load_recipe(path: Path | str) -> dict[str, Any]:
    p = Path(path)
    if not p.exists():
        raise RecipeError(f"recipe 不存在: {p}")
    with p.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    return parse_recipe(data, source=str(p))


def list_recipes(directory: Path | str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for path in sorted(Path(directory).glob("*.yaml")):
        try:
            recipe = load_recipe(path)
        except RecipeError as exc:
            out.append({"name": path.stem, "error": str(exc), "path": str(path)})
            continue
        out.append(
            {
                "name": recipe["name"],
                "description": recipe["description"],
                "driver": recipe["driver"],
                "steps": len(recipe["steps"]),
                "path": str(path),
            }
        )
    return out


def render(value: Any, variables: dict[str, Any]) -> Any:
    """极简模板：仅支持 {{var}}，不做表达式求值，避免注入面。"""
    if isinstance(value, str):
        out = value
        for key, val in variables.items():
            out = out.replace("{{" + key + "}}", str(val))
        return out
    if isinstance(value, dict):
        return {k: render(v, variables) for k, v in value.items()}
    if isinstance(value, list):
        return [render(v, variables) for v in value]
    return value
