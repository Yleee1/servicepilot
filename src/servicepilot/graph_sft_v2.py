from __future__ import annotations

import argparse
import hashlib
import json
import random
from collections import Counter
from pathlib import Path

from .agents.supervisor import SUPERVISOR_SYSTEM_PROMPT
from .agents.verifier_agent import VERIFIER_SYSTEM_PROMPT
from .model_caller import build_system_prompt


def _conversation(system: str, user: dict, assistant: dict, *, role: str, group: str) -> dict:
    return {
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
            {"role": "assistant", "content": json.dumps(assistant, ensure_ascii=False)},
        ],
        "metadata": {"role": role, "group": group},
    }


def _supervisor_record(index: int, kind: str) -> dict:
    order_id = f"O{1100 + index}"
    sku = f"SKU-TRAIN-{index:03d}"
    if kind == "order":
        request = f"请查询我的订单 {order_id} 当前状态"
        tasks = [{"task_id": "t1", "agent": "order", "instruction": request, "depends_on": []}]
    elif kind == "tracking":
        request = f"我想知道 {order_id} 的快递进度"
        tasks = [{"task_id": "t1", "agent": "order", "instruction": f"查询物流：{request}", "depends_on": []}]
    elif kind == "inventory":
        request = f"请查询 {sku} 的可售库存"
        tasks = [{"task_id": "t1", "agent": "inventory", "instruction": request, "depends_on": []}]
    elif kind == "compound":
        request = f"查订单 {order_id}，同时查 {sku} 库存"
        tasks = [
            {"task_id": "t1", "agent": "order", "instruction": request, "depends_on": []},
            {"task_id": "t2", "agent": "inventory", "instruction": request, "depends_on": []},
        ]
    elif kind == "refund":
        request = f"订单 {order_id} 的商品损坏，请帮我申请退款"
        tasks = [
            {"task_id": "t1", "agent": "order", "instruction": f"查询订单状态：{request}", "depends_on": []},
            {"task_id": "t2", "agent": "refund", "instruction": request, "depends_on": ["t1"]},
        ]
    else:
        request = f"请帮我写一首第 {index} 号小诗"
        tasks = []
    user = {
        "request": request,
        "output_schema": {
            "tasks": [
                {"task_id": "t1", "agent": "order", "instruction": "...", "depends_on": []}
            ]
        },
    }
    return _conversation(
        SUPERVISOR_SYSTEM_PROMPT,
        user,
        {"tasks": tasks},
        role="supervisor",
        group=f"supervisor-{index}",
    )


def _expert_record(index: int, role: str, mode: str) -> dict:
    order_id = f"O{2100 + index}"
    sku = f"SKU-EXPERT-{index:03d}"
    boundaries = {
        "order": (("get_order", "track_package"), "只能处理当前订单子任务"),
        "inventory": (("check_inventory",), "只能处理当前库存子任务"),
        "refund": (
            ("get_refund_eligibility", "propose_refund"),
            "只能检查资格或提出退款提案，永远不能执行 create_refund",
        ),
    }
    tools, policy = boundaries[role]
    if role == "order" and mode == "read":
        message = f"查订单 {order_id}"
        output = {"action": "call", "tool": "get_order", "arguments": {"order_id": order_id}, "message": ""}
        confirmed = False
    elif role == "order":
        message = f"查 {order_id} 物流"
        output = {"action": "call", "tool": "track_package", "arguments": {"order_id": order_id}, "message": ""}
        confirmed = False
    elif role == "inventory" and mode == "read":
        message = f"查库存 {sku}"
        output = {"action": "call", "tool": "check_inventory", "arguments": {"sku_id": sku}, "message": ""}
        confirmed = False
    elif role == "inventory":
        message = f"请查第 {index} 个商品的库存，但我还没有 SKU"
        output = {"action": "clarify", "message": "请提供需要查询的 SKU 编号。"}
        confirmed = False
    elif mode == "eligibility":
        message = f"查询 {order_id} 的退款资格"
        output = {"action": "call", "tool": "get_refund_eligibility", "arguments": {"order_id": order_id}, "message": ""}
        confirmed = False
    else:
        message = f"订单 {order_id} 商品损坏，提出退款方案"
        output = {"action": "call", "tool": "propose_refund", "arguments": {"order_id": order_id, "reason": "damaged"}, "message": ""}
        confirmed = True
    return _conversation(
        build_system_prompt(role, tools, policy),
        {"message": message, "trusted_context": {"confirmed": confirmed}},
        output,
        role=role,
        group=f"{role}-{index}",
    )


