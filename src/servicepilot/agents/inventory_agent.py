from __future__ import annotations

import re

from ..schemas import AgentDecision, ToolCall
from .common import AgentBoundary, LeastPrivilegeAgent


class InventoryAgent(LeastPrivilegeAgent):
    boundary = AgentBoundary("inventory", ("check_inventory",))

    def decide(self, instruction: str, user_id: str) -> AgentDecision:
        if getattr(self.caller, "client", None) is not None:
            return super().decide(instruction, user_id)
        match = re.search(r"\bSKU[-_A-Z0-9]+\b", instruction, re.IGNORECASE)
        if match is None:
            return AgentDecision("clarify", message="请提供需要查询的 SKU 编号。")
        return AgentDecision(
            "call", ToolCall("check_inventory", {"sku_id": match.group(0).upper()})
        )
