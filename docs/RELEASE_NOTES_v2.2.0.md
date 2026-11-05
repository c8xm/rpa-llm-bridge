# v2.2.0 · 安全与稳定性加固

> 发布日期：2026-11-05 · 上一个版本：[v2.1.0](https://github.com/c8xm/rpa-llm-bridge/releases/tag/v2.1.0)
> 这是一次"按能不能上生产"做的自审：5 个问题全部先复现、再修、再补回归用例。

---

## 一、修了什么（都有复现证据）

### 1. 没配 token 时，任意 Bearer 都能进

**复现**：`tokens={}`，`Authorization: Bearer completely-made-up` → **200**。

原来 `_tenant()` 里有一句"没配 tokens 就直接放行"，本意是本地开发方便，实际等于把网关变成无鉴权。

**修法**（`gateway.py`）：

```python
if token:
    for known, tenant in state.config.tokens.items():
        if secrets.compare_digest(token, known):
            return tenant
    return None          # 未知 token 一律 401
```

顺带把 `AGENTRPA_ALLOW_ANON` 默认值从 `1` 改成 `0`：安全默认值应该是"关"，要开自己开。

### 2. 超限请求体会打断 keep-alive 连接

**复现**：发 3MB body → 400，同一连接上再发正常请求 → `WinError 10053 连接被中止`。

原因是超过 `MAX_BODY` 直接抛错，body 还堆在 socket 里，被当成下一个请求解析。

**修法**：先按 `DRAIN_CAP`（16MB）把 body 读掉丢弃，回 `413 payload too large`，并显式 `Connection: close`。

### 3. 同一个 agent 的串行锁，不受调用方 timeout 约束

**复现**：调用方给 0.5s 预算，实际等 **2.91s** 才报错。慢请求期间网关信号量一直被占着，`503` 也救不回来。

**修法**：`RLock.acquire(timeout=预算)`，拿不到直接返回

```
[codex] busy：会话被占用超过 0.5s
```

### 4. 超时被当成"可重试错误"，预算翻倍

**复现**：`timeout=0.6s` → 实际 **1.37s** 才抛错。

`except (McpError, OSError)` 把 `McpTimeout` 一起捕获了，于是"超时 → 重连 → 再超时"。对 codex 这种能跑命令的 agent，重试还可能**重复执行副作用**。

**修法**：先单独捕获 `McpTimeout` 直接上抛；只有进程崩溃/管道断开才重连一次。

### 5. 审计只脱敏 key，不脱敏 PII

**复现**：手机号、身份证、银行卡、邮箱**原样进审计**——和 `docs/ENTERPRISE-ADOPTION.md` 里"审计不含原文单据"的承诺冲突。

**修法**（`providers.py`）：在原有 key 规则后追加 PII 规则，`prompt_head` 也走 `redact()`。

```
13800138000              → [PHONE]
11010519491231002X       → [ID_CARD]
6222021234567890123      → [BANK_CARD]
zhang.san@corp.example.com → [EMAIL]
订单 20261104 金额 11300   → 原样（8 位日期、5 位金额不误伤）
```

### 6. 附加：RPA 并发槽位泄漏

只靠 `rpa_query_job` 轮询到终态才释放槽位，没人轮询就永久占用，最后全体 `busy`。
新增 `YINGDAO_MAX_HOLD`（默认 3600s）：超过最长占用时间的槽位在下次 `acquire` / `snapshot` 时自动回收，并计入 `stats.expired`。

---

## 二、打包修复

**复现**：`python -m build` 后检查 wheel——

```
server.py in wheel: False
sdk/: []
entry_points:  agentrpa = server:main     ← 指向不存在的模块
```

`pip install` 后 `agentrpa` 命令直接 `ModuleNotFoundError`，影刀要的 `rpa_agent_block.py` 也不在包里。

**修法**（`pyproject.toml`）：

```toml
[tool.hatch.build.targets.wheel.force-include]
"server.py" = "server.py"
"sdk/rpa_agent_block.py" = "agentrpa_bridge/sdk/rpa_agent_block.py"
```

---

## 三、回归用例（新增 10 个）

`tests/test_hardening.py`：

| 用例 | 锁死的行为 |
|---|---|
| `test_bogus_token_rejected_when_no_tokens_configured` | 空配置 + 伪造 token → 401 |
| `test_localhost_anon_still_works` | 本机匿名仍然可用（开发不被误伤） |
| `test_configured_token_ok_and_wrong_token_rejected` | 正确 token 200 / 错误 token 401 |
| `test_oversize_returns_413_and_server_survives` | 413 + 服务端仍健康 |
| `test_timeout_is_not_retried` | 超时耗时 < 2s，不许翻倍 |
| `test_second_caller_fails_fast_with_busy` | 锁等待 < 1.5s 返回 busy |
| `test_pii_masked` / `test_short_numbers_not_masked` | PII 打码且不误伤普通数字 |
| `test_audit_prompt_head_redacted` | 审计文件里搜不到手机号 |
| `test_expired_slot_is_reclaimed` | 槽位过期自动回收 |

全量结果：

```
120 passed（未装 openpyxl 时 114 passed, 1 skipped）
```

---

## 四、升级指南

纯增量，配置不用改；唯一行为变化是 **`AGENTRPA_ALLOW_ANON` 默认变 0**：

```powershell
git pull
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"   # 重新装，拿到新 wheel 内容
python -m pytest                                        # 120 passed
```

- 本机 PoC 需要匿名访问：显式 `AGENTRPA_ALLOW_ANON=1`。
- 生产：配好 `AGENTRPA_TOKENS`，客户端带 `Authorization: Bearer <token>`。
- 反方向：新增可选 `YINGDAO_MAX_HOLD`，不配则默认 3600 秒。

## 五、还没做的（下一版）

| 项 | 说明 |
|---|---|
| 多节点共享限速 | 目前限速是进程内内存态，集群部署时限额会线性放大 |
| `/v1/invoke` 幂等键 | RPA 超时重试仍可能重复跑 agent（反向 RPA 侧已有幂等） |
| `cmd /c` 参数转义 | `.cmd` shim 的 args 含 `& \| ^` 会被 cmd 解释 |
| 覆盖率门槛 | 目前只跑用例，没有 coverage gate |

---

MIT © 2026 c8xm
