# 项目归档 · rpa-llm-bridge v0.1.0

> 这份文档是给"下一个对话"用的交接档案。新会话里把这个文件贴进去或作为附件上传，
> 不需要重新翻仓库就能继续开发。

---

## 0. 一句话

**rpa-llm-bridge** = 把 LLM（通过 MCP）接到 RPA 上的本地执行内核：一套统一 action 契约，驱动浏览器 / Windows 桌面 / Office 文件；YAML 描述流程；策略闸门管住不可逆动作；审计留痕；支持人工闸门暂停与断点续跑。

| 项 | 值 |
|---|---|
| 仓库 | https://github.com/c8xm/rpa-llm-bridge |
| 本地路径 | `D:\github\rpa-llm-bridge` |
| 版本 | v0.1.0（tag 已推，Release 已发布） |
| 许可 | MIT，Copyright (c) 2026 c8xm |
| 代码量 | 45 个入库文件 / 约 1728 行 Python |
| 测试 | 43 个单元测试，全绿，不启动外部进程 |
| 默认分支 | `main` |

---

## 1. 技术栈

- Python 3.11+（本机 3.14 也能跑测试；CI 覆盖 3.11 / 3.12）
- MCP：`mcp[cli]>=1.27,<2`，FastMCP，stdio 为主，`--http` 走 streamable-http
- 浏览器：`playwright>=1.54`，持久化 Profile
- 可选：`pywinauto`（桌面，仅 Windows）、`openpyxl` + `python-docx`（Office）
- 测试：pytest + pytest-asyncio（`asyncio_mode=auto`）
- 打包：hatchling；入口 `agentrpa = "server:main"`

---

## 2. 目录地图

```text
rpa-llm-bridge/
├─ server.py                    MCP 工具层（27 个工具），只做参数收敛与路由
├─ agentrpa/
│  ├─ config.py                 Settings.from_env()，22 个环境变量
│  ├─ core/
│  │  ├─ types.py               Step / StepResult / RunResult / Decision
│  │  ├─ policy.py              唯一安全判定点（域名/应用/输入/状态变更）
│  │  ├─ runner.py              RecipeRunner：顺序执行、重试、暂停、续跑、状态落盘
│  │  ├─ audit.py               AuditLog：jsonl + 双重脱敏
│  │  └─ registry.py            驱动注册表（当前 server.py 手工组装，未使用）
│  ├─ drivers/
│  │  ├─ base.py                BaseDriver + DriverError + PolicyBlocked
│  │  ├─ web.py                 Playwright：navigate/snapshot/elements/click/fill/press/scroll/back/screenshot/extract/wait
│  │  ├─ desktop.py             pywinauto UIA：launch/windows/focus/elements/click/fill/press/read
│  │  └─ office.py              openpyxl+docx：sheets/read/write/append
│  └─ recipes/
│     ├─ loader.py              YAML 校验、Step 构造、{{var}} 渲染
│     └─ examples/*.yaml        5 个场景模板
├─ tests/                       43 用例
├─ docs/
│  ├─ DESIGN.md                 4 个关键取舍
│  ├─ RELEASE_NOTES_v0.1.0.md   Release 正文（版本化）
│  ├─ HANDOFF.md                本文件
│  └─ assets/                   architecture.png / demo.png / social-preview.png
├─ scripts/
│  ├─ install_windows.ps1       一键装环境
│  └─ make_assets.py            配图生成（架构图/终端图/社交卡片）
└─ .github/workflows/ci.yml     pytest × (ubuntu, windows) × (3.11, 3.12) + ruff + build
```

---

## 3. 核心契约（改代码前必读）

### 3.1 驱动接口

```python
class BaseDriver:
    name = "browser"
    def __init__(self, settings, policy, audit): ...
    async def start(self) -> dict: ...
    async def close(self) -> dict: ...
    async def health(self) -> dict: ...
    def capabilities(self) -> list[str]: ...
    async def execute(self, action: str, params: dict) -> Any: ...
```

约定：

- `execute` 一律 `async`；office/desktop 内部是同步实现，但外层包了 async。
- 失败抛 `DriverError`；策略拒绝抛 `PolicyBlocked(reason, requires_human)`。
- 返回值必须是可 JSON 序列化的 dict（MCP 直接回给模型）。

