# 接哪些平台、怎么接

一张表先分清方向，方向和产品能力不能搞混：

| 平台 | 它是什么 | 让 RPA 调它（当"脑"） | 让它驱动 RPA（当"手"） |
|---|---|---|---|
| 影刀 ShadowBot | RPA 平台 | — | ✅ `yingdao_mcp_server.py` |
| Codex CLI | 智能体 | ✅ `kind="mcp"`，`codex mcp-server` | ✅ 注册 `yingdao` MCP |
| Claude Code | 智能体 | ✅ `kind="mcp"`，`claude mcp serve`（工具面，非问答） | ✅ 同上 |
| DeepSeek Harness (dsh) | 智能体运行时 | ✅ `kind="cli"`，`dsh headless -` | ✅ 同上（它本身是 MCP 客户端） |
| 豆包 / 火山方舟 | 模型 API | ✅ `kind="openai"`，Ark 兼容层 | ❌ 它没有工具面，只能当模型 |
| WorkBuddy | MCP 客户端 + 办公 Agent | ⚠️ 无公开 Agent 调用 API，见下 | ✅ 注册 `yingdao` MCP |

## 1. WorkBuddy

WorkBuddy 是**客户端**：它读 `~/.workbuddy/mcp.json`（用户级）或 `<项目>/.workbuddy/mcp.json`（项目级），
格式与 Claude Desktop 一致（`{"mcpServers": {...}}`）。

所以整件事是**把影刀注册进去**：

```powershell
python scripts\export_mcp_config.py --workbuddy
# [workbuddy/user] C:\Users\<you>\.workbuddy\mcp.json
#   更新: yingdao  备份: ...mcp.json.bak-20261104...
```

写进去的内容：

```json
{
  "mcpServers": {
    "yingdao": {
      "command": "C:\\Users\\<you>\\AppData\\Local\\Programs\\Python\\Python314\\python.exe",
      "args": ["D:\\rpa-bridge-0.2\\agentrpa_bridge\\yingdao_mcp_server.py"],
      "env": {
        "YINGDAO_API_BASE": "https://api.yingdao.com",
        "YINGDAO_ACCESS_KEY_ID": "ak_xxx",
        "YINGDAO_ACCESS_KEY_SECRET": "sk_xxx",
        "PYTHONUTF8": "1"
      }
    }
  }
}
```

保存后 WorkBuddy 的插件 → MCP 服务器里应显示 🟢。之后在对话里"帮我把这个月的发票流程跑一遍，
结果整理成表"就会走 `rpa_list_apps → rpa_start_job → rpa_query_job`。

`export_mcp_config.py` 是**合并写**：已有的 `wecom` 之类条目原样保留，只覆盖 `yingdao` 自己的键，
写之前自动备份，重复执行不会产生 diff。

> 反方向（RPA 流程里调 WorkBuddy 的 Agent）目前没有稳定的公开 API：WorkBuddy 的模型与 Agent 跑在腾讯侧，
> 企业集成走的是它的连接器/企业版，而不是"给我一个 endpoint"。要 RPA 当编排方，后端请选 DSH / Codex / 豆包。
> 真要用 WorkBuddy 的企业接口，把它的 base_url 与鉴权头塞进 `AgentSpec(kind="openai", headers={...})` 即可。

## 2. 豆包（火山方舟 Ark）

方舟提供 OpenAI 兼容层，所以不需要新代码，只要环境变量：

```powershell
$env:AGENTRPA_DOUBAO_KEY   = "<ARK_API_KEY>"
$env:AGENTRPA_DOUBAO_MODEL = "ep-20261104-xxxxxx"        # 接入点 id，不是模型名
$env:AGENTRPA_DOUBAO_BASE  = "https://ark.cn-beijing.volces.com/api/v3"
```

RPA 侧：

```python
答案 = ab.ask_agent("把这段话改成 60 字以内的客服回复", agent="doubao", timeout=60)
```

