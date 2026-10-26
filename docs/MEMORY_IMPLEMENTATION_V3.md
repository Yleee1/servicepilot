# ServicePilot 长期记忆实现报告

## 已落地

- 版本化 `MemoryRecord` 与 `MemoryBackend` 接口，支持 add/search/update/invalidate/history。
- `SQLiteMemoryBackend` 保存当前记忆和追加式审计历史；并发写入使用确定的事务顺序。
- Add Pipeline 覆盖敏感信息/指令投毒拦截、运营状态拒写、同义重复合并、时间更新、作用域例外、真冲突争议和低权威冲突。
- Search Pipeline 在 SQL 层先执行 subject/scope ACL，再做关键词、作用域、来源权威和冲突惩罚排序；`deleted/expired/disputed` 默认不注入。
- LangGraph 新增 `memory_read`/`memory_write` 节点；读取发生在 Supervisor 前，写入只在 Verifier 通过后发生。Graph State 仅保存 memory ID、压缩摘要和审计事件。
- pending action、确认凭证、权限、幂等键和退款状态仍只来自 checkpoint/业务数据库；Policy Gate 不读取记忆字段。
- Demo 轨迹已包含 `memory_hit_ids` 和 `memory_audit_events`。
- `GraphServiceSession` 支持注入本地 JSONL 和 Langfuse v4 Trace Sink；只输出 thread/run、memory ID、handoff、工具结果引用、Verifier 结论和错误码，不发送用户原文、工具参数或业务返回数据。可选 Sink 失败时记录日志但不改变执行语义。

## 真实验证

```bash
./.venv/bin/ruff check src tests
./.venv/bin/pytest -q
PYTHONPATH=src ./.venv/bin/python -m servicepilot.memory_eval_cli
```

- 全量自动化测试：69 passed。
- 记忆治理验收：12/12 passed，覆盖召回、同义合并、更新、冲突、作用域、跨用户 ACL、删除、低权威冲突、运营状态、投毒、并发和重启。
- 该套件中 Cross-user Leakage=0、Operational-state Write=0、Unsafe Execution=0。
- 原始记录：`results/memory_v3_governance_eval.json`。
- Langfuse 4.18.0 的 `start_as_current_observation` 真实 SDK 契约测试通过；本机未配置 Langfuse 服务端凭据，因此不声称远端 trace 上传或生产观测验收。

12/12 是确定性记忆治理验收，不是小模型端到端 Scenario Success。

## Mem0 与 M0—M6 真实消融

- `Mem0MemoryBackend` 现使用 Mem0 1.0.11 的真实 `Memory` API、本地 Qdrant 和无网络 deterministic embedding；add/search/update/delete 以及跨用户 ACL 集成测试通过。
- 新建 14/14/14 条 dev/held-out/frozen 多 Session 集。Frozen memory-subsystem Scenario Success：M0 35.71%、M1 21.43%、M2 21.43%、M3 50.00%、M4 92.86%、M5 85.71%、M6 92.86%。
- M3 是 Mem0 OSS 本地 data-plane 基线（`infer=False`、deterministic embedding，不调用云端抽取模型）；M4 叠加本项目 scope/conflict/write policy 后提升 42.86pp。M1/M2 低于 No Memory，暴露全历史堆叠、跨用户泄漏、过期/冲突记忆和无关召回负迁移。
- 该矩阵只评测记忆子系统事件序列，不是小模型端到端客服 Scenario Success。数据为作者构造，尚未完成第二审阅者签字。

## 窄职责 Adapter 实验

- Router、Argument、Memory Classifier、Conflict Classifier 各生成 96 条数据，72/12/12 split 的输入交集均为 0；使用 Qwen3-1.7B 4-bit、8-layer LoRA、40 steps 分别训练四个 Adapter。
- 独立 test exact JSON match：Router 33.33%→91.67%，Argument 0→50.00%，Memory Classifier 0→50.00%，Conflict Classifier 16.67%→91.67%；合计 12.50%→70.83%。
- 这些是窄任务合成 test 指标，不替代 Graph V2 frozen 的端到端 Scenario Success 和安全指标。

## M7 Evo-Harness 自演化

- 官方 Evo-Harness 固定在 commit `3c7c7b8c62a3f07da1d4407aa03f0ca079954bcf`；M7 使用其 `AgentWorkspace`/`Observer` 合同，并实现 ServicePilot 专用 Context-to-Harness pipeline。编译器是本地 MLX Qwen3-1.7B-4bit，不向外部 API 发送评测数据。
- M7 只能生成 character-bigram 相关性过滤 Skill 和 `[0.01, 0.5]` 白名单阈值，不能改 scorer、ACL、权限、确认或业务状态。第 1 轮阈值 0.30 令 dev 从 92.86% 退化到 71.43%，被 Dev Gate 拒绝且没有读取 held-out；第 2 轮放宽为 0.075，dev 与 held-out 均为 100%，无安全回归，候选被接受。
- 接受后才运行 frozen：同一 14 条记忆事件集上 M6 92.86%→M7 100%，修复唯一的 irrelevant negative transfer；Cross-user、operational-state、injection、task-scope 四类安全用例没有回归。Frozen 未用于候选选择。
- 完整历史位于 `results/memory_v3/M7_evolution.json`，包括被拒候选、candidate hash、编译器 token/延迟和一次性 frozen 结果。

## 结果边界

- 这是基于官方 workspace/observer 合同实现的项目专用 pipeline，不是上游五条 benchmark pipeline，也不是模型权重自训练或 Agentic RL。
- M0—M7 是同一确定性记忆事件沙箱，不是“同一 LLM 端到端客服”全矩阵；14/14 的样本量小、分类同构且由作者构造，仍需独立审阅和更大的业务场景 frozen 集。