### 3.2 Step / RunResult

```python
Step(id, action, driver="browser", params={}, description="", hitl=False,
     on_error="abort"|"skip"|"retry", retries=0)
```

### 3.3 状态机

```text
running ──hitl 或策略 requires_human──▶ awaiting_human ──run_resume(approve=True)──▶ running ──▶ completed
                     │
                     └── 失败且 on_error=abort ──▶ failed
```

- 暂停时写 `artifacts/runs/<run_id>.json`，字段：`run_id / recipe / status / index / done / vars / started_at / finished_at / steps / results`。
- **续跑从 index 指向的那一步开始执行**（不是下一步），并带 `auto_approve=True`；写成 `index+1` 会跳过待确认动作，这是已修过的 bug。

### 3.4 策略矩阵

| 方法 | 判据 | 默认 |
|---|---|---|
| `check_url` | http(s) + 域名白名单（后端子域匹配） | 拒绝 `jd.com.evil.cn` 这类同形域名 |
| `check_app` | `ALLOWED_APPS` 后缀/包含匹配 | 列表为空 → 全拒 |
| `check_input` | label 命中敏感词（密码/验证码/OTP/卡号/CVV/身份证） | 拒绝 |
| `check_click` | label 命中交易/发布/删除词 | 默认拦截并要求人工 |
| `check_step` | 执行前静态检查（navigate/launch/click） | — |

开关组合语义：

- `ALLOW_STATE_CHANGING_ACTIONS=false` + `REQUIRE_HITL=true` → 拦截 + 需人工（默认）
- `true` + `false` → 放行
- `false` + `false` → 硬拒绝，不留人工入口

---

## 4. 测试现状

```powershell
cd D:\github\rpa-llm-bridge
.\.venv\Scripts\python.exe -m pytest        # 43 passed
```

| 文件 | 覆盖 |
|---|---|
| `test_policy.py` | 14 例：白名单、同形域名、非 http、敏感字段、状态变更三组合、应用白名单、check_step |
| `test_runner.py` | 10 例：完成、暂停→续跑、失败中止、跳过、重试计数、dry-run、模板渲染、未知驱动、策略拦截、状态落盘 |
| `test_recipes.py` | 9 例：5 个示例可加载、缺失/空 steps、重复 id、非法 on_error、驱动继承、模板替换 |
| `test_audit.py` | 5 例：写入、读取、key 脱敏、label 联动脱敏、limit |
| `test_office_driver.py` | 6 例：建表写、追加读、缺表、缺文件、health、未知 action（`importorskip("openpyxl")`） |

`tests/conftest.py` 提供 `settings / policy / audit` fixture 与 `FakeDriver`（记录调用序列，可注入失败）。

---

## 5. 已经踩过并修掉的坑（别重复踩）

1. **`drivers/base.py` 相对导入层级错**：写成 `from .audit import ...` 导致 `ModuleNotFoundError: agentrpa.drivers.audit`。正确是 `from ..core.audit import ...`。compileall 抓不到，必须真的 import 驱动。
2. **openpyxl 新建工作簿工作表不存在**：`wb["T"]` 直接 KeyError。已加 `_target_sheet(..., fresh=True)`：新建文件时改 `wb.active.title`，不额外造空表。
3. **续跑跳过待确认步骤**：`resume` 里写 `index + 1` 会跳过 hitl 那一步。已改为从 `index` 开始 + `auto_approve`。
4. **审计只按 key 脱敏**：`text` 这个 key 不含敏感词，密码明文入库。已加"label 命中敏感词则联动脱敏 text/value/input"。
5. **PowerShell 读写 UTF-8 文件**：`Get-Content` + `Set-Content` 会按 GBK 解码再写回，把中文文档变成乱码。改文件用编辑工具，不要用 PS 管道重写。
6. **pip 直连 PyPI 会卡死**（本机网络）：一律加 `-i https://pypi.tuna.tsinghua.edu.cn/simple --timeout 30`。
7. **git 直连 github 超时**：系统代理 `127.0.0.1:7890`，但 git 不读 WinINET。已在仓库本地配置 `http.proxy=http://127.0.0.1:7890`。

