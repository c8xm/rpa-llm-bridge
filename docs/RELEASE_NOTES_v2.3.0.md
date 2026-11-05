# v2.3.0 · 调用幂等与参数注入防护

> 发布日期：2026-11-05 · 上一个版本：[v2.2.0](https://github.com/c8xm/rpa-llm-bridge/releases/tag/v2.2.0)

v2.2 把"能安全上线"补上了；v2.3 补的是**RPA 场景每天都在发生的那两件事**：超时重试导致的重复执行，以及把用户输入拼进命令行。

---

## 1. `Idempotency-Key`：重复请求只跑一次

RPA 节点超时会重试，以前这等于让 agent 再跑一遍（codex 带工具时还可能重复执行副作用）。

现在带一个业务单号即可：

```powershell
curl -s -X POST http://127.0.0.1:8765/v1/invoke `
  -H "Authorization: Bearer tok-a" `
  -H "Idempotency-Key: order-88123" `
  -H "Content-Type: application/json" `
  -d '{"agent":"codex","prompt":"把这张发票的税额算出来"}'
```

行为：

| 情况 | 结果 |
|---|---|
| 首次请求 | 正常执行，结果进缓存 |
| 窗口内重复请求 | 直接回放，`idempotent_replay: true`，耗时毫秒级 |
| 并发重复请求 | 后到的等待首个完成，然后回放；等待超过 `AGENTRPA_QUEUE_TIMEOUT` 回 `409 duplicate request in flight` |
| 不同 key | 各跑各的 |
| 不带 key | 与 v2.2 一致，每次执行 |

- 缓存键 = `(tenant, key)`，跨租户不会串。
- 窗口由 `AGENTRPA_IDEMPOTENCY_TTL` 控制（默认 900s，0 = 关闭）。
- 失败结果同样缓存，重试拿到的还是同一条错误，不会"重试出一个成功"。

## 2. `.cmd` 参数转义：prompt 里的 `&` 不再是命令

npm 装的 `codex` / `claude` / `dsh` 都是 `.cmd` shim，走 `cmd /c` 时参数会被二次解析：

```text
{"prompt": "统计完顺便 & calc"}   →  旧行为：cmd 把 & 后面的内容当第二条命令
```

现在 `argv_for()` 对 shim 参数统一转义 `& | < > ^ ( )`，实测：

```
a & echo INJECTED   → 原样作为参数传入，不会多出一行 INJECTED
x | whoami          → 原样
x > out.txt         → 原样
```

已知边界：空字符串参数经 `subprocess` 列表 + `cmd /c` 会被再包一层引号（不影响调用，值不保证为空）；需要严格空参的场景请改走 stdin。

## 3. 回归用例（新增 11 个）

| 文件 | 覆盖 |
|---|---|
| `tests/test_idempotency.py` | 同 key 只跑一次 / 不同 key 各跑一次 / 无 key 每次跑 / 并发同 key 只跑一次 |
| `tests/test_cmd_escape.py` | `&`、`\|`、`>`、`<`、`^` 不当命令执行；带空格路径；空参不崩；shim 包裹形态 |

全量：

```
131 passed（未装 openpyxl 时 125 passed, 1 skipped）
```

## 4. 升级

```powershell
git pull
python -m pytest          # 131 passed
```

- 不需要改配置；想让幂等生效，客户端带 `Idempotency-Key` 即可。
- 新增可选 `AGENTRPA_IDEMPOTENCY_TTL`（默认 900）。
- 幂等缓存是**进程内**的：多节点部署时，同一个 key 落到不同节点仍会各跑一次；要跨节点幂等，需要把缓存换成 Redis（路线图）。

## 5. 还没做

| 项 | 说明 |
|---|---|
| 跨节点幂等 / 限速 | 目前都是进程内内存态，集群场景会放大 |
| 覆盖率门槛 | 有 131 个用例，但还没有 coverage gate |
| `--json` 事件流 → SSE | 影刀里仍看不到"第几步"的实时进度 |

---

MIT © 2026 c8xm
