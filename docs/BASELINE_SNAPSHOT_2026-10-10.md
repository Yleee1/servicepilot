# ServicePilot 基线保护快照（2026-10-10）

- Python 3.14.7
- servicepilot 0.1.0
- LangGraph 1.2.14
- langgraph-checkpoint-sqlite 3.1.1
- pytest 9.1.1
- V2 改造后全量测试：`42 passed`

V1 保护对象 SHA-256：

```text
401871d6fd6e8fa1d055538e5bdc0d00aa5e0ccf33e98d2518a581b7c6366368  data/eval/frozen_v1.jsonl
8bb8ff2d6abce8c7ff534e84b79702ac374c6945d95110bc20cd7a38ff5c180b  results/frozen_summary_v1.json
```

V2 开发集与结果 SHA-256：

```text
b34854cac7b7a5cf2208b3293ded9409eb74715077799aa60faf27877c14779d  data/eval/graph_dev_v2.jsonl
aa672140440cb47380000dc9de72e76bd0c7949b378b3476091fec1e127830c9  results/graph_rule_dev_v2.json
```

验证命令：

```bash
.venv/bin/pip install -e '.[dev]'
.venv/bin/python -m pytest -q
.venv/bin/python -m servicepilot.graph_eval_cli
```
