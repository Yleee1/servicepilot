from __future__ import annotations

import argparse
import json
from pathlib import Path

from .baseline import RuleBasedToolCaller
from .llm_client import OpenAICompatibleJSONClient
from .mlx_client import MLXJSONClient
from .model_caller import ModelToolCaller
from .session import ProposalCaller
from .session_evaluation import evaluate_sessions, load_scenarios


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate multi-turn ServicePilot sessions")
    parser.add_argument("--caller", choices=("rule", "deepseek", "mlx"), default="rule")
    parser.add_argument("--cases", default="data/eval/session_seed_v1.jsonl")
    parser.add_argument("--output", default="results/session_rule_v1.json")
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--model", default="models/Qwen3-1.7B-4bit")
    parser.add_argument("--adapter-path")
    parser.add_argument("--adapter-weights")
    args = parser.parse_args()

    client = None
    if args.caller == "deepseek":
        client = OpenAICompatibleJSONClient.from_env(args.env_file)
        base_caller = ModelToolCaller(client)
    elif args.caller == "mlx":
        client = MLXJSONClient(args.model, args.adapter_path, args.adapter_weights)
        base_caller = ModelToolCaller(client)
    else:
        base_caller = RuleBasedToolCaller()
    report = evaluate_sessions(ProposalCaller(base_caller), load_scenarios(args.cases))
    if client is not None:
        report["usage"] = client.usage
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["metrics"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
