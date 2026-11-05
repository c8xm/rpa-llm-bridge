# v2.5.0 · SSE 进度流

> 发布日期：2026-11-05 · 上一个版本：[v2.4.0](https://github.com/c8xm/rpa-llm-bridge/releases/tag/v2.4.0)

到 v2.4 为止，影刀点完「运行」只会看到转圈，直到最后拿到一段文本。v2.5 让它能看见进度：**作业级事件流 + SSE 端点 + 断点续传**，并把 `dsh --json` 的真实事件接进来。

---

## 1. 新端点：`GET /v1/jobs/{id}/events`

```powershell
$r = curl -s -X POST http://127.0.0.1:8765/v1/jobs `
  -H "Authorization: Bearer tok-a" -H "Content-Type: application/json" `
  -d '{"agent":"dsh","prompt":"核对这几个月的对账单"}' | ConvertFrom-Json

curl -N "http://127.0.0.1:8765/v1/jobs/$($r.run_id)/events" -H "Authorization: Bearer tok-a"
```

```text
id: 1
event: progress
data: {"stage": "queued", "seq": 1, "ts": 1790000000.1}

id: 3
event: progress
data: {"stage": "tool_call", "name": "read_file", "seq": 3, "ts": 1790000002.4}

id: 9
event: end
data: {"seq": 9}
```

生命周期：`queued → running → （后端事件） → succeeded / failed`。

## 2. 事件从哪来

| 后端 | 事件 |
|---|---|
| CLI（参数含 `--json`，如 `dsh headless --json -`） | **真实逐行解析**：`session` / `text` / `tool_call` / `final` … |
| CLI（无 `--json`） | 1 条 `backend_running` |
| MCP（codex / claude） | `waiting_backend` + `backend_started`（tools/call 本身不支持流） |
| OpenAI 兼容 | `waiting_backend` |

开启 dsh 真实进度：

```powershell
$env:AGENTRPA_DSH_ARGS = "headless --json -"
```

## 3. 断点续传

```text
GET /v1/jobs/{id}/events?since=3
GET /v1/jobs/{id}/events        （带 Last-Event-ID: 3）
```

重连不会重复收到旧事件，也不会漏；服务端只保留最近 200 条，避免长任务把内存撑爆。空闲时每 10 秒发一次 `event: ping` 心跳，防代理/网关超时断流。

## 4. 影刀侧

```python
import rpa_agent_block as ab

def 收到进度(event):
    if event["stage"] == "tool_call":
        影刀变量.当前步骤 = event.get("name", "")
    elif event["stage"] == "final":
        影刀变量.进度 = 100

答案 = ab.ask_agent_stream("核对这几个月的对账单", on_event=收到进度,
                           agent="dsh", timeout=600)
```

`ask_agent_stream` 内部：`POST /v1/jobs` → 读 SSE → 轮询取最终文本；流断了还有轮询兜底。只支持网关模式（本地 stdio 没有事件流）。

## 5. 修掉的一个真 bug

`threading.Condition` 绑定 `_jobs_lock` 后，`notify_all()` 必须在持锁时调用。v2.4 之前只有一个 job 表、没人 notify，所以没暴露；加事件流后第一版把 `notify_all()` 写在 `with` 外面，作业线程直接抛 `RuntimeError: cannot notify on un-acquired lock`，任务永远停在 `queued`。测试里复现后修正，现在 `set_job` / `job_emit` 都在锁内 notify。

## 6. 验证（新增 5 个用例）

| 用例 | 证明 |
|---|---|
| `test_events_are_parsed` | CLI `--json` 逐行解析，`final` 文本正确 |
| `test_without_json_flag_emits_single_event` | 无 `--json` 只发 `backend_running` |
| `test_stream_until_end` | SSE 依次收到 queued/running/text/final/succeeded，seq 单调不重复 |
| `test_resume_with_since` | `?since=N` 续传与首次结果一致 |
| `test_sdk_stream_helper` | `ask_agent_stream` 拿到事件并返回最终文本 |

全量：

```
143 passed（未装 openpyxl 时 137 passed, 1 skipped）
```

## 7. 升级

```powershell
git pull
python -m pytest        # 143 passed
```

- 不需要改配置；不订阅 SSE 的调用方行为完全不变。
- 想看到真实进度：`AGENTRPA_DSH_ARGS="headless --json -"`。

## 8. 还没做

| 项 | 说明 |
|---|---|
| Redis 后端 | store 接口已就位，多机部署时才需要 |
| 覆盖率门槛 | 143 个用例，CI 还没有 coverage gate |
| 事件持久化 | 作业事件只在内存，进程重启后不可回放 |

---

MIT © 2026 c8xm
