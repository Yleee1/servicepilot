# ServicePilot 多 Agent 失败模式

以下 12 类均已进入自动测试或 61 条冻结场景；逐例 trace 在 `results/graph_rule_ablation_frozen_v2.json`。

| # | 失败模式 | 防线 | 当前证据 |
|---:|---|---|---|
| 1 | Supervisor 把退款直接交给写工具 | Supervisor 无工具；Refund 仅能提案 | 自动化 |
| 2 | Refund Agent 直接输出 `create_refund` | allow-list 拦截，并转换成 `propose_refund` | 自动化 |
| 3 | 退款跳过订单依赖 | `depends_on` + completed task 集合 | 自动化 |
| 4 | 新消息后旧确认仍有效 | 先 resume cancel，再启动新 run version | 自动化 |
| 5 | 修改参数后复用确认 | binding 覆盖 thread/user/tool/arguments/version | 自动化 |
| 6 | 另一个 thread 复用确认 | checkpoint 由 thread id 隔离 | 自动化 |
| 7 | 重复确认产生两次退款 | 图完成后无 pending；Executor 幂等缓存 | 自动化 |
| 8 | 进程重启丢失 pending action | SQLite checkpointer + 稳定 thread id | 独立 Python 进程 request/confirm probe |
| 9 | 工具失败却宣称成功 | Verifier 检查 failed observation 与成功宣称 | 冻结集 + evaluator 异常隔离 |
| 10 | handoff 循环 | 最大 6 次，超限转人工 | 冻结故障场景 |
| 11 | transient timeout 被当成权限错误 | 错误分类后只重试 transient | 故障注入 adapter + 冻结场景 |
| 12 | 两个连接并发确认 | SQLite 唯一幂等键 + `BEGIN IMMEDIATE` | 两个独立 DB 连接并发测试 |

本地已证明 SQLite 事务语义，但不声称它等于生产级多机部署；生产仍应使用 PostgreSQL 唯一约束与业务事务。
