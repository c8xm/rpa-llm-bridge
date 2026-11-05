# 使用说明 · rpa-llm-bridge v2.5

> 这份文档是操作手册，不是概念介绍。每一节都能直接照抄执行。
> 概念与取舍见 [DESIGN.md](DESIGN.md)（v0.1 内核）与 [BRIDGE_DESIGN.md](BRIDGE_DESIGN.md)（v2.0 桥）；
> 各平台能力对照见 [PLATFORMS.md](PLATFORMS.md)。

---

## 目录

1. [这套东西到底解决什么](#1-这套东西到底解决什么)
2. [安装](#2-安装)
3. [5 分钟跑通（不需要任何 API key）](#3-5-分钟跑通不需要任何-api-key)
4. [配置总表](#4-配置总表)
5. [网关：接口与调用](#5-网关接口与调用)
6. [接后端：Codex / Claude / dsh / 豆包 / 自研](#6-接后端codex--claude--dsh--豆包--自研)
7. [接 RPA：影刀 / UiPath / Power Automate](#7-接-rpa影刀--uipath--power-automate)
8. [反方向：把 RPA 变成智能体的工具](#8-反方向把-rpa-变成智能体的工具)
9. [和 v0.1 内核一起用](#9-和-v01-内核一起用)
10. [运维：服务化、监控、容量、灰度](#10-运维服务化监控容量灰度)
11. [故障排查](#11-故障排查)
12. [安全边界](#12-安全边界)
13. [开发与测试](#13-开发与测试)
14. [FAQ](#14-faq)

---

## 1. 这套东西到底解决什么

两个方向，两套代码，同一个仓库：

```
方向 A：智能体 → 电脑（v0.1 内核 agentrpa）
  Codex / Claude / Cursor ──MCP──▶ 浏览器(Playwright) / Windows 桌面(pywinauto) / Office
  能力：统一 action 契约、YAML Recipe、Policy 闸门、hitl 暂停、断点续跑、审计

方向 B：RPA → 智能体（v2.0 桥 agentrpa_bridge）
  影刀 / UiPath / Power Automate ──HTTP 或 Python──▶ 网关 ──stdio/HTTP──▶ Codex / Claude / dsh / 豆包
  能力：凭证托管、进程复用、并发闸门、限速、审计、指标、异步任务

方向 C：智能体 → RPA（v2.0 桥，反方向）
  Codex / Claude / WorkBuddy ──MCP──▶ 影刀应用（startJob / queryJob）
```

企业里真实的分工：**RPA 当"手"（登录、下载、上传、点按钮），智能体当"脑"（判断该填什么、怎么写），桥负责把两边都变成对方能调用的形状，并把凭证、并发、审计、超时集中管住。**

---

## 2. 安装

### 2.1 环境要求

| 项 | 要求 | 说明 |
|---|---|---|
| Python | 3.11+（推荐 3.12） | `agentrpa` 内核需要 3.11；`sdk/rpa_agent_block.py` 单文件兼容 3.8+ |
| 操作系统 | Windows 10/11 | 桌面驱动与影刀客户端都只在这上面 |
| 网络 | 能装 pip 包；能访问模型/影刀 API | 内网离线见 2.3 |

### 2.2 标准安装

```powershell
git clone https://github.com/c8xm/rpa-llm-bridge.git
cd rpa-llm-bridge
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -U pip
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m playwright install chromium
Copy-Item .env.example .env
```

网络不好时加镜像：

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev]" `
  -i https://pypi.tuna.tsinghua.edu.cn/simple --timeout 30
```

### 2.3 只跑桥（零第三方依赖）

v2.0 的桥与 SDK **不需要任何第三方包**，只用标准库。适合：离线内网、影刀工位、只想调网关的机器。

```powershell
git clone https://github.com/c8xm/rpa-llm-bridge.git
cd rpa-llm-bridge
python -m unittest tests.test_mcp_client tests.test_providers tests.test_gateway `
                  tests.test_sdk tests.test_yingdao tests.test_platforms
python examples\local_demo.py
```

### 2.4 验证安装

```powershell
python -m pytest                          # 143 passed（未装 openpyxl 时 137 passed, 1 skipped）
python server.py --help 2>$null; echo $LASTEXITCODE   # v0.1 MCP 入口可加载
python agentrpa_bridge\yingdao_mcp_server.py --selftest
```

`--selftest` 输出：

```json
{"tools": ["rpa_list_apps", "rpa_app_params", "rpa_start_job", "rpa_query_job", "rpa_clients"],
 "openapi_configured": true, "local_available": false}
```

---

## 3. 5 分钟跑通（不需要任何 API key）

演示脚本用一个假智能体（Python 回显）替代 codex，把网关、鉴权、审计、指标全跑一遍：

```powershell
python examples\local_demo.py
```

预期输出：

```
网关已启动: http://127.0.0.1:49839  agents=['codex']
[同步] 200 status=succeeded 75ms
   text = 【假智能体】收到 16 个字符，结论：把 11300 含税金额拆出税额
[异步] 202 run_id=24f84620f25449a0
   最终状态=succeeded
agentrpa_invoke_ok_total 2
agentrpa_agent_avg_ms{agent="codex"} 70
[审计] artifacts\audit\agent-20261104.jsonl
    {'tenant': 'demo', 'flow': 'invoice_tax', 'agent': 'codex', 'status': 'succeeded',
     'duration_ms': 75, 'prompt_sha256': '96631bf89e32fb0b'}
```

看到审计里只有指纹、没有原文，就说明脱敏生效了。

---

## 4. 配置总表

### 4.1 网关

| 变量 | 默认 | 说明 |
|---|---|---|
| `AGENTRPA_HOST` | `127.0.0.1` | 生产改内网 IP，公网必须前置 TLS |
| `AGENTRPA_PORT` | `8765` | |
| `AGENTRPA_TOKENS` | 空 | `tenant:token,tenant:token`；**生产必须配** |
| `AGENTRPA_ALLOW_ANON` | `0` | 是否允许本机匿名；v2.2 起默认关闭，PoC 才设 `1` |
| `AGENTRPA_MAX_CONCURRENCY` | `4` | 同时在跑的调用数 |
| `AGENTRPA_QUEUE_TIMEOUT` | `30` | 排队超时（秒），超了回 503 |
| `AGENTRPA_RATE_LIMIT` | `60` | 每 tenant 每分钟请求上限 |
| `AGENTRPA_IDLE_TTL` | `180` | MCP 会话空闲回收（秒） |
| `AGENTRPA_AUDIT_DIR` | `artifacts/audit` | 审计目录 |
| `AGENTRPA_JOB_TTL` | `3600` | 异步任务记录保留（秒） |
| `AGENTRPA_IDEMPOTENCY_TTL` | `900` | `Idempotency-Key` 结果缓存时长（秒），0 = 关闭 |
| `AGENTRPA_STATE_BACKEND` | `memory` | 限速与幂等的后端：`memory`（单机）/ `sqlite`（跨进程） |
| `AGENTRPA_STATE_PATH` | `artifacts/bridge-state.db` | SQLite 库路径；放共享盘可跨机 |

### 4.2 后端

| 变量 | 默认 | 说明 |
|---|---|---|
| `AGENTRPA_CODEX_CMD` / `_ARGS` | `codex` / `mcp-server` | Codex 的 MCP 形态 |
| `AGENTRPA_CODEX_TOOL` | 自动 | 不给就从 `codex/ask/chat/run/task/agent` 里挑 |
| `AGENTRPA_CODEX_SANDBOX` | `workspace-write` | 传给 codex 的 sandbox 参数 |
| `AGENTRPA_CODEX_TIMEOUT` | `600` | |
| `AGENTRPA_CLAUDE_CMD` / `_ARGS` | `claude` / `mcp serve` | 注意：暴露的是工具集合 |
| `AGENTRPA_CLAUDE_TOOL` | 自动 | 用 Claude Code 时**必须显式给**，如 `Bash` |
| `AGENTRPA_DSH_CMD` / `_ARGS` | `dsh` / `headless -` | 任务走 stdin |
| `AGENTRPA_DSH_TIMEOUT` | `900` | |
| `AGENTRPA_DOUBAO_BASE` | `https://ark.cn-beijing.volces.com/api/v3` | |
| `AGENTRPA_DOUBAO_MODEL` | 空 | **接入点 id**（`ep-...`），不是模型名 |
| `AGENTRPA_DOUBAO_KEY` | 空 | 方舟 API Key |
| `AGENTRPA_OPENAI_BASE/MODEL/KEY` | `api.deepseek.com/v1` / `deepseek-chat` / 空 | 通用 OpenAI 兼容 |
| `AGENTRPA_OPENAI_TIMEOUT` | `120` | |

### 4.3 影刀（反向）

| 变量 | 说明 |
|---|---|
| `YINGDAO_API_BASE` | 默认 `https://api.yingdao.com`，以控制台为准 |
| `YINGDAO_ACCESS_KEY_ID` / `_SECRET` | 开放平台凭据，需在控制台把应用授权给它 |
| `YINGDAO_SHADOWBOT` | 本地模式：`ShadowBot.exe` 路径 |
| `YINGDAO_USER_FOLDER` | 本地模式：影刀用户目录 |
| `YINGDAO_LAUNCH_ARGS` | 本地模式启动参数模板，支持 `{app}` `{params_json}` |
| `YINGDAO_ALLOWED_APPS` | 应用白名单；生产必填，空 = 开发模式放行 |
| `YINGDAO_APPROVAL_APPS` | 需人工审批的应用，调用时必须带 `approved_by` |
| `YINGDAO_MAX_PER_APP` | 单应用并发上限，= 该应用可用机器人数 |
| `YINGDAO_IDEMPOTENCY_TTL` | 幂等窗口（秒），同参重复提交返回原 jobId |
| `YINGDAO_MAX_HOLD` | 槽位最长占用（秒），超时自动回收 |

### 4.4 RPA 工位侧

| 变量 | 说明 |
|---|---|
| `AGENTRPA_GATEWAY` | 有值就走网关，如 `http://10.0.0.8:8765` |
| `AGENTRPA_TOKEN` | 该工位的 token |

---

## 5. 网关：接口与调用

### 5.1 启动

```powershell
$env:AGENTRPA_TOKENS = "tenantA:tok-a,tenantB:tok-b"
$env:AGENTRPA_ALLOW_ANON = "0"
$env:AGENTRPA_MAX_CONCURRENCY = "4"
python -m agentrpa_bridge.gateway --host 0.0.0.0 --port 8765
```

启动日志会打印实际装配成功的 agent 列表。后端缺 key/缺 model 不会让网关起不来，只会在调用时报明确错误。

### 5.2 `POST /v1/invoke`（同步）

```powershell
curl -s -X POST http://127.0.0.1:8765/v1/invoke `
  -H "Authorization: Bearer tok-a" -H "Content-Type: application/json" `
  -d '{
        "agent": "codex",
        "prompt": "把 11300 含税金额按 13% 拆出税额，只返回数字",
        "timeout_s": 120,
        "meta": {"flow": "invoice_tax", "batch": "2026-11"}
      }'
```

```json
{"run_id": null, "status": "succeeded", "agent": "codex",
 "text": "1300.00", "duration_ms": 4821, "request_id": "a1b2c3d4e5f6"}
```

字段：

| 字段 | 必填 | 说明 |
|---|---|---|
| `agent` | 否 | 默认 `codex`；可选 `claude` / `dsh` / `doubao` / `llm` |
| `prompt` | 是 | 也接受 `text` 别名 |
| `cwd` | 否 | 智能体的工作目录，强烈建议显式给 |
| `timeout_s` | 否 | 覆盖后端默认超时 |
| `meta` | 否 | 任意键值，进审计，不回传模型 |

**幂等**：带 `Idempotency-Key: <业务单号>` 时，同 key 在 `AGENTRPA_IDEMPOTENCY_TTL` 窗口内
只会真正跑一次 agent；重复请求直接回放上次结果并带 `idempotent_replay: true`，
并发重复请求等待首个完成后同样回放（等待超时回 `409`）。RPA 超时重试请务必带这个头。

### 5.3 `POST /v1/jobs` + `GET /v1/jobs/{id}`（异步）

超过 60 秒的任务一律用异步，避免 RPA 线程被 HTTP 超时切断：

```powershell
$r = curl -s -X POST http://127.0.0.1:8765/v1/jobs `
       -H "Authorization: Bearer tok-a" -H "Content-Type: application/json" `
       -d '{"agent":"dsh","prompt":"核对这几个月的对账单","timeout_s":900}' | ConvertFrom-Json
$r.run_id

curl -s "http://127.0.0.1:8765/v1/jobs/$($r.run_id)" -H "Authorization: Bearer tok-a"
```

轮询间隔建议 ≥ 3 秒；`status` 取值 `queued` / `succeeded` / `failed`。

### 5.4 其它

```powershell
curl -s http://127.0.0.1:8765/healthz                                    # 无需鉴权
curl -s http://127.0.0.1:8765/v1/agents -H "Authorization: Bearer tok-a" # 后端清单 + 会话冷热
curl -s http://127.0.0.1:8765/metrics   -H "Authorization: Bearer tok-a" # Prometheus 文本
```

`/v1/agents` 里 `session: alive|cold` 能直接看出 MCP 进程是否常驻，排查"为什么这次慢了 3 秒"靠它。

### 5.5 状态码约定

| 码 | 含义 | RPA 侧建议动作 |
|---|---|---|
| 200 | 成功 | 取 `text` |
| 202 | 已入队 | 轮询 `/v1/jobs/{id}` |
| 400 | 缺 prompt / JSON 坏 | 修流程，不要重试 |
| 413 | 请求体超过 2MB | 改走文件 + 路径，不要重试 |
| 409 | 同一 Idempotency-Key 仍在执行 | 稍后重试，会拿到同一结果 |
| 401 | token 错 | 检查配置，不要重试 |
| 429 | 撞限速 | 退避后重试 |
| 502 | 后端失败（进程崩、模型报错） | 读 `detail`；可重试一次 |
| 503 | 并发满且排队超时 | 降并发或扩容，稍后重试 |

---

### 5.6 进度流 `GET /v1/jobs/{id}/events`（SSE）

长任务别再干等：提交异步任务后订阅事件流，能看到 `queued → running → text → tool_call → final → succeeded`。

```powershell
$r = curl -s -X POST http://127.0.0.1:8765/v1/jobs `
  -H "Authorization: Bearer tok-a" -H "Content-Type: application/json" `
  -d '{"agent":"dsh","prompt":"核对这几个月的对账单"}' | ConvertFrom-Json
curl -N "http://127.0.0.1:8765/v1/jobs/$($r.run_id)/events" -H "Authorization: Bearer tok-a"
```

```text
id: 1
event: progress
data: {"stage": "queued", "seq": 1, ...}

id: 3
event: progress
data: {"stage": "tool_call", "name": "read_file", "seq": 3, ...}
```

- 断线重连带 `Last-Event-ID`（或 `?since=N`）即可续传，不重复、不丢。
- 事件只保留最近 200 条；`text` 字段经过脱敏。
- 真事件来源：CLI 后端参数里带 `--json`（如 `dsh headless --json -`）时逐行解析；
  MCP / HTTP 后端只有 `waiting_backend` / `backend_started` 两个节点。
- 影刀侧用 SDK 的 `ask_agent_stream(prompt, on_event=...)`，回调里写变量即可。

## 6. 接后端：Codex / Claude / dsh / 豆包 / 自研

### 6.1 Codex（推荐的默认后端）

```powershell
$env:AGENTRPA_CODEX_CMD  = "codex"
$env:AGENTRPA_CODEX_ARGS = "mcp-server"
```

- 常驻进程 + 会话复用，单次 3–15 秒。
- 参数不匹配时先看 `/v1/agents` 拿真实工具名，再用 `arg_template` 覆盖：

```python
AgentSpec(name="codex", kind="mcp", command="codex", args=["mcp-server"],
          tool="codex", arg_style="generic",
          arg_template={"prompt": "{prompt}", "cwd": "{cwd}", "sandbox": "workspace-write"})
```

### 6.2 Claude Code

```powershell
$env:AGENTRPA_CLAUDE_CMD  = "claude"
$env:AGENTRPA_CLAUDE_ARGS = "mcp serve"
$env:AGENTRPA_CLAUDE_TOOL = "Bash"
```

`claude mcp serve` 暴露的是 Claude Code 自己的 46 个工具（Bash/Edit/WebFetch…），**不是一次性问答**。
要"问一句答一段"，用 Codex、dsh 或豆包；要"让模型在本机执行动作"，才用 Claude 这个工具面。

### 6.3 DeepSeek Harness（dsh headless）

```powershell
$env:AGENTRPA_DSH_CMD  = "dsh"
$env:AGENTRPA_DSH_ARGS = "headless -"
$env:AGENTRPA_DSH_TIMEOUT = "900"
```

- 语义：stdout = 最终答案，stderr = `dsh: reasoning:`，退出码 0/1。
- 凭据只认启动环境、`<cwd>/.env`、`$DSH_HOME/.env`，**不读 GUI 里存的 key**。服务账号部署时最容易在这翻车。
- 首次使用先跑 `dsh headless --help` 确认 profile 已物化。
- 一次一进程（node 冷启动 2–5 s），适合分钟级长任务；高频短任务用 Codex。
- 需要并发就同名多实例：`dsh-1` / `dsh-2`，RPA 侧按工单号取模选。

### 6.4 豆包（火山方舟）

```powershell
$env:AGENTRPA_DOUBAO_KEY   = "<ARK_API_KEY>"
$env:AGENTRPA_DOUBAO_MODEL = "ep-20261104-xxxxxx"     # 接入点 id
$env:AGENTRPA_DOUBAO_BASE  = "https://ark.cn-beijing.volces.com/api/v3"
```

调用：`{"agent":"doubao","prompt":"..."}`。缺 `model` 会直接报错（不会静默换成别的模型）。

### 6.5 任意 OpenAI 兼容服务

```powershell
$env:AGENTRPA_OPENAI_BASE  = "http://10.0.0.9:8000/v1"   # vLLM / one-api / 私有网关
$env:AGENTRPA_OPENAI_MODEL = "qwen2.5-32b-instruct"
$env:AGENTRPA_OPENAI_KEY   = "sk-internal"
```

### 6.6 自研 MCP 服务

```python
AgentSpec(name="my_agent", kind="mcp", command="node", args=["server.js"],
          tool="run_task", arg_template={"prompt": "{prompt}", "workdir": "{cwd}"},
          timeout=300)
```

只要对方实现了 MCP 的 `initialize / tools/list / tools/call`，本桥就能接。
`resources`、`prompts`、OAuth 未实现——遇到这类远程 MCP，换成官方 `mcp` SDK 实现 `BaseProvider` 即可。

### 6.7 后端怎么选

| 场景 | 选 | 理由 |
|---|---|---|
| 改脚本、跑命令、要工具 | Codex | 常驻会话，工具面完整 |
| 长任务（几分钟） | dsh | headless 一次性执行，自带推理流 |
| 中文文本生成、量大 | 豆包 | 便宜、稳定，无需工具 |
| 要模型在机器上动手 | Claude | 工具面即 Claude Code 的能力 |
| 离线/私有模型 | OpenAI 兼容 | 指向自家 vLLM |

---

## 7. 接 RPA：影刀 / UiPath / Power Automate

### 7.1 影刀 · Python 代码块（推荐）

1. 把 `sdk/rpa_agent_block.py` 复制到影刀的 Python 模块目录。
2. 代码块里：

```python
import rpa_agent_block as ab

def main(发票号, 价税合计):
    答案 = ab.ask_agent(
        "发票号 %s，价税合计 %s 元（含 13%% 增值税）。只返回税额，两位小数。"
        % (发票号, 价税合计),
        agent="codex",
        timeout=180,
        meta={"flow": "invoice_tax", "batch": 发票号},
    )
    return float(答案.strip())
```

3. 变量接在代码块的返回值上；失败用影刀的异常处理块接住，走人工复核分支。

要点：

- `GATEWAY` 环境变量存在就自动走网关，否则本地拉起 agent——同一份代码块两种环境都能跑。
- 返回值是字符串；提示词里写死输出格式（"只返回数字"），比让模型自由发挥稳一个数量级。
- **不要把整张表塞进 prompt**：落盘 + 递路径 + 递统计量。
- 长任务用 `ab.ask_agent_async(prompt, callback=...)`，回调里只写一个中间变量，主流程用"等待条件"轮询它。
- 想看进度（"第几步了"）用 `ab.ask_agent_stream(prompt, on_event=..., agent="dsh")`，
  事件形如 `{"stage": "tool_call", "name": "read_file", "seq": 3}`。

### 7.2 影刀 · HTTP 请求指令

不想装 Python 模块时，直接配「HTTP 请求」：

```
POST http://<gateway>:8765/v1/invoke
Authorization: Bearer <token>
Content-Type: application/json

{"agent":"codex","prompt":"……","timeout_s":180,"meta":{"flow":"invoice_tax"}}
```

取响应 JSON 的 `text` 字段。长任务改 `/v1/jobs` 轮询。

### 7.3 UiPath

- 「Invoke Python Method」活动：Python 版本随 Robot 走，`sdk/rpa_agent_block.py` 兼容 3.8+。
- 或「HTTP Request」活动直接打网关；Orchestrator 里把 token 放 Asset，不要硬编码。

### 7.4 Power Automate

- 云版：自定义连接器，Swagger 指向 `/v1/invoke`，鉴权选 API Key（Header `Authorization: Bearer …`）。
- 桌面版：「运行 Python 脚本」指向 `rpa_agent_block.py`。

### 7.5 自研调度

直接打 HTTP，不需要本仓库的 Python 包：

```bash
curl -s -X POST https://gw.internal/v1/invoke \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d "{\"agent\":\"llm\",\"prompt\":\"$PROMPT\",\"meta\":{\"flow\":\"ops_digest\"}}"
```

---

## 8. 反方向：把 RPA 变成智能体的工具

一条命令写配置（幂等、自动备份、保留已有条目）：

```powershell
python scripts\export_mcp_config.py --workbuddy          # ~/.workbuddy/mcp.json
python scripts\export_mcp_config.py --workbuddy --project .   # <项目>/.workbuddy/mcp.json
python scripts\export_mcp_config.py --claude --cursor
python scripts\export_mcp_config.py --codex              # 打印 TOML 片段
python scripts\export_mcp_config.py --print              # 只看不写
```

生成的 `yingdao` 服务暴露 5 个工具：

| 工具 | 作用 |
|---|---|
| `rpa_list_apps` | 列应用，拿 appId |
| `rpa_app_params` | 查应用入参定义 |
| `rpa_start_job` | 启动任务，返回 jobId |
| `rpa_query_job` | 查状态与输出 |
| `rpa_clients` | 查可调度机器人 |
| `rpa_run_local` | 本机拉起 ShadowBot（配了 `YINGDAO_SHADOWBOT` 才出现） |

配好后在 WorkBuddy / Codex / Claude 里说："把这个月的发票流程跑一遍，结果整理成表"，
模型会自己走 `rpa_list_apps → rpa_start_job → rpa_query_job`。

手动验证服务本身：

```powershell
python agentrpa_bridge\yingdao_mcp_server.py --selftest
```

### 8.1 没有影刀系统怎么验证

反方向不是"必须有影刀才能跑"——它对外的契约就是 HTTP，所以仓库里带了一个假影刀开放 API
（`examples/mock_yingdao_server.py`），路由、字段、状态流转与真实接口一致：

```powershell
python examples\reverse_demo.py
```

真实输出：

```
假影刀开放 API: http://127.0.0.1:55040
已连上 MCP 服务: {'name': 'yingdao-rpa', 'version': '0.2.0'}
工具: ['rpa_list_apps', 'rpa_app_params', 'rpa_start_job', 'rpa_query_job', 'rpa_clients']

[1] 应用列表: ['发票批量下载', '银行对账单核对']
[2] 入参定义: ['月份', '税号']
[3] 已启动任务: job_3b69791ade
    状态 queued   进度 0%
    状态 running  进度 45%
    状态 success  进度 100%
[4] 最终结果: {"rows": 128, "file": "D:\\rpa\\out\\app_invoice_job_3b69791ade.xlsx", ...}
[5] 机器人: [('财务机器人-01', '在线'), ('财务机器人-02', '在线')]
```

这条链路跑通说明：MCP 服务、工具 schema、JSON 解析、"启动-轮询-取结果"的状态机、错误分支
全都是对的。等拿到影刀控制台时，只需把 `YINGDAO_API_BASE` 指过去、填 AccessKey，
再按 [PLATFORMS.md](PLATFORMS.md) 核一遍路径与签名即可。

同理，把 `examples/mock_yingdao_server.py` 换成任意 HTTP 服务（UiPath Orchestrator、
自研调度、企业内部 API），只需改 `yingdao_api.py` 的 `DEFAULT_PATHS`，本仓库的其他部分不动。

### 8.2 企业护栏（生产必开）

反向 MCP 进程内置四条规则，配置在**进程环境变量**上：

| 变量 | 行为 | 生产建议 |
|---|---|---|
| `YINGDAO_ALLOWED_APPS` | 白名单外的应用直接拒绝，不产生任务 | 只放已评审的应用 |
| `YINGDAO_APPROVAL_APPS` | 清单内应用必须带 `approved_by`，否则返回 `requires_approval` | 资金/对外发送类流程 |
| `YINGDAO_MAX_PER_APP` | 单应用并发上限，满了返回 `busy`（不排队） | = 该应用可用机器人数 |
| `YINGDAO_IDEMPOTENCY_TTL` | 同参数窗口内重复提交返回同一 jobId（`deduplicated: true`） | 900 |
| `YINGDAO_MAX_HOLD` | 没人轮询终态时槽位最长占用，超时自动回收 | 3600 |

```powershell
$env:YINGDAO_ALLOWED_APPS    = "app_invoice,app_reconcile"
$env:YINGDAO_APPROVAL_APPS   = "app_reconcile"
$env:YINGDAO_MAX_PER_APP     = "2"
$env:YINGDAO_IDEMPOTENCY_TTL = "900"
python agentrpa_bridge\yingdao_mcp_server.py --selftest
# policy: {"whitelist_enforced": true, "approval_apps": ["app_reconcile"], "max_per_app": 2, ...}
```

智能体侧看到的拒绝是结构化 JSON（`isError: true`），可直接分支处理：

```json
{"error": "requires_approval", "message": "应用 app_reconcile 属于审批清单，必须提供 approved_by…"}
{"error": "busy", "app_id": "app_invoice", "limit": 2}
{"error": "app_not_allowed", "allowed": ["app_invoice", "app_reconcile"]}
```

新增工具 `rpa_policy` 可随时查看当前护栏与在跑任务数，便于智能体自己判断"能不能再开一个"。
完整的企业接入路径（角色分工、四阶段、评审问答、验收清单）见
[ENTERPRISE-ADOPTION.md](ENTERPRISE-ADOPTION.md)。

---

## 9. 和 v0.1 内核一起用

两者装在一个仓库里，可同时启动：

```powershell
# 方向 A：智能体操作本机 GUI（v0.1）
.\.venv\Scripts\python.exe server.py

# 方向 B/C：RPA 桥（v2.0，零依赖）
python -m agentrpa_bridge.gateway --port 8765
```

混合编排的典型写法：智能体出判断，影刀执行不可逆动作。

```python
# 影刀代码块
判断 = ab.ask_agent("这张单据的税率按 13% 还是 9%？只返回数字。", agent="llm")
税额 = 价税合计 / (1 + float(判断) / 100) * float(判断) / 100
# 剩下的填单、提交仍走影刀既有节点（人审在影刀里，桥不碰）
```

**桥不提供"agent 直接点确认"的通道**：不可逆动作留在 RPA 的审批节点，这是刻意的边界。

---

## 10. 运维：服务化、监控、容量、灰度

### 10.1 Windows 服务

```powershell
nssm install AgentRPABridge "C:\Python312\python.exe" "-m agentrpa_bridge.gateway --port 8765"
nssm set AgentRPABridge AppDirectory "D:\rpa-llm-bridge"
nssm set AgentRPABridge AppEnvironmentExtra "AGENTRPA_TOKENS=tenantA:tok-a" "AGENTRPA_ALLOW_ANON=0"
nssm set AgentRPABridge AppStdout "D:\rpa-llm-bridge\artifacts\gateway.log"
nssm start AgentRPABridge
```

或任务计划：`schtasks /create /tn AgentRPABridge /sc onstart /ru SYSTEM /tr "...python.exe -m agentrpa_bridge.gateway"`。
服务账号别用 SYSTEM 跑 agent，给专用低权账号 + 独立工作目录。

### 10.2 监控

```yaml
# Prometheus 抓取
- job_name: agentrpa-bridge
  metrics_path: /metrics
  static_configs: [{ targets: ["10.0.0.8:8765"] }]
```

关键指标：

| 指标 | 含义 | 告警建议 |
|---|---|---|
| `agentrpa_invoke_ok_total` | 成功调用数 | 环比骤降 |
| `agentrpa_invoke_error_total` | 失败数 | >5% 持续 10 分钟 |
| `agentrpa_rate_limited_total` | 被限速 | 持续增长说明额度或限速配置不对 |
| `agentrpa_queue_timeout_total` | 并发打满 | 出现即扩容 |
| `agentrpa_inflight_requests` | 当前并发 | 长期贴近上限需扩容 |
| `agentrpa_agent_avg_ms{agent}` | 平均耗时 | 突增看后端 |

### 10.3 容量估算

单次问答 3–15 秒（会话复用），冷启动 +2–5 秒。1000 单/天、每单 1–2 次调用，单节点 2 并发够用。
MCP 会话是串行的（一条 stdio 管道），**要真并行就同名多实例或多节点**。

### 10.4 多节点：共享限速与幂等

默认 `memory` 后端是进程内状态：起两个网关，限速就会变成两倍，幂等也会各算各的。
同机多进程（或网关共用一块盘）时切 SQLite：

```powershell
$env:AGENTRPA_STATE_BACKEND = "sqlite"
$env:AGENTRPA_STATE_PATH    = "D:\bridge\state.db"   # 两个节点指向同一个文件
```

| 后端 | 适用 | 说明 |
|---|---|---|
| `memory` | 单进程 / PoC | 零依赖，重启即清空 |
| `sqlite` | 同机多进程、共享盘 | WAL + `BEGIN IMMEDIATE`，跨进程计数与幂等一致 |
| Redis | 多机（未内置） | 实现 `store.py` 里同样的 4 个方法即可替换 |

跨机用共享盘放 SQLite 属「能用但别硬扛」：网络文件系统的锁语义有差异，量大时换 Redis。

### 10.5 灰度与回滚

1. 先接一个非关键流程，`meta.flow` 打标。
2. 看 `/metrics` 的失败率与 p95。
3. 稳定后收紧 `timeout_s`。
4. 回滚：把该流程的 `agent` 改成 `llm`（纯模型兜底），RPA 侧零改动。

---

## 11. 故障排查

| 症状 | 原因 | 处理 |
|---|---|---|
| `MCP 进程退出：… ENOENT` | CLI 不在 PATH | 环境变量填绝对路径；`.cmd` shim 已由 `argv_for` 包 `cmd /c` |
| `响应超时 180s` | 任务真长 / 进程卡死 | 看 `/v1/agents` 的 session；长任务改 `/v1/jobs` |
| `spawn claude ENOENT` | Claude Code 不在 PATH | `where claude` 后填绝对路径 |
| 401 | token 不在 `AGENTRPA_TOKENS`（或没配 tokens 却带了 token） | 检查 `tenant:token` 拼写 |
| 429 | 撞限速 | 退避重试或调 `AGENTRPA_RATE_LIMIT` |
| 503 busy | 并发满 | 调 `AGENTRPA_MAX_CONCURRENCY` / `QUEUE_TIMEOUT` |
| 502 且 detail 提到 model | 豆包 model 填了模型名 | 改接入点 `ep-...` |
| 502 且 detail 提到 key | dsh/豆包/OpenAI 缺凭据 | 配 env 或 `.env`（dsh 只读 `.env`） |
| 中文乱码 | 控制台编码 | `PYTHONUTF8=1`、`PYTHONIOENCODING=utf-8` |
| 影刀业务 `code != 0` | 无权限 / 参数名不符 | 先 `rpa_app_params` 拉参数定义 |
| 审计里没有记录 | 请求被 401 挡在鉴权层 | 鉴权失败也计 `auth_failures_total`，但不写 invoke 记录 |
| `mcpServers` 配置不生效 | JSON 语法坏 / 路径带引号 | `export_mcp_config.py --print` 对比；确认没有 BOM |
| `claude mcp serve` 没反应 | 它是 stdio 服务，静默=正常 | 不要当交互式命令跑 |

快速定位顺序：`/healthz` → `/v1/agents` → 单次 `/v1/invoke` → 审计 `/v1/agents` 的 session → 后端 CLI 手工执行一次。

---

## 12. 安全边界

- 凭证分层：工位只有网关 token；模型 key 只存在网关机；影刀 AccessKey 只存在反向 MCP 进程。
- 审计：`prompt` 只存前 200 字符 + sha256；`sk-*` / `ghp_*` / `AKIA*` / `Bearer *` 一律 `[REDACTED]`；
  手机号、身份证、银行卡、邮箱分别打码为 `[PHONE]` / `[ID_CARD]` / `[BANK_CARD]` / `[EMAIL]`。
- 限速与并发：默认 60/min/tenant、4 并发，防止单个流程打穿额度。
- 审计日志按天落盘，交给现有采集器推 SIEM。
- 不做的事：不把 agent 输出直接拼 SQL / 命令行 / 文件路径；不可逆动作留在 RPA 审批节点。
- 公网部署：必须前置 TLS 与 IP 白名单，`/metrics` 只对内网开。

---

## 13. 开发与测试

```powershell
python -m pytest                                   # 全部 143 个用例
python -m pytest tests/test_gateway.py -v          # 单文件
python -m unittest tests.test_gateway              # 桥的部分不需要 pytest
ruff check agentrpa agentrpa_bridge server.py tests --select E9,F63,F7,F82
python -m build                                    # 出 wheel
```

目录：

```
server.py                  v0.1 MCP 工具层（27 个工具）
agentrpa/                  v0.1 内核：core / drivers / recipes
agentrpa_bridge/           v2.0 桥：mcp_client / providers / gateway / yingdao_*
sdk/rpa_agent_block.py     影刀代码块单文件 SDK
scripts/                   export_mcp_config.py / install_windows.ps1 / make_assets.py
tests/                     143 个用例（内核 43 + 桥 51 + 反方向 5 + 护栏 11 + 加固 10 + 幂等/转义 11 + 共享状态 7 + 进度流 5）
examples/local_demo.py     无 key 端到端演示
```

新增一个后端 = 实现 `BaseProvider.invoke()` 并在 `default_specs()` 里登记，网关与 RPA 侧零改动。
新增一个 RPA 平台 = 仿 `yingdao_mcp_server.py` 写一个 MCP 服务，或直接让该平台打 `/v1/invoke`。

---

## 14. FAQ

**Q：能不能只用桥、不要 v0.1 的浏览器/桌面驱动？**
能。桥零依赖，`pip install` 都不用，`python -m agentrpa_bridge.gateway` 即可。

**Q：一个流程能不能中途换后端？**
能，每次调用带 `agent` 字段。典型：先 `doubao` 写文案，再 `codex` 改脚本。

**Q：MCP 会话会不会串味？**
同一 agent 的调用在进程内串行，`meta` 不进模型上下文；不同 `cwd` 建议起不同实例。

**Q：影刀流程超时怎么设？**
`timeout_s` 要比影刀节点超时小 30 秒，让桥先失败并返回可读错误。

**Q：审计能不能关？**
把 `AGENTRPA_AUDIT_DIR` 指到临时目录即可，但企业场景建议开着。

**Q：dsh 一次一进程会不会太慢？**
冷启动 2–5 秒，分钟级任务可忽略；高频短任务用 Codex 常驻会话。

**Q：能不能接非 MCP 的自研 agent？**
两种方式：给它套一个 MCP stdio 服务（最省事），或实现 `BaseProvider` 子类。

---

MIT © c8xm · 问题与需求走 [Issues](https://github.com/c8xm/rpa-llm-bridge/issues)
