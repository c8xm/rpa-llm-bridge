# v2.0.0 · 企业 RPA ⇄ 智能体 双向桥

> 发布日期：2026-11-04 · 上一个版本：v0.1.0

## 一句话

v0.1.0 解决的是**智能体驱动本机**（Codex/Claude 通过 MCP 点浏览器、点桌面、写 Office）。
v2.0 补上企业里真正会买单的另一半：**RPA 反过来调用智能体**，以及**把 RPA 应用暴露成智能体的工具**。

## 新增能力

### 1. 企业网关 `agentrpa_bridge/gateway.py`

| 接口 | 说明 |
|---|---|
| `POST /v1/invoke` | 同步调用，返回 `{status, text, duration_ms, request_id}` |
| `POST /v1/jobs` + `GET /v1/jobs/{id}` | 异步任务，长流程用 |
| `GET /v1/agents` | 后端清单、工具名、会话冷热（`cold`/`alive`） |
| `GET /healthz` | 无需鉴权的存活探针 |
| `GET /metrics` | Prometheus 文本指标 |

- Bearer 鉴权：`AGENTRPA_TOKENS="tenantA:tok-a,tenantB:tok-b"`
- 每 tenant 滑动窗口限速（默认 60/min），并发信号量（默认 4）+ 排队超时 503
- 审计 jsonl：`tenant / flow / agent / prompt_sha256 / prompt_head(200 字符) / status / duration_ms`，密钥形状统一 `[REDACTED]`
- 指标：`invoke_ok_total`、`invoke_error_total`、`rate_limited_total`、`queue_timeout_total`、`agent_avg_ms{agent}`

### 2. 四种后端形态 `agentrpa_bridge/providers.py`

| kind | 形态 | 已适配 |
|---|---|---|
| `mcp` | stdio MCP，进程常驻、会话复用 | `codex mcp-server`、`claude mcp serve`、任意自研 MCP |
| `cli` | 一次性进程 | `dsh headless -`（DeepSeek Harness） |
| `openai` | OpenAI 兼容 HTTP | 豆包/火山方舟、DeepSeek、vLLM、私有网关 |

细节：工具名自动探测、`arg_template` 覆盖入参映射、进程崩溃自动重连一次、空闲 TTL 回收、Windows `.cmd` shim 自动包 `cmd /c`。

### 3. 影刀 SDK `sdk/rpa_agent_block.py`

单文件、Python 3.8+、**零第三方依赖**，可直接粘进影刀 Python 代码块：

```python
import rpa_agent_block as ab
答案 = ab.ask_agent("发票号 123，价税合计 11300，只返回税额数字", agent="codex", timeout=180)
```

`AGENTRPA_GATEWAY` 有值走网关，没有就本地拉起 agent；长任务用 `ask_agent_async(prompt, callback=...)`。
长文本走 body/stdin，不撞 Windows 8191 字符命令行上限。

### 4. 反向 MCP `agentrpa_bridge/yingdao_mcp_server.py`

把影刀开放 API 翻成工具：`rpa_list_apps` / `rpa_app_params` / `rpa_start_job` / `rpa_query_job` / `rpa_clients` / `rpa_run_local`。
Codex、Claude Code、Cursor、WorkBuddy 都能直接用自然语言驱动影刀流程。

### 5. 配置导出 `scripts/export_mcp_config.py`

```powershell
python scripts\export_mcp_config.py --workbuddy        # ~/.workbuddy/mcp.json（幂等合并 + 自动备份）
python scripts\export_mcp_config.py --claude --cursor
python scripts\export_mcp_config.py --codex            # 打印 TOML 片段
```

### 6. 文档

- `docs/USAGE.md` —— 详细使用说明（安装、配置总表、接口、逐平台接入、运维、故障排查、FAQ）
- `docs/PLATFORMS.md` —— WorkBuddy / 豆包 / dsh / Codex / Claude 能力与边界对照
- `docs/BRIDGE_DESIGN.md` —— 为什么不用官方 SDK、为什么 `http.server`、会话模型与失败模式表

## 兼容性

- **v0.1 内核不变**：`agentrpa/`、`server.py` 一行未改，27 个工具、Policy、hitl、断点续跑照旧。
- 新增包 `agentrpa_bridge`，与 `agentrpa` 同 wheel 发布；入口新增 `agentrpa-gateway`、`agentrpa-rpa-mcp`。
- Python 要求仍是 3.11+（发行版）；`sdk/rpa_agent_block.py` 单独兼容 3.8+。
- 桥本身**零第三方依赖**，可以只 clone 不 install 直接跑。

## 验证

```powershell
python -m pytest                                   # 94 passed（内核 43 + 桥 51）
python examples\local_demo.py                      # 无 key 端到端：同步 + 异步 + 指标 + 审计
python agentrpa_bridge\yingdao_mcp_server.py --selftest
```

`local_demo.py` 的实际输出：

```
网关已启动: http://127.0.0.1:51062  agents=['codex']
[同步] 200 status=succeeded 86ms
[异步] 202 run_id=ed5bf71a6e244c57 → succeeded
agentrpa_invoke_ok_total 2
[审计] artifacts\audit\agent-20261104.jsonl
    {'tenant': 'demo', 'flow': 'invoice_tax', 'status': 'succeeded',
     'duration_ms': 86, 'prompt_sha256': '96631bf89e32fb0b'}
```

## 已知限制

1. **单个 agent 的并发 = 1**（一条 stdio 管道串行）。要并行请同名多实例（`codex-1..N`）或多节点，接口不变。
2. `claude mcp serve` 暴露的是 Claude Code 的工具集合（Bash/Edit/…），**不是一次性问答**；问答型任务请用 codex / dsh / 豆包。
3. MCP 只实现 `initialize / tools/list / tools/call / ping`；`resources`、`prompts`、远程 OAuth 未做。
4. 影刀开放 API 的**路径与签名**按公开资料写默认值，`DEFAULT_PATHS` 与 `sign()` 两处可替换，需按控制台文档核对。
5. WorkBuddy 目前没有稳定的公开 Agent 调用 API，只能作为 MCP 客户端使用。
6. 流式输出未做（`dsh --json` 事件流 → SSE 在路线图里）。

## 升级指南

v0.1.0 → v2.0.0 是**纯增量**，没有破坏性改动：

```powershell
git pull
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"   # 重新装一遍以拿到新入口
python -m pytest
```

`.env` 不用改；想启用桥，把 `.env.example` 里 `v2.0 桥` 段追加进去即可。

## 下一步

- [ ] `dsh headless --json` 事件流 → 网关 SSE 进度接口
- [ ] 同名多实例调度与按工单 hash 分片
- [ ] 审计按 run 聚合查询与导出
- [ ] 录制回放：一次人工操作转成 Recipe 草稿
- [ ] 桌面控件截图回归

---

MIT © 2026 c8xm
