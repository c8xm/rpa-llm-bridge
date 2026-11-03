"""AgentRPA MCP Server 入口：工具层只做参数收敛与路由，业务在 core / drivers 里。"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

_browsers = os.getenv("PLAYWRIGHT_BROWSERS_PATH", "").strip()
if _browsers:
    _p = Path(_browsers)
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(_p if _p.is_absolute() else ROOT / _p)

from mcp.server.fastmcp import FastMCP  # noqa: E402

from agentrpa.config import Settings  # noqa: E402
from agentrpa.core.audit import AuditLog  # noqa: E402
from agentrpa.core.policy import Policy  # noqa: E402
from agentrpa.core.runner import RecipeRunner  # noqa: E402
from agentrpa.drivers.desktop import DesktopDriver  # noqa: E402
from agentrpa.drivers.office import OfficeDriver  # noqa: E402
from agentrpa.drivers.web import WebDriver  # noqa: E402
from agentrpa.recipes.loader import list_recipes, load_recipe  # noqa: E402

settings = Settings.from_env(ROOT)
settings.ensure_dirs()

policy = Policy(
    allowed_domains=settings.allowed_domains,
    allowed_apps=settings.allowed_apps,
    allow_state_changing=settings.allow_state_changing_actions,
    require_hitl_for_state_changes=settings.require_hitl_for_state_changes,
    extra_blocked_terms=settings.extra_blocked_terms,
)
audit = AuditLog(settings.audit_dir)

drivers: dict[str, Any] = {
    "browser": WebDriver(settings, policy, audit),
    "desktop": DesktopDriver(settings, policy, audit),
    "office": OfficeDriver(settings, policy, audit),
}
runner = RecipeRunner(drivers, policy, audit, settings)

mcp = FastMCP(
    "AgentRPA",
    instructions=(
        "本地 RPA Agent。统一 action 层覆盖浏览器（Playwright）、Windows 桌面（pywinauto）与 Office 文件。"
        "优先用 recipe_run 跑声明式流程；只有页面结构未知时才手动 open_url / list_page_elements / click_page_element。"
        "所有状态变更动作（购买、支付、提交订单、发布、发送、删除）默认拦截，必须由用户在返回值里确认后再用 run_resume 继续。"
        "密码、验证码、OTP、银行卡、身份证等字段禁止自动输入，工具会直接拒绝。"
        "元素 ref 属于短生命周期标识：点击或导航后必须重新调用 list_page_elements。"
        "不要高频、大规模抓取；单次任务保持低频与可解释的步数。"
    ),
)


# ---------------- 信息 ----------------
@mcp.tool()
async def server_info() -> dict[str, Any]:
    """返回版本、驱动能力与当前策略摘要。"""
    return {
        "name": "AgentRPA",
        "version": "0.1.0",
        "dry_run": settings.dry_run,
        "drivers": {name: drivers[name].capabilities() for name in drivers},
        "policy": policy.summary(),
        "paths": {
            "artifacts": str(settings.artifacts_dir),
            "runs": str(settings.runs_dir),
            "audit": str(settings.audit_dir),
            "recipes": str(settings.recipes_dir),
        },
    }


@mcp.tool()
async def policy_summary() -> dict[str, Any]:
    """查看白名单、敏感字段与状态变更规则。"""
    return policy.summary()


@mcp.tool()
async def driver_status() -> dict[str, Any]:
    """逐个驱动健康检查。"""
    return {name: await drivers[name].health() for name in drivers}


# ---------------- 浏览器 ----------------
@mcp.tool()
async def browser_start() -> dict[str, Any]:
    """启动可见 Chromium，使用独立持久化资料目录复用登录状态。"""
    return await drivers["browser"].start()


@mcp.tool()
async def browser_status() -> dict[str, Any]:
    """查看浏览器是否运行、当前 URL 与资料目录。"""
    return await drivers["browser"].health()


@mcp.tool()
async def browser_close() -> dict[str, Any]:
    """关闭浏览器，保留本地登录资料。"""
    return await drivers["browser"].close()


@mcp.tool()
async def open_url(url: str) -> dict[str, Any]:
    """打开白名单域名下的 URL，其他域名直接拒绝。"""
    return await drivers["browser"].execute("navigate", {"url": url})


@mcp.tool()
async def page_snapshot(max_chars: int = 6000) -> dict[str, Any]:
    """获取当前页面标题、正文摘要、链接、表单和 meta。"""
    return await drivers["browser"].execute("snapshot", {"max_chars": max_chars})


@mcp.tool()
async def list_page_elements(limit: int = 80) -> dict[str, Any]:
    """列出可交互元素并分配短生命周期 ref。"""
    return await drivers["browser"].execute("elements", {"limit": limit})


@mcp.tool()
async def click_page_element(ref: str, label: str = "") -> dict[str, Any]:
    """点击 ref 对应元素；命中交易/提交类文案时会被拦截并要求人工确认。"""
    return await drivers["browser"].execute("click", {"ref": ref, "label": label})


@mcp.tool()
async def type_into_element(ref: str, text: str, label: str = "", press_enter: bool = False) -> dict[str, Any]:
    """向普通输入框写入文本；密码、验证码、卡片等字段会被拒绝。"""
    return await drivers["browser"].execute(
        "fill", {"ref": ref, "text": text, "label": label, "press_enter": press_enter}
    )


@mcp.tool()
async def scroll_page(direction: str = "down", amount: int = 900) -> dict[str, Any]:
    """上下滚动页面。"""
    return await drivers["browser"].execute("scroll", {"direction": direction, "amount": amount})


@mcp.tool()
async def take_screenshot(full_page: bool = False) -> dict[str, Any]:
    """截图并保存到 artifacts 目录，返回本地路径。"""
    return await drivers["browser"].execute("screenshot", {"full_page": full_page})


@mcp.tool()
async def extract_page(selectors: dict[str, str] | None = None, multi: dict[str, str] | None = None,
                       limit: int = 20) -> dict[str, Any]:
    """按 CSS 选择器批量抽取字段：selectors 取单值，multi 取列表。"""
    return await drivers["browser"].execute(
        "extract", {"selectors": selectors or {}, "multi": multi or {}, "limit": limit}
    )


# ---------------- Recipe ----------------
@mcp.tool()
async def recipe_list() -> dict[str, Any]:
    """列出本地可用流程模板。"""
    return {"dir": str(settings.recipes_dir), "recipes": list_recipes(settings.recipes_dir)}


@mcp.tool()
async def recipe_get(name: str) -> dict[str, Any]:
    """按名称读取流程定义，便于修改参数后重跑。"""
    path = Path(settings.recipes_dir) / f"{name}.yaml"
    recipe = load_recipe(path)
    return {
        "name": recipe["name"],
        "description": recipe["description"],
        "driver": recipe["driver"],
        "vars": recipe["vars"],
        "steps": [
            {"id": s.id, "action": s.action, "driver": s.driver, "hitl": s.hitl, "params": s.params}
            for s in recipe["steps"]
        ],
    }


@mcp.tool()
async def recipe_run(name: str, variables: dict[str, Any] | None = None,
                     auto_approve: bool = False) -> dict[str, Any]:
    """执行流程模板；命中人工闸门时返回 awaiting_human 与 pending_step。"""
    recipe = load_recipe(Path(settings.recipes_dir) / f"{name}.yaml")
    result = await runner.run(recipe, variables=variables, auto_approve=auto_approve)
    return result.to_dict()


@mcp.tool()
async def run_state(run_id: str) -> dict[str, Any]:
    """读取某次运行的完整状态，用于排查或续跑。"""
    state = runner.load_state(run_id)
    return state or {"error": f"run 不存在: {run_id}"}


@mcp.tool()
async def run_resume(run_id: str, approve: bool = True) -> dict[str, Any]:
    """人工确认后从暂停点继续执行。"""
    return (await runner.resume(run_id, approve=approve)).to_dict()


@mcp.tool()
async def run_list(limit: int = 20) -> dict[str, Any]:
    """最近运行记录。"""
    return {"runs": runner.list_runs(limit)}


# ---------------- 审计 ----------------
@mcp.tool()
async def audit_tail(limit: int = 50) -> dict[str, Any]:
    """读取最近审计日志（敏感字段已脱敏）。"""
    return {"entries": audit.tail(limit)}


# ---------------- 桌面 / Office ----------------
@mcp.tool()
async def desktop_windows() -> dict[str, Any]:
    """列出当前桌面上可见的窗口标题。"""
    return await drivers["desktop"].execute("windows", {})


@mcp.tool()
async def desktop_focus(title: str) -> dict[str, Any]:
    """按窗口标题正则聚焦某个应用窗口。"""
    return await drivers["desktop"].execute("focus", {"title": title})


@mcp.tool()
async def desktop_elements(limit: int = 60) -> dict[str, Any]:
    """列出已聚焦窗口中可交互的控件。"""
    return await drivers["desktop"].execute("elements", {"limit": limit})


@mcp.tool()
async def office_sheets(path: str) -> dict[str, Any]:
    """列出 Excel 工作簿中的工作表。"""
    return await drivers["office"].execute("sheets", {"path": path})


@mcp.tool()
async def office_read(path: str, sheet: str | None = None, max_rows: int = 100) -> dict[str, Any]:
    """读取 Excel 行数据或 Word 段落。"""
    return await drivers["office"].execute("read", {"path": path, "sheet": sheet, "max_rows": max_rows})


@mcp.tool()
async def office_write(path: str, cells: dict[str, Any], sheet: str | None = None) -> dict[str, Any]:
    """按单元格坐标写入 Excel，例如 {"B2": 42}。"""
    return await drivers["office"].execute("write", {"path": path, "cells": cells, "sheet": sheet})


def main() -> None:
    if "--http" in sys.argv:
        mcp.run(transport="streamable-http")
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
