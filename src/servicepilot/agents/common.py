from __future__ import annotations

from dataclasses import dataclass

from ..schemas import AgentDecision, ExecutionContext


@dataclass(frozen=True)
class AgentBoundary:
    role: str
    allowed_tools: tuple[str, ...]


class LeastPrivilegeAgent:
    boundary: AgentBoundary

    def __init__(self, caller) -> None:
        self.caller = caller

    def decide(self, instruction: str, user_id: str) -> AgentDecision:
        decision = self.caller.decide(instruction, ExecutionContext(user_id, confirmed=False))
        if decision.tool_call is not None and decision.tool_call.name not in self.boundary.allowed_tools:
            return AgentDecision(
                "respond",
                message=f"{self.boundary.role} agent blocked disallowed tool: {decision.tool_call.name}",
            )
        return decision
