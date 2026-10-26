from __future__ import annotations

import json
from typing import Protocol

from .schemas import AgentDecision, ExecutionContext, ToolCall
from .tools import TOOL_DEFINITIONS


class JSONChatClient(Protocol):
    def chat_json(self, system: str, user: str) -> dict: ...


LEGACY_TOOLS = (
    "get_order",
    "track_package",
    "check_inventory",
    "get_refund_eligibility",
    "create_refund",
)


def build_system_prompt(
    role: str = "general",
    allowed_tools: tuple[str, ...] | None = None,
    state_policy: str = "",
) -> str:
    allowed = allowed_tools or LEGACY_TOOLS
    tool_specs = []
    for name in allowed:
        definition = TOOL_DEFINITIONS[name]
        tool_specs.append(
            {
                "name": definition.name,
                "description": definition.description,
                "parameters": definition.parameters,
                "mutating": definition.mutating,
            }
        )
    return (
            f"你是电商客服系统的 {role} Agent。只输出一个 JSON 对象，不得输出 Markdown。"
            "action 只能是 call、clarify、respond。需要调用工具时输出"
            '{"action":"call","tool":"工具名","arguments":{...},"message":""}；'
            "缺少必填参数时 action=clarify；闲聊或能力外问题 action=respond。"
            "只能调用当前角色 allow-list 中的工具。create_refund 是写操作：confirmed=false 时必须 clarify，不能调用；"
            "confirmed=true 仍必须有订单号和明确退款原因。不要把 user_id 放进参数，"
            "身份由可信会话提供。不得编造参数。可用工具："
            + json.dumps(tool_specs, ensure_ascii=False)
            + (f"。状态策略：{state_policy}" if state_policy else "")
        )


SYSTEM_PROMPT = build_system_prompt()


class ModelToolCaller:
    def __init__(
        self,
        client: JSONChatClient,
        *,
        role: str = "general",
        allowed_tools: tuple[str, ...] | None = None,
        state_policy: str = "",
    ) -> None:
        self.client = client
        self.allowed_tools = allowed_tools or LEGACY_TOOLS
        self.system = build_system_prompt(role, self.allowed_tools, state_policy)

    def for_role(
        self,
        role: str,
        allowed_tools: tuple[str, ...],
        state_policy: str,
    ) -> ModelToolCaller:
        return ModelToolCaller(
            self.client,
            role=role,
            allowed_tools=allowed_tools,
            state_policy=state_policy,
        )

    def decide(self, text: str, context: ExecutionContext) -> AgentDecision:
        payload = {"message": text, "trusted_context": {"confirmed": context.confirmed}}
        return self._decide_payload(payload)

    def decide_with_trace(
        self, text: str, context: ExecutionContext, observations: list[dict]
    ) -> AgentDecision:
        payload = {
            "message": text,
            "trusted_context": {"confirmed": context.confirmed},
            "tool_observations": observations,
            "instruction": "根据工具结果继续完成原始任务；不要重复已有调用，也不要虚构成功。",
        }
        return self._decide_payload(payload)

    def _decide_payload(self, payload: dict) -> AgentDecision:
        value = self.client.chat_json(self.system, json.dumps(payload, ensure_ascii=False))
        action = str(value.get("action", "respond"))
        if action == "call":
            tool = value.get("tool")
            arguments = value.get("arguments")
            if not isinstance(tool, str) or not isinstance(arguments, dict):
                return AgentDecision("respond", message="模型返回了无效的工具调用结构。")
            if tool not in self.allowed_tools:
                return AgentDecision("respond", message=f"角色无权调用工具：{tool}")
            return AgentDecision("call", ToolCall(tool, arguments), str(value.get("message", "")))
        if action not in {"clarify", "respond"}:
            action = "respond"
        return AgentDecision(action, message=str(value.get("message", "")))
