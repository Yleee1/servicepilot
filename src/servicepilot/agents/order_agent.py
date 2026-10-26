from __future__ import annotations

import re

from ..schemas import AgentDecision, ToolCall
from .common import AgentBoundary, LeastPrivilegeAgent


class OrderAgent(LeastPrivilegeAgent):
    boundary = AgentBoundary("order", ("get_order", "track_package"))

    def decide(self, instruction: str, user_id: str) -> AgentDecision:
        if getattr(self.caller, "client", None) is not None:
            return super().decide(instruction, user_id)
        match = re.search(r"\bO\d+\b", instruction, re.IGNORECASE)
        if match is None:
            return AgentDecision("clarify", message="请提供需要查询的订单号。")
        name = (
            "track_package"
            if any(
                word in instruction.lower()
                for word in ("物流", "快递", "到哪", "tracking", "package")
            )
            else "get_order"
        )
        return AgentDecision("call", ToolCall(name, {"order_id": match.group(0).upper()}))
