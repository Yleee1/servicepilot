from __future__ import annotations

import argparse
import json
from pathlib import Path

from .baseline import RuleBasedToolCaller
from .evaluation import evaluate, load_cases
from .llm_client import OpenAICompatibleJSONClient
from .mlx_client import MLXJSONClient
from .model_caller import ModelToolCaller


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate a ServicePilot tool caller")
    parser.add_argument("--cases", default="data/eval/tool_call_seed_v1.jsonl")
    parser.add_argument("--output", default="results/rule_baseline_v1.json")
    parser.add_argument("--caller", choices=("rule", "deepseek", "mlx"), default="rule")
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--model", default="models/Qwen3-1.7B-4bit")
    parser.add_argument("--adapter-path")
    parser.add_argument("--adapter-weights", help="Optional checkpoint weights within an adapter run")
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()

    cases = load_cases(args.cases)
    if args.limit is not None:
        cases = cases[: args.limit]
    client = None
    if args.caller == "deepseek":
        client = OpenAICompatibleJSONClient.from_env(args.env_file)
        caller = ModelToolCaller(client)
    elif args.caller == "mlx":
        client = MLXJSONClient(args.model, args.adapter_path, args.adapter_weights)
        caller = ModelToolCaller(client)
    else:
        caller = RuleBasedToolCaller()
    report = evaluate(caller, cases)
    if client is not None:
        report["usage"] = client.usage
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["metrics"], ensure_ascii=False, indent=2))
    if client is not None:
        print(json.dumps({"usage": client.usage}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
