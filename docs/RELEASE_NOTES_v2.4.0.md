# v2.4.0 · 跨节点共享状态（限速与幂等）

> 发布日期：2026-11-05 · 上一个版本：[v2.3.0](https://github.com/c8xm/rpa-llm-bridge/releases/tag/v2.3.0)

v2.3 给单机补上了幂等。但只要有第二台网关，限速就变成两倍、幂等各算各的——每个节点只看得到自己的计数。v2.4 把这个状态从进程里搬出来。

---

## 1. 可插拔的 state store

```powershell
$env:AGENTRPA_STATE_BACKEND = "sqlite"          # memory（默认）| sqlite
$env:AGENTRPA_STATE_PATH    = "D:\bridge\state.db"
```

| 后端 | 适用 | 实现 |
|---|---|---|
| `memory` | 单进程 / PoC | 进程内 dict + deque，零依赖，重启即清空 |
| `sqlite` | 同机多进程、共享盘 | WAL + `BEGIN IMMEDIATE`，跨进程计数与幂等一致 |
| Redis | 多机（未内置） | 按 `store.py` 的 4 个方法实现一个类即可替换 |

`store.py` 的契约只有四个动作，换后端不碰网关：

```python
allow(tenant, limit, window)        # 滑动窗口限速
idem_begin(key) -> (state, payload, code)
idem_finish(key, code, payload)
idem_get(key)
```

## 2. 跨进程一致性怎么保证

- **限速**：一条 `(tenant, ts)` 表，`BEGIN IMMEDIATE` 内 delete → count → insert，两个进程不会同时读到"还没满"。
- **幂等**：`idem` 表 + `(k, done, code, payload, ts)`；`idem_begin` 在同一事务里决定 `new / inflight / done`，并发重复请求最多只有一个拿到 `new`。
- **等待方**：拿不到 `new` 的请求按 50ms 轮询 `idem_get`，等到 `done` 就回放，超时回 `409 duplicate request in flight`。
- **清理**：过期条目在 `sweep()` / 下一次 `idem_begin` 时删除；限速表按窗口删除。

## 3. 验证（新增 7 个用例）

| 用例 | 证明什么 |
|---|---|
| `test_two_instances_share_one_budget` | 两个 store 实例共用一份额度，租户之间互不影响 |
| `test_cross_process` | **真的起子进程**去抢额度 → `DENIED` |
| `test_inflight_then_done_across_instances` | A 建键 → B 看到 `inflight` → A 完成 → B 拿到 `done` |
| `test_expired_entry_is_reusable` | TTL 过期后同 key 可以重新执行 |
| `test_same_key_on_two_nodes_runs_once` | **两个网关服务 + 一个库**：同 key 只跑一次 agent |

全量：

```
138 passed（未装 openpyxl 时 132 passed, 1 skipped）
```

## 4. 升级

```powershell
git pull
python -m pytest        # 138 passed
```

- 默认仍是 `memory`，行为与 v2.3 完全一致，不需要改配置。
- 多节点部署：设 `AGENTRPA_STATE_BACKEND=sqlite` + 同一个 `AGENTRPA_STATE_PATH`。
- 跨机共享盘放 SQLite 属"能用但别硬扛"：网络文件系统锁语义有差异，量大换 Redis。

## 5. 还没做

| 项 | 说明 |
|---|---|
| Redis 后端 | 接口已就位，缺一个实现类 |
| `dsh --json` → SSE | 影刀里仍看不到实时进度 |
| coverage gate | 有 138 个用例，CI 还没有覆盖率门槛 |

---

MIT © 2026 c8xm
