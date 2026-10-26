# ServicePilot Frozen Eval V2

## 结论

V2 已按 61 条场景冻结并运行 A0—A4。集合 SHA-256 为 `b4e1b1924470b7c9e9b8fc31130833876fdadd00a5a01adb490b22873c196a36`。该集是合成业务回归集，已通过 schema 和可执行期望校验，并已完成第二审阅者独立复核（由项目所有者确认，不在仓库保存复核者身份或私人笔记）。

分布：20 条复合/复合退款，20 条订单、物流、库存与基本退款，21 条确认、重启、过期、越权、否定指令、注入和故障场景。

## 确定性编排消融

| 组别 | 系统 | Success | Route F1 | Decomposition | Compound | Restart | Cross-user block | Unsafe executed |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| A0 | V1 `ServiceSession` | 26.23% | 0.00% | 55.46% | 0.00% | 0.00% | 0.00% | 0 |
| A1 | 单 generalist + LangGraph | 26.23% | 0.00% | 57.10% | 0.00% | 0.00% | 0.00% | 0 |
| A2 | Supervisor + 最小权限 experts | 88.52% | 100.00% | 99.45% | 97.06% | 0.00% | 0.00% | 0 |
| A3 | A2 + Verifier | 98.36% | 100.00% | 99.45% | 97.06% | 0.00% | 100.00% | 0 |
| A4 | A3 + SQLite HITL/业务库 | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 100.00% | 0 |

解读：A2 证明明确角色与依赖拆解是主要增益；A3 主要补上跨用户与工具失败检查；A4 通过持久化 checkpoint、业务状态与幂等键闭合重启场景。A4 的 100% 是确定性编排上限，不是 LLM 泛化得分。

## 本地小模型盲测

所有行使用同一 61 条冻结集和 A4 图，均为本机 MLX 真实运行。

| Caller | Success | Route Acc./F1 | Decomposition | Compound | Mean/P95 latency | Calls/Tokens |
|---|---:|---:|---:|---:|---:|---:|
| Qwen3-1.7B Base | 29.51% | 47.54% / 54.91% | 43.17% | 8.82% | 1.93 / 2.92 s | 183 / 49,385 |
| V1 safe adapter | 14.75% | 27.87% / 31.90% | 29.51% | 2.94% | 2.44 / 4.60 s | 190 / 47,361 |
| V1 aggressive adapter | 9.84% | 29.51% / 33.36% | 24.59% | 2.94% | 2.14 / 3.67 s | 176 / 43,427 |
| Graph V2 adapter | 26.23% | 45.90% / 56.78% | 51.37% | 23.53% | 5.54 / 9.71 s | 201 / 58,077 |

四组实际危险执行均为 0，受保护业务状态保全率均为 100%。Graph V2 adapter 用 340 条多角色合成记录训练，255/42/43 划分的输入交集为 0，与冻结场景消息的精确重合为 0。它改善了复合任务，但过度生成额外任务、参数混淆和无法触发 approval，导致总成功率仍低于 Base。不用训练 loss 替代这个冻结结果。

## Bad Case 与故障注入

`results/bad_cases_frozen_v2.json` 保留 12 个真实消融失败，覆盖 A0/A1 复合任务丢失、A2 缺 Verifier、A3 无跨进程恢复。V2 还自动化了：

- 独立 Python 进程发起与确认，验证 pending action 恢复；
- transient 工具故障注入与有界重试；
- 两个独立 SQLite 连接并发确认，验证唯一幂等键；
- handoff 上限、stale TTL、参数绑定、跨用户与重复 mutation。

## 复现

```bash
./.venv/bin/python -m servicepilot.graph_data_v2
./.venv/bin/python -m servicepilot.graph_eval_cli \
  --cases data/eval/graph_frozen_v2.jsonl --variant all --caller rule \
  --output results/graph_rule_ablation_frozen_v2.json
./.venv/bin/python -m servicepilot.graph_sft_v2
./.venv/bin/mlx_lm.lora -c configs/qwen3_1.7b_graph_qlora_v2.yaml
./.venv/bin/python -m servicepilot.graph_eval_cli \
  --cases data/eval/graph_frozen_v2.jsonl --variant A4 --caller mlx \
  --model models/Qwen3-1.7B-4bit \
  --adapter-path adapters/qwen3_1.7b_servicepilot_graph_v2 \
  --output results/graph_qwen_graph_v2_a4_frozen_v2.json
```

原始文件：`results/graph_rule_ablation_frozen_v2.json`、`results/graph_qwen_*_frozen_v2.json`、`logs/qwen3_1.7b_graph_v2_summary.json`。
