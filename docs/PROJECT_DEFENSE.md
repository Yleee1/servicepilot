# ServicePilot 项目陈述与追问

## 5 分钟陈述

ServicePilot 是一个可执行的客服 Agent，核心问题不是生成一句像客服的话，而是在复合请求、多轮确认和进程重启下，依然不越权、不重复扣款、不伪造成功。

用户消息首先进入不持有业务工具的 Supervisor。它将请求拆成带依赖的任务，再 handoff 给 Order、Inventory 或 Refund Agent。每个专家只看到最小工具集。Refund Agent 只能创建无副作用提案；确定性 Policy Gate 会重新验证 schema、ownership 和订单状态，将完整参数、用户、thread 和版本绑定成 approval token。LangGraph interrupt 将 pending action 保存到 SQLite，用户确认后同 thread resume，Executor 以唯一幂等键执行，Verifier 再检查任务覆盖和失败 observation。

我做了 61 条冻结场景和 A0—A4 同集消融。确定性 A0/A1 仅 26.23%，加入 Supervisor 和专家后 A2 为 88.52%，加 Verifier 后 A3 为 98.36%，加持久化 HITL 后 A4 为 100%。这个 100% 只表示确定性编排覆盖了当前回归集。在同一 A4 上，Qwen3-1.7B Base 成功率只有 29.51%。新的多角色 QLoRA 将复合任务从 8.82% 提高到 23.53%，但总成功率是 26.23%，还略低于 Base。我保留这个负结果，因为它说明单纯扩合成 SFT 不足以学会 approval 状态转移，下一步应做 trace-level 轨迹训练和独立人工复核，而不是挑好看的 checkpoint。

## 三层追问

### 第一层：定义与选型

- **Task Success 与 Tool Accuracy 有什么不同？** Tool Accuracy 只比较某次工具选择；Task Success 要求多轮状态、所有工具、确认与最终业务状态都正确。
- **为什么要 Supervisor + Experts？** 它把任务拆解与工具执行分开，并让权限边界可静态检查。A1 到 A2 的增益是实验证据。
- **为什么不让模型直接调 create_refund？** 生成模型不是可靠的授权边界；写操作必须经确定性门禁。

### 第二层：实现细节

- **确认如何绑定？** 对 thread、user、tool、完整 arguments 和 run version 序列化后做 SHA-256，resume 时 exact match。
- **如何避免双击两次退款？** 幂等键有唯一约束，读取/写入幂等记录和业务 mutation 在 `BEGIN IMMEDIATE` 事务内完成。
- **Verifier 能修复什么？** 它可以阻止工具失败后的虚假成功，并允许一次 transient 修复；不会绕过权限错误。

### 第三层：生产化与反例

- **SQLite 够生产吗？** 不够多机高并发；生产要用 PostgreSQL checkpoint/业务库、唯一约束、事务 outbox、监控和审计。
- **为什么 SFT 变差？** V1 adapter 学的是单轮 Tool Calling，与多角色 JSON 协议不匹配；Graph V2 虽改善拆解，但单节点样本不能充分教会跨节点状态转移。
- **如何继续改？** 按失败类型构造完整 trace，分开 supervisor/expert/verifier 评测，增加 approval 和否定样本，但仍以冻结任务成功率和危险执行为 checkpoint 选择标准。
