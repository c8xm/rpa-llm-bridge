# 设计取舍与失败模式

## 为什么不用官方 `mcp` Python SDK

官方 SDK 很好用，但在这三个约束下不划算：

1. **影刀的解释器**：版本老、离线、装包要 IT 审批。stdlib 版本可以直接粘进代码块。
2. **网关要常驻**：多一个依赖就多一类启动失败；`mcp` 的依赖树不小。
3. **协议本身很小**：stdio 传输就是行分隔 JSON-RPC + 一次 initialize 握手。

代价：只实现了 `tools/*` 与 `ping`，`resources`、`prompts`、`sampling`、OAuth 都没做。
如果哪天要接带 OAuth 的远程 MCP，直接上官方 SDK，把 `McpAgentProvider` 换掉即可——
provider 契约只有 `invoke(prompt) -> dict` 一条。

## 为什么 gateway 用 `http.server` 而不是 FastAPI

- 部署目标是 Windows 服务器 + 任务计划/NSSM，`pip install fastapi uvicorn` 在离线内网是长期痛点。
- 这个网关只做 5 个接口，`ThreadingHTTPServer` 足够；真要抗高并发，前面必然有 Nginx。
- 想要 ASGI 也很容易：`create_server()` 里的路由逻辑与 HTTP 层是分离的。

## 会话模型：一个 agent 一条常驻连接

- MCP 的 stdio 会话是有状态的（工具列表、服务端上下文）。每请求一个新进程 = 每次重付
  初始化和工具 schema，且丢掉了 codex 的线程上下文。
- 所以 `McpAgentProvider` 每个 provider 实例持有一个 `McpStdioClient`，用 `RLock` 串行化。
  并发提升靠 `max_concurrency`（多进程/多节点），不是靠同一条管道多路复用。
- 空闲 `idle_ttl` 后由后台线程回收；进程崩溃时 `invoke` 内部重连一次，第二次才上抛。

这带来一个已知限制：**同一个 agent 的并发度 = 1（每 provider 实例）**。
要真正并行就跑多个网关实例，或在 `AgentPool` 里把同一 spec 注册成 `codex-1..codex-4`
（接口不变，RPA 侧按 hash 选名字）。

## 失败模式表

| 失败 | 表现 | 已处理 |
|---|---|---|
| agent CLI 不在 PATH | `MCP 进程退出：...` + stderr 尾部 | 错误信息带 stderr 最后 5 行，不吞掉 |
| 服务端往 stdout 打 banner | 解析失败 | 非 JSON 行收进 `banners`，不中断 |
| 服务端请求 `roots/list` | 客户端不回 → 卡死 | 就地应答 `{"roots": []}` |
| 单次调用超时 | 一直等 | 读取有 deadline，抛 `McpTimeout` |
| 进程中途被杀 | 读到 EOF | 抛 `McpError`，provider 重连一次 |
| Chinese/emoji | 乱码 | 全链路 UTF-8，`errors="replace"` 兜底 |
| 参数里带 `{}` | `str.format` KeyError | 只替换已知占位符（`_subst`） |
| 并发打满 | 请求堆积 | 信号量 + 排队超时 → 503，不无限增长 |
| 额度被打穿 | 账单异常 | 每 tenant 滑动窗口限速 → 429 |
| 审计泄漏密钥 | 密钥进日志 | 正则脱敏 + prompt 只留指纹与前 200 字符 |

## 数据流（一次调用）

```
影刀代码块
  └─ ab.ask_agent(prompt)
       └─ POST /v1/invoke  {agent, prompt, timeout_s, meta}
            ├─ 鉴权：Bearer → tenant
            ├─ 限速：tenant 滑动窗口
            ├─ 信号量：并发闸门（排队超时 503）
            ├─ AgentPool.invoke → McpAgentProvider
            │     ├─ 复用进程 / 冷启动 + initialize
            │     ├─ tools/call {"prompt": ..., "cwd": ..., "sandbox": ...}
            │     └─ 拆外层 JSON（codex 会把答案再包一层）
            ├─ 审计：指纹 + 耗时 + 状态
            └─ 返回 {status, text, duration_ms, request_id}
       └─ 返回值 → 影刀变量
```

## 与 v0.1.0 Policy 的关系

v0.1.0 的 `Policy` 管的是"agent 能对世界做什么"（域名白名单、敏感字段、状态变更闸门）。
0.2 的方向反过来，管的是"RPA 能对 agent 做什么"，所以规则集不同：

- 输入侧：`prompt` 长度上限、敏感字段脱敏、租户配额。
- 输出侧：不让 agent 的输出直接变成命令/路径 —— 这一步交回给 RPA 流程显式校验
  （只接受数字、枚举、JSON Schema）。
- 不可逆动作：仍然留在 RPA 的审批节点，桥不提供"agent 直接点确认"的通道。
