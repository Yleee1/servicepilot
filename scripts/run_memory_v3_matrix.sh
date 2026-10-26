#!/usr/bin/env bash
set -euo pipefail

split="${1:-dev}"
if [[ "$split" != "dev" && "$split" != "heldout" ]]; then
  echo "usage: $0 [dev|heldout]" >&2
  echo "frozen is intentionally excluded from repeatable matrix runs" >&2
  exit 2
fi

./.venv/bin/python -m servicepilot.memory_ablation_v3 --split "$split"
