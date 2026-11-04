# v2.1.0 · 企业护栏与接入方案

> 发布日期：2026-11-04 · 上一个版本：v2.0.0

## 一句话

v2.0 解决了"能不能接"，v2.1 解决"敢不敢在生产接"：**给反向触发 RPA 的动作加上白名单、审批、并发与幂等四道闸门**，并补一份可直接拿去评审的企业接入方案。

## 新增：企业护栏 `agentrpa_bridge/policy.py`

反向触发企业 RPA 属于对内部系统的**写操作**，比模型调用更需要按"应用"维度管，而不是按 tenant。

| 护栏 | 变量 | 行为 | 拒绝码 |
|---|---|---|---|
| 应用白名单 | `YINGDAO_ALLOWED_APPS` | 白名单外的应用直接拒绝，不产生任务 | `app_not_allowed` |
| 人工审批 | `YINGDAO_APPROVAL_APPS` | 必须带 `approved_by`（审批人工号/工单号） | `requires_approval` |
| 单应用并发 | `YINGDAO_MAX_PER_APP` | 满即返回 busy，不排队、不堆积 | `busy` |
| 幂等窗口 | `YINGDAO_IDEMPOTENCY_TTL` | 同参数窗口内重复提交返回同一 `jobId` | —（`deduplicated: true`） |

设计细节：

- **槽位随任务终态释放**：`rpa_query_job` 观察到 `success/failed/...` 才归还并发名额。
- **幂等键 = sha256(app_id + 规范化 params)**，参数顺序不影响结果；窗口过期自动失效。
- **拒绝是结构化 JSON**（`isError: true`），智能体可直接分支，不需要解析自然语言。
- 空白名单 = 开发模式放行；生产建议必填。

新增工具 `rpa_policy`，可查看白名单、审批清单、并发上限、幂等窗口、在跑任务数与拒绝统计：

```json
{"whitelist_enforced": true, "allowed_apps": ["app_invoice", "app_reconcile"],
 "approval_apps": ["app_reconcile"], "max_per_app": 2, "idempotency_ttl_s": 900.0,
 "inflight": {"app_invoice": 1}, "stats": {"denied": 1, "deduplicated": 2, "busy": 1, "approved": 1}}
```

## 新增：`docs/ENTERPRISE-ADOPTION.md`

企业接入不是技术问题，是权限、账号、网络、评审四个关口。文档给出：

- **角色分工**：业务 Owner / 影刀管理员 / IT / 安全 / 集成方，各自提供什么、不做什么
- **三种拓扑**：内网+受控出网、完全隔离（内网模型）、MCP 反向驱动
- **开干前的 6 样东西**：企业版权限、应用授权、机器人账号、机器、网络策略、安全评审入口
- **四阶段路径**：P0 单机 PoC（1 周）→ P1 单产线灰度（2–4 周）→ P2 集群（1–2 月）→ P3 规模化治理，每阶段带出口条件
- **评审必答 10 题**：数据出不出内网、谁负责、离线能不能用、模型胡说怎么办……
- **验收清单 10 项**与**常见否决理由的应对**

其中最关键的一句写在文档里：**agent 只产出建议值，不可逆动作仍在影刀既有审批节点执行。**

## 修复

- `RpaGuard` 内部锁改为 `RLock`：`acquire()` 持锁时调用统计自增会自锁，导致 MCP 调用挂死（测试暴露后修正）。

## 验证

```powershell
python -m pytest                    # 110 passed（内核 43 + 桥 51 + 反方向 5 + 护栏 11）
python agentrpa_bridge\yingdao_mcp_server.py --selftest   # 含 policy 快照
python examples\reverse_demo.py     # 无影刀环境的全链路
```

护栏端到端（`tests/test_rpa_guard.py`）覆盖：白名单拒绝、审批缺失、审批放行、幂等去重、幂等过期、并发打满、槽位随终态释放。

## 兼容性

- 纯增量：v2.0 的环境变量与接口全部不变。
- 不配 `YINGDAO_*` 护栏变量时行为与 v2.0 一致（开发模式）。
- 升级：`git pull` 即可，无需重装依赖。

---

MIT © 2026 c8xm