---

## 6. 未完成 / 下一步

优先级从高到低（与置顶 issue #1 一致）：

### P0 多驱动混合流程
当前 `Step.driver` 已能逐步切换，缺的是**跨步骤数据传递**。

建议实现：

```yaml
- id: grab
  driver: browser
  action: extract
  multi: { prices: ".p-price" }   # output 进 ctx
- id: save
  driver: office
  action: append
  rows: "{{steps.grab.lists.prices}}"   # 引用上游 output
```

- `runner._execute_step` 已拿到 output，塞进 `result.steps`；渲染时把 `steps.<id>.<path>` 注入 render 的变量上下文即可，改动面在 `recipes/loader.render` + `runner._loop`。
- 需要补测试：跨步骤引用、缺失引用、循环引用检测。

### P1 Recipe 条件与循环
`when`（基于上一步 output 的布尔）/ `foreach`（遍历数组）。建议先在 `loader` 侧做静态校验，`runner` 侧做展开。

### P2 录制回放
从 Playwright trace 或桌面控件点击日志生成 Recipe 草稿。依赖较重，建议独立模块 `agentrpa/recorder/`。

### P3 审计聚合查询
现在只有 `audit_tail`。加 `audit_query(recipe=, status=, since=)`，索引放在内存即可（日志量小）。

### P4 桌面视觉回归
控件截图对比，用于发现 Selector 失效。需要稳定的控件定位策略，先做单控件 demo。

---

## 7. 环境复原（新对话直接照抄）

```powershell
cd D:\github\rpa-llm-bridge
# .venv 已存在，直接用；若丢失则重建：
# python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple --timeout 30 pytest pytest-asyncio pyyaml python-dotenv "mcp[cli]" openpyxl python-docx
.\.venv\Scripts\python.exe -m pytest
```

运行 MCP 服务（stdio 会被客户端拉起，直接跑会停在等待状态）：

```powershell
.\.venv\Scripts\python.exe server.py
```

---

## 8. GitHub 线上状态

| 项 | 值 |
|---|---|
| Release | https://github.com/c8xm/rpa-llm-bridge/releases/tag/v0.1.0 |
| Topics | rpa, mcp, playwright, automation, llm, agent, python, windows-automation |
| 置顶 issue | #1 Roadmap v0.2 |
| CI | ubuntu/windows × 3.11/3.12 + ruff + wheel build，最近一次成功 |
| 社交预览 | 卡片已生成 `docs/assets/social-preview.png`，需在 Settings 手动上传 |
| 凭据 | 本机 git 走 credential manager（GitHub Desktop OAuth），可直接 push |

---

## 9. 新对话开场提示词（复制这段）

```text
接续一个已开源的 Python 项目：rpa-llm-bridge（本地路径 D:\github\rpa-llm-bridge，
远程 https://github.com/c8xm/rpa-llm-bridge，v0.1.0）。

它是一个通过 MCP 暴露给 LLM 的 RPA 执行内核：统一 action 契约驱动浏览器(Playwright)/
Windows 桌面(pywinauto)/Office(openpyxl)，YAML Recipe 编排，Policy 集中做安全闸门，
支持 hitl 暂停 + 断点续跑 + jsonl 审计。43 个单元测试全绿。

交接文档见 docs/HANDOFF.md（已附上）。现在要做的是 P0：多驱动混合流程，
即 Recipe 步骤之间传递 output（例如浏览器抽取的价格列表喂给 office append）。
请先读 agentrpa/core/runner.py 与 agentrpa/recipes/loader.py，给出改动方案再动手。
```

---

## 10. 变更日志

```text
2ce2740 docs: 增加 1280x640 社交分享卡片
209d773 docs: 归档 v0.1.0 发布说明
1ccc18d docs: 增加架构图与真实运行截图，配图生成脚本化
57cd635 docs: 修正 clone 地址为 rpa-llm-bridge
038a0bc feat: 接入 AgentRPA 内核 —— 统一 action 契约 / 三驱动 / Recipe 编排 / 策略闸门 / 审计
a6800ee Initial commit（GitHub Desktop 空模板）
```
