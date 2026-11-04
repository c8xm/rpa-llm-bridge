# -*- coding: utf-8 -*-
"""把本仓库的 MCP 服务写进各家客户端的配置（幂等，保留已有条目）。

支持：
  WorkBuddy    ~/.workbuddy/mcp.json              或 <项目>/.workbuddy/mcp.json
  Codex        ~/.codex/config.toml（只打印片段，TOML 合并交给用户/手改更安全）
  Claude       ~/.claude/mcp.json                 或 claude_desktop_config.json
  Cursor       ~/.cursor/mcp.json

用法：
    python scripts/export_mcp_config.py --workbuddy              # 写用户级
    python scripts/export_mcp_config.py --workbuddy --project .  # 写项目级
    python scripts/export_mcp_config.py --claude --cursor --print
    python scripts/export_mcp_config.py --print                  # 只看内容不写盘

环境变量（有就带上，没有就写空串，等你在客户端界面里填）：
    YINGDAO_ACCESS_KEY_ID / YINGDAO_ACCESS_KEY_SECRET / YINGDAO_API_BASE
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable


def server_defs(v01_dir: str = "") -> dict:
    """本仓库提供的 MCP 服务。"""
    servers = {
        "yingdao": {
            "command": PY,
            "args": [os.path.join(ROOT, "agentrpa_bridge", "yingdao_mcp_server.py")],
            "env": {
                "YINGDAO_API_BASE": os.environ.get("YINGDAO_API_BASE",
                                                   "https://api.yingdao.com"),
                "YINGDAO_ACCESS_KEY_ID": os.environ.get("YINGDAO_ACCESS_KEY_ID", ""),
                "YINGDAO_ACCESS_KEY_SECRET": os.environ.get("YINGDAO_ACCESS_KEY_SECRET", ""),
                "PYTHONUTF8": "1",
            },
        }
    }
    if v01_dir:
        # v0.1.0 的 AgentRPA 内核（浏览器/桌面/Office 动作）
        servers["agentrpa"] = {
            "command": PY,
            "args": [os.path.join(v01_dir, "server.py")],
            "env": {
                "PLAYWRIGHT_HEADLESS": "false",
                "ALLOW_STATE_CHANGING_ACTIONS": "false",
                "PYTHONUTF8": "1",
            },
        }
    return servers


def merge_json(path: str, servers: dict, write: bool = True) -> tuple:
    """读旧配置 → 只覆盖自己的键 → 原子写回。返回 (新增/更新列表, 备份路径)。"""
    old = {}
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8-sig") as fh:
                old = json.load(fh) or {}
        except (json.JSONDecodeError, OSError) as exc:
            raise SystemExit(f"现有配置不是合法 JSON，先手工修：{path}\n{exc}")
    existing = old.get("mcpServers") or {}
    touched = []
    for name, definition in servers.items():
        if existing.get(name) != definition:
            touched.append(name)
        existing[name] = definition
    old["mcpServers"] = existing
    backup = ""
    if write:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        if os.path.exists(path):
            backup = f"{path}.bak-{time.strftime('%Y%m%d%H%M%S')}"
            shutil.copy2(path, backup)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(old, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        os.replace(tmp, path)
    return touched, backup


def toml_snippet(servers: dict) -> str:
    lines = []
    for name, definition in servers.items():
        lines.append(f"[mcp_servers.{name}]")
        lines.append(f'command = "{definition["command"].replace(chr(92), chr(92) * 2)}"')
        args = ", ".join('"' + a.replace("\\", "\\\\") + '"' for a in definition["args"])
        lines.append(f"args = [{args}]")
        env = ", ".join(f'{k} = "{v}"' for k, v in definition["env"].items())
        if env:
            lines.append(f"env = {{ {env} }}")
        lines.append("enabled = true")
        lines.append("")
    return "\n".join(lines)


TARGETS = {
    "workbuddy": [("user", "~/.workbuddy/mcp.json"), ("project", ".workbuddy/mcp.json")],
    "claude": [("user", "~/.claude/mcp.json")],
    "cursor": [("user", "~/.cursor/mcp.json")],
}


def main(argv=None):
    ap = argparse.ArgumentParser(description="导出 MCP 客户端配置")
    ap.add_argument("--workbuddy", action="store_true", help="写入 WorkBuddy 配置")
    ap.add_argument("--claude", action="store_true", help="写入 Claude 配置")
    ap.add_argument("--cursor", action="store_true", help="写入 Cursor 配置")
    ap.add_argument("--codex", action="store_true", help="打印 Codex TOML 片段")
    ap.add_argument("--project", metavar="DIR",
                    help="给了就写项目级 .workbuddy/mcp.json（目录不存在会自动建）")
    ap.add_argument("--v01-dir", default=os.environ.get("AGENTRPA_V01_DIR", ""),
                    help="v0.1.0 agentrpa 仓库路径，给了就同时注册 agentrpa 服务")
    ap.add_argument("--print", action="store_true", help="只打印，不写盘")
    args = ap.parse_args(argv)

    servers = server_defs(args.v01_dir)
    printed = False

    for name in ("workbuddy", "claude", "cursor"):
        if not getattr(args, name):
            continue
        for level, raw in TARGETS[name]:
            if level == "project":
                if not args.project:
                    continue
                path = os.path.join(os.path.abspath(args.project), ".workbuddy", "mcp.json")
            else:
                path = os.path.expanduser(raw)
            if args.print:
                print(f"# {name} ({level}) -> {path}")
                print(json.dumps({"mcpServers": servers}, ensure_ascii=False, indent=2))
                printed = True
                continue
            touched, backup = merge_json(path, servers)
            print(f"[{name}/{level}] {path}")
            print(f"  更新: {', '.join(touched) if touched else '无变化'}"
                  + (f"  备份: {backup}" if backup else ""))

    if args.codex:
        print("# Codex: %USERPROFILE%\\.codex\\config.toml")
        print(toml_snippet(servers))
        printed = True

    if printed or not any((args.workbuddy, args.claude, args.cursor, args.codex)):
        if not printed:
            print(json.dumps({"mcpServers": servers}, ensure_ascii=False, indent=2))
        print("# 记得把 YINGDAO_ACCESS_KEY_ID/SECRET 换成控制台里的真实值")


if __name__ == "__main__":
    main()
