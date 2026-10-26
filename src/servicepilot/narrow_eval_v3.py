from __future__ import annotations

import argparse
import json
from pathlib import Path
from time import perf_counter

from .mlx_client import MLXJSONClient

TASKS = ("router", "argument", "memory_classifier", "conflict_classifier")


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _evaluate(client: MLXJSONClient, rows: list[dict]) -> dict:
    records = []
    started = perf_counter()
    for index, row in enumerate(rows):
        messages = row["messages"]
        expected = json.loads(messages[2]["content"])
        try:
            actual = client.chat_json(messages[0]["content"], messages[1]["content"])
            error = None
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            actual = None
            error = f"{type(exc).__name__}: {exc}"
        records.append({
            "case_index": index,
            "passed": actual == expected,
            "expected": expected,
            "actual": actual,
            "error": error,
        })
    elapsed = perf_counter() - started
    passed = sum(record["passed"] for record in records)
    return {
        "case_count": len(records),
        "exact_match": passed / len(records),
        "json_valid_rate": sum(record["actual"] is not None for record in records) / len(records),
        "elapsed_seconds": elapsed,
        "usage": client.usage,
        "records": records,
    }


def run(data_dir: Path, model: str, adapter_root: Path) -> dict:
    result = {
        "suite": "narrow_adapter_v3_test",
        "scope": "independent synthetic test split; exact JSON match, not end-to-end scenario success",
        "model": model,
        "tasks": {},
    }
    for task in TASKS:
        rows = _rows(data_dir / task / "test.jsonl")
        base = _evaluate(MLXJSONClient(model, max_tokens=96), rows)
        adapter_path = adapter_root / f"qwen3_1.7b_servicepilot_{task}_v3"
        adapted = _evaluate(
            MLXJSONClient(model, adapter_path=str(adapter_path), max_tokens=96), rows
        )
        result["tasks"][task] = {
            "base": base,
            "adapter": adapted,
            "exact_match_delta": adapted["exact_match"] - base["exact_match"],
        }
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate narrow adapters on isolated test splits")
    parser.add_argument("--data-dir", default="data/sft/narrow_v3")
    parser.add_argument("--model", default="models/Qwen3-1.7B-4bit")
    parser.add_argument("--adapter-root", default="adapters")
    parser.add_argument("--output", default="results/narrow_adapter_eval_v3.json")
    args = parser.parse_args()
    result = run(Path(args.data_dir), args.model, Path(args.adapter_root))
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    summary = {
        task: {
            "base": values["base"]["exact_match"],
            "adapter": values["adapter"]["exact_match"],
            "delta": values["exact_match_delta"],
        }
        for task, values in result["tasks"].items()
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
