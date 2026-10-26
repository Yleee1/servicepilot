from __future__ import annotations

import argparse
import json
from pathlib import Path


def classify(variant: str, errors: list[str], category: str) -> tuple[str, str]:
    rendered = " ".join(errors)
    if variant in {"A0", "A1"} and "calls:" in rendered:
        return "single_agent_decomposition_gap", "Use Supervisor task decomposition and least-privilege experts."
    if "restart" in category or "expected status ['executed'], got clarify" in rendered:
        return "volatile_pending_state", "Persist checkpoint, pending action, business state and idempotency key."
    if variant == "A2" and category in {"cross_user", "unknown_order", "unknown_sku"}:
        return "missing_verifier_escalation", "Use the Verifier to convert failed observations into explicit escalation."
    if "order" in rendered:
        return "protected_state_or_execution_gap", "Keep deterministic policy checks and durable transactions."
    return "orchestration_gap", "Inspect the trace and add a bounded deterministic recovery path."


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract ServicePilot ablation bad cases")
    parser.add_argument("--results", default="results/graph_rule_ablation_frozen_v2.json")
    parser.add_argument("--output", default="results/bad_cases_frozen_v2.json")
    parser.add_argument("--limit", type=int, default=12)
    args = parser.parse_args()
    report = json.loads(Path(args.results).read_text(encoding="utf-8"))
    selected = []
    for variant in ("A3", "A2", "A1", "A0"):
        for item in report["details"][variant]["scenarios"]:
            if item["passed"]:
                continue
            root_cause, recommendation = classify(
                variant, item["errors"], item.get("category", "")
            )
            selected.append(
                {
                    "case_id": item["case_id"],
                    "variant": variant,
                    "category": item.get("category", ""),
                    "errors": item["errors"],
                    "root_cause": root_cause,
                    "recommendation": recommendation,
                    "fixed_in_a4": bool(
                        next(
                            row["passed"]
                            for row in report["details"]["A4"]["scenarios"]
                            if row["case_id"] == item["case_id"]
                        )
                    ),
                }
            )
            if len(selected) >= args.limit:
                break
        if len(selected) >= args.limit:
            break
    payload = {
        "source": args.results,
        "selection": "higher ablations first so each case isolates a smaller missing capability",
        "bad_case_count": len(selected),
        "cases": selected,
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"bad_case_count": len(selected)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
