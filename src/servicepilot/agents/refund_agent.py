from __future__ import annotations

import re

from ..baseline import RuleBasedToolCaller
from ..schemas import AgentDecision, ExecutionContext, ToolCall
from .common import AgentBoundary, LeastPrivilegeAgent

ORDER_RE = re.compile(r"\bO\d+\b", re.IGNORECASE)


class RefundAgent(LeastPrivilegeAgent):
    boundary = AgentBoundary("refund", ("get_refund_eligibility", "propose_refund"))

    def decide_for_state(self, state: dict) -> AgentDecision:
        request = str(state.get("request", ""))
        task_id = str((state.get("current_task") or {}).get("task_id", ""))
        observations = [
            item for item in state.get("observations", []) if item.get("task_id") == task_id
        ]
        eligibility = next(
            (item for item in observations if item.get("tool") == "get_refund_eligibility"),
            None,
        )
        order_match = ORDER_RE.search(request)
        if order_match is None:
            return AgentDecision("clarify", message="请提供需要退款的订单号。")
        order_id = order_match.group(0).upper()
        if eligibility is None:
            return AgentDecision("call", ToolCall("get_refund_eligibility", {"order_id": order_id}))
        if not eligibility.get("ok") or not eligibility.get("data", {}).get("eligible"):
            return AgentDecision("respond", message="当前订单不满足退款条件。")

        # The role may use any underlying model, but its output is converted into
        # a proposal and can never name the executor-only create_refund tool.
        decision = self.caller.decide(request, ExecutionContext(state["user_id"], confirmed=True))
        if decision.tool_call is not None and decision.tool_call.name in {
            "create_refund", "propose_refund"
        }:
            return AgentDecision(
                "call",
                ToolCall("propose_refund", dict(decision.tool_call.arguments)),
                decision.message,
            )
        fallback = RuleBasedToolCaller().decide(
            request, ExecutionContext(state["user_id"], confirmed=True)
        )
        if fallback.tool_call is not None and fallback.tool_call.name == "create_refund":
            return AgentDecision(
                "call", ToolCall("propose_refund", dict(fallback.tool_call.arguments))
            )
        return AgentDecision("clarify", message="请明确说明退款原因。")
