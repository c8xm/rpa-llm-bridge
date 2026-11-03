# 安全策略

## 报告问题

发现安全问题时请走 GitHub Security Advisory（仓库 Security 页 → Report a vulnerability），不要直接开公开 issue。请在报告中包含：复现步骤、影响面、期望行为、实际行为。

## 设计边界

AgentRPA 是一个**本地**自动化工具，默认配置下只读、低频、需要人工参与。

内置防线：

| 防线 | 实现位置 | 默认行为 |
|---|---|---|
| 域名白名单 | `agentrpa/core/policy.py::check_url` | 仅 `ALLOWED_DOMAINS` 内可访问 |
| 应用白名单 | `check_app` | 空列表时拒绝启动任何桌面程序 |
| 敏感输入拦截 | `check_input` | 密码/验证码/卡号等字段拒绝输入 |
| 状态变更闸门 | `check_click` | 交易、发布、删除等动作拦截并等待人工确认 |
| 审计脱敏 | `agentrpa/core/audit.py` | 敏感 key 与敏感 label 联动脱敏 |

## 使用者责任

- 只在你拥有或获授权操作的系统、账号与数据上运行。
- 遵守目标平台的服务条款与 robots 规则。
- 不要把 `BROWSER_PROFILE_DIR` 指向日常浏览器的用户目录。
- `ALLOW_STATE_CHANGING_ACTIONS=true` 会让工具具备下单/支付/发布能力，仅在隔离环境开启。
- 审计日志可能包含页面文本与业务数据，提交 issue 前先检查是否需要脱敏。

## 支持的版本

安全修复只针对 `main` 分支的最新版本。