def _verifier_record(index: int, passed: bool) -> dict:
    task_id = f"t{index % 3 + 1}"
    tasks = [{"task_id": task_id, "agent": "order", "instruction": "查订单", "depends_on": []}]
    observations = [
        {
            "task_id": task_id,
            "agent": "order",
            "tool": "get_order",
            "ok": passed,
            "code": "OK" if passed else "TIMEOUT",
            "data": {"status": "shipped"} if passed else {},
        }
    ]
    user = {
        "request": f"查询训练订单 O{3100 + index} 状态",
        "tasks": tasks,
        "observations": observations,
        "draft": "已查到订单" if passed else "已查询成功",
    }
    output = {
        "passed": passed,
        "missing_tasks": [] if passed else [task_id],
        "unsupported_claims": [] if passed else ["tool failed but draft claimed success"],
        "next_action": "pass" if passed else "retry",
    }
    return _conversation(
        VERIFIER_SYSTEM_PROMPT,
        user,
        output,
        role="verifier",
        group=f"verifier-{index}",
    )


def build_records() -> list[dict]:
    records = []
    kinds = ("order", "tracking", "inventory", "compound", "refund", "out_of_scope")
    for index in range(120):
        records.append(_supervisor_record(index, kinds[index % len(kinds)]))
    for index in range(60):
        records.append(_expert_record(index, "order", "read" if index % 2 == 0 else "tracking"))
    for index in range(40):
        records.append(_expert_record(index, "inventory", "read" if index % 2 == 0 else "clarify"))
    for index in range(60):
        records.append(_expert_record(index, "refund", "eligibility" if index % 2 == 0 else "proposal"))
    for index in range(60):
        records.append(_verifier_record(index, passed=index % 2 == 0))
    return records


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate multi-role graph SFT V2 data")
    parser.add_argument("--output-dir", default="data/sft/graph_v2")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--eval-cases", default="data/eval/graph_frozen_v2.jsonl")
    args = parser.parse_args()
    records = build_records()
    random.Random(args.seed).shuffle(records)
    boundaries = (int(len(records) * 0.75), int(len(records) * 0.875))
    splits = {
        "train": records[: boundaries[0]],
        "valid": records[boundaries[0] : boundaries[1]],
        "test": records[boundaries[1] :],
    }
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    hashes = {}
    split_inputs = {}
    for name, rows in splits.items():
        rendered = "".join(
            json.dumps({"messages": row["messages"]}, ensure_ascii=False) + "\n" for row in rows
        )
        path = output / f"{name}.jsonl"
        path.write_text(rendered, encoding="utf-8")
        hashes[name] = {
            "count": len(rows),
            "sha256": hashlib.sha256(rendered.encode("utf-8")).hexdigest(),
        }
        split_inputs[name] = {row["messages"][1]["content"] for row in rows}
    split_intersections = {
        "train_valid": len(split_inputs["train"] & split_inputs["valid"]),
        "train_test": len(split_inputs["train"] & split_inputs["test"]),
        "valid_test": len(split_inputs["valid"] & split_inputs["test"]),
    }
    training_inputs = set()
    for row in records:
        payload = json.loads(row["messages"][1]["content"])
        for field in ("request", "message"):
            if isinstance(payload.get(field), str):
                training_inputs.add(payload[field])
    eval_messages = set()
    eval_path = Path(args.eval_cases)
    if eval_path.exists():
        for line in eval_path.read_text(encoding="utf-8").splitlines():
            if line:
                eval_messages.update(json.loads(line)["messages"])
    overlap = sorted(training_inputs & eval_messages)
    manifest = {
        "seed": args.seed,
        "total": len(records),
        "splits": hashes,
        "role_distribution": dict(sorted(Counter(row["metadata"]["role"] for row in records).items())),
        "split_input_intersections": split_intersections,
        "frozen_eval_message_overlap": len(overlap),
        "overlap_examples": overlap[:5],
        "note": "Synthetic training identifiers O1100+/O2100+ are disjoint from V2 eval identifiers.",
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
