from __future__ import annotations

import argparse
import hashlib
import json
import random
from pathlib import Path

from .model_caller import SYSTEM_PROMPT


ORDER_IDS = [f"O{number}" for number in range(1101, 1191)]
SKUS = ["SKU-RED-42", "SKU-BLUE-40", "SKU-GREEN-38", "SKU-BLACK-XL"]
REASONS = {
    "not_received": ["一直没收到", "包裹没有送到", "not received"],
    "damaged": ["商品坏了", "收到时破损", "item is damaged"],
    "wrong_item": ["商家发错货了", "收到的不是我买的", "wrong item"],
    "changed_mind": ["我不想要了", "改变主意了", "changed my mind"],
}


TEMPLATES = {
    "train": {
        "order": ["查一下订单 {order}", "{order} 现在是什么状态", "帮我看看 order {order}"],
        "track": ["订单 {order} 的物流到哪了", "查快递 {order}", "track package {order}"],
        "inventory": ["{sku} 还有库存吗", "查一下 {sku} 是否有货", "inventory for {sku}"],
        "eligible": ["{order} 能不能退", "查一下 {order} 的退款资格", "is {order} eligible for refund"],
        "refund": ["确认给 {order} 退款，{reason}", "{order} {reason}，我确认退款"],
        "unconfirmed": ["{order} {reason}，帮我退款", "我要退掉 {order}，因为{reason}"],
        "missing": ["帮我查订单", "快递到哪了", "我想退款", "这个有库存吗"],
        "chat": ["你好", "谢谢", "你能做什么"],
    },
    "valid": {
        "order": ["我想知道 {order} 的订单状态"],
        "track": ["{order} 的包裹走到哪里了"],
        "inventory": ["麻烦确认 {sku} 有货没"],
        "eligible": ["订单 {order} 是否符合退款条件"],
        "refund": ["{reason}，确定申请订单 {order} 的退款"],
        "unconfirmed": ["{reason}，替我退一下 {order}"],
        "missing": ["查下我的订单详情", "帮忙看看包裹", "能申请退钱吗"],
        "chat": ["您好", "多谢帮助", "你们周末营业吗"],
    },
    "test": {
        "order": ["帮忙调出 {order} 的详情"],
        "track": ["我的 {order} 什么时候送到"],
        "inventory": ["{sku} 现在能下单吗"],
        "eligible": ["我想确认 {order} 可不可以申请退钱"],
        "refund": ["针对 {order}，{reason}，同意立即发起退款"],
        "unconfirmed": ["订单 {order} 因为{reason}需要退货"],
        "missing": ["查看一下购买记录", "货送哪儿了", "这款还能买吗"],
        "chat": ["hello", "拜拜", "客服在吗"],
    },
}


def assistant(action: str, tool: str | None = None, arguments: dict | None = None, message: str = "") -> str:
    value: dict = {"action": action}
    if tool is not None:
        value.update({"tool": tool, "arguments": arguments or {}, "message": message})
    else:
        value["message"] = message
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def example(text: str, confirmed: bool, target: str) -> dict:
    user = json.dumps(
        {"message": text, "trusted_context": {"confirmed": confirmed}},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return {
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user},
            {"role": "assistant", "content": target},
        ]
    }


def build_split(split: str, count: int, seed: int) -> list[dict]:
    rng = random.Random(seed)
    templates = TEMPLATES[split]
    rows = []
    intents = ["order", "track", "inventory", "eligible", "refund", "unconfirmed", "missing", "chat"]
    for index in range(count):
        intent = intents[index % len(intents)]
        order = rng.choice(ORDER_IDS)
        sku = rng.choice(SKUS)
        if intent == "order":
            text = rng.choice(templates["order"]).format(order=order)
            target = assistant("call", "get_order", {"order_id": order})
            rows.append(example(text, False, target))
        elif intent == "track":
            text = rng.choice(templates["track"]).format(order=order)
            target = assistant("call", "track_package", {"order_id": order})
            rows.append(example(text, False, target))
        elif intent == "inventory":
            text = rng.choice(templates["inventory"]).format(sku=sku)
            target = assistant("call", "check_inventory", {"sku_id": sku})
            rows.append(example(text, False, target))
        elif intent == "eligible":
            text = rng.choice(templates["eligible"]).format(order=order)
            target = assistant("call", "get_refund_eligibility", {"order_id": order})
            rows.append(example(text, False, target))
        elif intent == "refund":
            reason_code = rng.choice(list(REASONS))
            phrase = rng.choice(REASONS[reason_code])
            text = rng.choice(templates["refund"]).format(order=order, reason=phrase)
            target = assistant("call", "create_refund", {"order_id": order, "reason": reason_code})
            rows.append(example(text, True, target))
        elif intent == "unconfirmed":
            phrase = rng.choice(REASONS["damaged"])
            text = rng.choice(templates["unconfirmed"]).format(order=order, reason=phrase)
            rows.append(example(text, False, assistant("clarify", message="该操作会创建退款申请，请明确确认。")))
        elif intent == "missing":
            request = rng.choice(templates["missing"])
            rows.append(example(request, False, assistant("clarify", message="请提供完成操作所需的信息。")))
        else:
            text = rng.choice(templates["chat"])
            rows.append(example(text, False, assistant("respond", message="我可以协助订单、物流、库存和退款问题。")))
    rng.shuffle(rows)
    return rows


def write_jsonl(path: Path, rows: list[dict]) -> str:
    content = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
    path.write_text(content, encoding="utf-8")
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate deterministic, template-isolated MLX SFT data")
    parser.add_argument("--output-dir", default="data/sft/v1")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    if any((output / name).exists() for name in ("train.jsonl", "valid.jsonl", "test.jsonl")) and not args.force:
        raise FileExistsError(f"{output} already contains a split; pass --force to regenerate")

    specs = {"train": (240, 20260913), "valid": (48, 20260914), "test": (48, 20260915)}
    manifest = {"version": "v1", "split_strategy": "disjoint paraphrase template families", "splits": {}}
    for split, (count, seed) in specs.items():
        rows = build_split(split, count, seed)
        digest = write_jsonl(output / f"{split}.jsonl", rows)
        manifest["splits"][split] = {"count": len(rows), "seed": seed, "sha256": digest}
    (output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
