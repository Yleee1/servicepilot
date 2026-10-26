#!/usr/bin/env bash
set -euo pipefail

./.venv/bin/python -m servicepilot.narrow_sft_v3
for task in router argument memory_classifier conflict_classifier; do
  ./.venv/bin/mlx_lm.lora -c "configs/qwen3_1.7b_${task}_qlora_v3.yaml"
done
./.venv/bin/python -m servicepilot.narrow_eval_v3
