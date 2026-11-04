"""agentrpa_bridge —— 把企业 RPA 接到智能体 MCP 服务的双向桥。

对 RPA 侧：纯标准库实现，无第三方依赖，可直接粘贴进影刀 Python 代码块、
UiPath Invoke Python Method、Power Automate 的自定义连接器后台。

对智能体侧：把 RPA 应用注册成 MCP 工具，Codex / Claude Code / Cursor 可直接触发流程。
"""

__version__ = "0.2.0"

from .mcp_client import McpError, McpStdioClient, McpTimeout  # noqa: F401

__all__ = ["__version__", "McpStdioClient", "McpError", "McpTimeout"]