要点：

- **`model` 必须是控制台创建的接入点 id（`ep-...`）**，写模型名会 404。缺了会直接报
  `缺少 model（豆包填方舟控制台的接入点 id…）`，不会静默用别的模型。
- 方舟的思考模型会返回 `reasoning_content`，本桥只取 `choices[0].message.content`；
  要保留推理过程，改 `OpenAICompatProvider.invoke` 里的解析即可（一行）。
- 流式没做：RPA 场景对首字延迟不敏感，一次性返回更省事；要流式加 `/v1/jobs` + 进度查询。

## 3. DeepSeek Harness（dsh）

dsh 有 headless 模式，正好是 RPA 需要的形状：

```powershell
dsh headless "把 build 日志里的报错归类"      # 任务作为参数
Get-Content task.txt | dsh headless -        # 或从 stdin（长文本走这条）
```

- stdout = 最终答案，stderr = `dsh: reasoning:` 推理流，退出码 0 成功 / 1 失败。
- `--json` 输出按行事件（`session` → `text`/`tool_call`/`tool_result` → `final`），
  监控进程可以拿它做进度条。
- `--session-id <id>` 续同一段会话，多轮任务靠它。
- **凭据**：headless profile 不读 GUI 里存的 key，只认启动环境、`<cwd>/.env`、`$DSH_HOME/.env`。
  所以网关跑在服务账号下时，先把 key 放进服务账号的 `.env`，否则第一次调用就会 401。

本桥的默认装配已经带上：

```powershell
$env:AGENTRPA_DSH_CMD  = "dsh"
$env:AGENTRPA_DSH_ARGS = "headless -"     # 任务走 stdin，避开 Windows 8k 命令行上限
$env:AGENTRPA_DSH_TIMEOUT = "900"
```

调用：

```python
答案 = ab.ask_agent("核对这份对账单和系统导出是否一致", agent="dsh", timeout=600,
                    meta={"flow": "reconcile"})
```

注意 dsh 是**一次一个进程**（node 冷启动约 2–5 s），不像 codex MCP 那样常驻。
高频短任务用 codex，长任务（几分钟级）用 dsh，冷启动占比可以忽略。

想在网关里给 dsh 加并发，就同名多实例：

```python
AgentPool([AgentSpec(name="dsh-1", kind="cli", command="dsh", args=["headless", "-"], timeout=900),
           AgentSpec(name="dsh-2", kind="cli", command="dsh", args=["headless", "-"], timeout=900)])
# RPA 侧按 hash(工单号) % 2 选 dsh-1 / dsh-2
```

## 4. 组合建议（企业里真会这么排）

```
影刀                    网关                    后端
├─ 取数/登录/上传  ──▶  /v1/invoke agent=codex   ──▶  Codex（改脚本、跑命令）
├─ 判断/写文案     ──▶  /v1/invoke agent=doubao  ──▶  豆包（便宜、中文稳）
└─ 长任务/复核     ──▶  /v1/jobs   agent=dsh     ──▶  dsh headless（几分钟级）
```

一个流程里换后端只是改 `agent` 字段，RPA 侧的代码块不用动。
`meta.flow` 与 `meta.batch` 会跟着进审计，事后能按"哪个流程用了哪个后端、花了多久"出报表。

## 5. 自检清单

```powershell
python scripts\export_mcp_config.py --print                 # 看配置内容
python agentrpa_bridge\yingdao_mcp_server.py --selftest      # 反向 MCP 是否就绪
python -m unittest discover -s tests -t .                    # 50 个测试
curl -s localhost:8765/v1/agents -H "Authorization: Bearer tok-a"   # 看后端清单与会话状态
```

`/v1/agents` 会同时告诉你每个后端当前是 `cold` 还是 `alive`，
以及 dsh/doubao 是否因为缺 key/缺 model 而没装配起来。
