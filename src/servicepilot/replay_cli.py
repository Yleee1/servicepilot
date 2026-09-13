from __future__ import annotations

import argparse
import json
from pathlib import Path

from .evaluation import evaluate, load_cases
from .schemas import AgentDecision, ToolCall


class ReplayCaller:
    def __init__(self, predictions: list[dict]) -> None:
        self.predictions = iter(predictions)

    def decide(self, text, context) -> AgentDecision:
        prediction = next(self.predictions)
        call = None
        if prediction.get("action") == "call":
            call = ToolCall(prediction["tool"], prediction["arguments"])
        if prediction.get("action") == "error":
            raise ValueError("replayed caller error")
        return AgentDecision(prediction["action"], call, prediction.get("message", ""))


def main() -> None:
    parser = argparse.ArgumentParser(description="Re-score saved decisions after evaluator/policy changes")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--cases", default="data/eval/tool_call_seed_v1.jsonl")
    args = parser.parse_args()

    previous = json.loads(Path(args.input).read_text(encoding="utf-8"))
    predictions = [case["predicted"] for case in previous["cases"]]
    report = evaluate(ReplayCaller(predictions), load_cases(args.cases))
    if "usage" in previous:
        report["usage"] = previous["usage"]
    report["replayed_from"] = args.input
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["metrics"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
