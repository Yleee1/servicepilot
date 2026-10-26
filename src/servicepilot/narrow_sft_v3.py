from __future__ import annotations

import argparse
import hashlib
import json
import random
from collections import Counter
from pathlib import Path

TASK_PROMPTS = {
    "router": (
        "Classify the customer request. Return JSON only with keys intent and expert. "
        "intent is order_status, tracking, inventory, refund, compound, or out_of_scope; "
        "expert is a list containing only order, inventory, and refund."
    ),
    "argument": (
        "Extract a schema-valid tool call. Return JSON only with action, tool, and arguments. "
        "If a required identifier is absent, return action=clarify, tool=null, arguments={}."
    ),
    "memory_classifier": (
        "Classify a memory write candidate. Return JSON only with write, memory_type, and "
        "scope_type. Never write operational state, credentials, or unverified inference."
    ),
    "conflict_classifier": (
        "Compare an existing memory and a candidate. Return JSON only with decision. Allowed "
        "decisions: new, duplicate, temporal_update, scope_override, true_conflict, "
        "low_authority_conflict."
    ),
}


def _conversation(task: str, payload: dict, answer: dict, group: str) -> dict:
    return {
        "messages": [
            {"role": "system", "content": TASK_PROMPTS[task]},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            {"role": "assistant", "content": json.dumps(answer, ensure_ascii=False)},
        ],
        "metadata": {"task": task, "group": group},
    }


def _router(index: int) -> dict:
    order_id = f"R{5000 + index}"
    sku = f"NARROW-{index:03d}"
    variants = (
        (f"查一下订单 {order_id}", "order_status", ["order"]),
        (f"订单 {order_id} 物流到哪了", "tracking", ["order"]),
        (f"{sku} 还有库存吗", "inventory", ["inventory"]),
        (f"订单 {order_id} 商品损坏，申请退款", "refund", ["order", "refund"]),
        (f"查订单 {order_id}，同时查 {sku} 库存", "compound", ["order", "inventory"]),
        (f"帮我写第 {index} 首诗", "out_of_scope", []),
    )
    text, intent, expert = variants[index % len(variants)]
    return _conversation("router", {"request": text}, {"intent": intent, "expert": expert}, f"router-{index}")


def _argument(index: int) -> dict:
    order_id = f"A{6000 + index}"
    sku = f"ARG-{index:03d}"
    variants = (
        (f"查订单 {order_id}", {"action": "call", "tool": "get_order", "arguments": {"order_id": order_id}}),
        (f"查 {order_id} 物流", {"action": "call", "tool": "track_package", "arguments": {"order_id": order_id}}),
        (f"查库存 {sku}", {"action": "call", "tool": "check_inventory", "arguments": {"sku_id": sku}}),
        (f"查一下我的第 {index} 个订单，但我没提供订单号", {"action": "clarify", "tool": None, "arguments": {}}),
        (f"查一下第 {index} 个商品库存，但我没提供 SKU", {"action": "clarify", "tool": None, "arguments": {}}),
        (f"查询 {order_id} 退款资格", {"action": "call", "tool": "get_refund_eligibility", "arguments": {"order_id": order_id}}),
    )
    text, answer = variants[index % len(variants)]
    return _conversation("argument", {"request": text}, answer, f"argument-{index}")


def _memory_classifier(index: int) -> dict:
    variants = (
        ("请记住以后用中文回复", "user_statement", {"write": True, "memory_type": "semantic", "scope_type": "user"}),
        ("项目 P1 请用 Slack 通知", "user_statement", {"write": True, "memory_type": "semantic", "scope_type": "project"}),
        ("本次退款已经执行成功", "tool_result", {"write": False, "memory_type": None, "scope_type": None}),
        ("模型猜测用户喜欢英文", "agent_inference", {"write": False, "memory_type": None, "scope_type": None}),
        ("已验证：先查资格再提出退款方案", "verifier", {"write": True, "memory_type": "procedural", "scope_type": "organization"}),
        ("API key 是 sk-secret", "user_statement", {"write": False, "memory_type": None, "scope_type": None}),
    )
    content, source, answer = variants[index % len(variants)]
    return _conversation(
        "memory_classifier",
        {"content": f"{content}（样本{index}）", "source_type": source},
        answer,
        f"memory-{index}",
    )


def _conflict_classifier(index: int) -> dict:
    variants = (
        ("请用中文回复", "中文回复", "user", "user", "user_statement", "duplicate"),
        ("请用中文回复", "请改用英文回复", "user", "user", "user_statement", "temporal_update"),
        ("邮件通知", "项目 P1 用 Slack 通知", "user", "project", "user_statement", "scope_override"),
        ("请用中文回复", "请用英文回复", "user", "user", "user_statement", "true_conflict"),
        ("请用中文回复", "模型推断用户喜欢英文", "user", "user", "agent_inference", "low_authority_conflict"),
        ("请用中文回复", "回答尽量简洁", "user", "user", "user_statement", "new"),
    )
    old, new, old_scope, new_scope, source, decision = variants[index % len(variants)]
    return _conversation(
        "conflict_classifier",
        {
            "existing": {"content": old, "scope_type": old_scope, "source_type": "user_statement"},
            "candidate": {
                "content": f"{new}（样本{index}）",
                "scope_type": new_scope,
                "source_type": source,
                "explicit_update": decision == "temporal_update",
            },
        },
        {"decision": decision},
        f"conflict-{index}",
    )


BUILDERS = {
    "router": _router,
    "argument": _argument,
    "memory_classifier": _memory_classifier,
    "conflict_classifier": _conflict_classifier,
}


def generate(output_dir: Path, seed: int = 73, records_per_task: int = 96) -> dict:
    manifest = {"seed": seed, "records_per_task": records_per_task, "tasks": {}}
    for task, builder in BUILDERS.items():
        rows = [builder(index) for index in range(records_per_task)]
        random.Random(f"{seed}:{task}").shuffle(rows)
        boundaries = (int(len(rows) * 0.75), int(len(rows) * 0.875))
        splits = {
            "train": rows[: boundaries[0]],
            "valid": rows[boundaries[0] : boundaries[1]],
            "test": rows[boundaries[1] :],
        }
        task_dir = output_dir / task
        task_dir.mkdir(parents=True, exist_ok=True)
        split_meta = {}
        inputs = {}
        for split, split_rows in splits.items():
            rendered = "".join(
                json.dumps({"messages": row["messages"]}, ensure_ascii=False) + "\n"
                for row in split_rows
            )
            (task_dir / f"{split}.jsonl").write_text(rendered, encoding="utf-8")
            split_meta[split] = {
                "count": len(split_rows),
                "sha256": hashlib.sha256(rendered.encode()).hexdigest(),
            }
            inputs[split] = {row["messages"][1]["content"] for row in split_rows}
        intersections = {
            "train_valid": len(inputs["train"] & inputs["valid"]),
            "train_test": len(inputs["train"] & inputs["test"]),
            "valid_test": len(inputs["valid"] & inputs["test"]),
        }
        manifest["tasks"][task] = {
            "splits": split_meta,
            "split_input_intersections": intersections,
            "label_distribution": dict(
                sorted(Counter(row["messages"][2]["content"] for row in rows).items())
            ),
        }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate four narrow-responsibility SFT datasets")
    parser.add_argument("--output-dir", default="data/sft/narrow_v3")
    parser.add_argument("--seed", type=int, default=73)
    parser.add_argument("--records-per-task", type=int, default=96)
    args = parser.parse_args()
    print(json.dumps(generate(Path(args.output_dir), args.seed, args.records_per_task), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
