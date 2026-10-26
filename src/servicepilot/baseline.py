from __future__ import annotations

import re

from .schemas import AgentDecision, ExecutionContext, ToolCall

ORDER_RE = re.compile(r"\bO\d+\b", re.IGNORECASE)
SKU_RE = re.compile(r"\bSKU[-_A-Z0-9]+\b", re.IGNORECASE)


class RuleBasedToolCaller:
    """可解释的非模型基线，用来证明评测链路有效并提供最低对照组。"""

    def decide(self, text: str, context: ExecutionContext) -> AgentDecision:
        normalized = text.strip()
        lowered = normalized.lower()
        order_id = self._extract(ORDER_RE, normalized)
        sku_id = self._extract(SKU_RE, normalized)

        if any(word in lowered for word in ("你好", "谢谢", "再见", "hello", "hi")):
            return AgentDecision("respond", message="您好，请问有什么可以帮您？")

        if any(word in lowered for word in ("库存", "有货", "stock", "inventory")):
            if sku_id is None:
                return AgentDecision("clarify", message="请提供需要查询的 SKU 编号。")
            return self._call("check_inventory", sku_id=sku_id)

        if any(word in lowered for word in ("物流", "快递", "到哪", "tracking", "package")):
            if order_id is None:
                return AgentDecision("clarify", message="请提供需要查询的订单号。")
            return self._call("track_package", order_id=order_id)

        eligibility_words = ("能不能退", "可以退", "是否能退", "退款资格", "eligible")
        if any(word in lowered for word in eligibility_words):
            if order_id is None:
                return AgentDecision("clarify", message="请提供需要检查的订单号。")
            return self._call("get_refund_eligibility", order_id=order_id)

        if any(word in lowered for word in ("退款", "退货", "refund")):
            if order_id is None:
                return AgentDecision("clarify", message="请提供需要退款的订单号。")
            reason = self._refund_reason(lowered)
            if reason is None:
                return AgentDecision("clarify", message="请说明退款原因。")
            if not context.confirmed:
                return AgentDecision("clarify", message="该操作会创建退款申请，请明确确认。")
            return self._call("create_refund", order_id=order_id, reason=reason)

        if any(word in lowered for word in ("订单", "order", "买了什么", "状态")):
            if order_id is None:
                return AgentDecision("clarify", message="请提供需要查询的订单号。")
            return self._call("get_order", order_id=order_id)

        return AgentDecision("respond", message="我可以帮助查询订单、物流、库存或办理退款。")

    @staticmethod
    def _extract(pattern: re.Pattern[str], text: str) -> str | None:
        match = pattern.search(text)
        return match.group(0).upper() if match else None

    @staticmethod
    def _refund_reason(text: str) -> str | None:
        mappings = (
            (("没收到", "未收到", "not received"), "not_received"),
            (("坏", "破损", "损坏", "damaged"), "damaged"),
            (("发错", "错货", "wrong item"), "wrong_item"),
            (("不想要", "不需要", "改变主意", "changed my mind"), "changed_mind"),
        )
        for words, reason in mappings:
            if any(word in text for word in words):
                return reason
        return None

    @staticmethod
    def _call(name: str, **arguments: str) -> AgentDecision:
        return AgentDecision("call", ToolCall(name, arguments))
