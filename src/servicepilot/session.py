from __future__ import annotations

from dataclasses import dataclass

from .schemas import AgentDecision, ExecutionContext, ToolCall, ToolResult
from .tools import TOOL_DEFINITIONS, ToolRegistry

CONFIRM_WORDS = {"确认", "确定", "同意", "yes", "confirm", "确认执行"}
CANCEL_WORDS = {"取消", "算了", "不要了", "no", "cancel"}


@dataclass(frozen=True)
class SessionTurn:
    status: str
    message: str
    decision: AgentDecision | None = None
    result: ToolResult | None = None
    pending_call: ToolCall | None = None


class ProposalCaller:
    """把现有 caller 安全地用于交互式草案生成，而不授予执行权限。"""

    def __init__(self, caller) -> None:
        self.caller = caller

    def decide(self, text: str, context: ExecutionContext) -> AgentDecision:
        # 模型在这里只能提出草案。即使它收到 confirmed=True，也不会接触执行器。
        draft_context = ExecutionContext(context.user_id, confirmed=True)
        decision = self.caller.decide(text, draft_context)
        if decision.tool_call is not None:
            definition = TOOL_DEFINITIONS.get(decision.tool_call.name)
            if definition is not None and definition.mutating:
                return AgentDecision("confirm", decision.tool_call, "请确认是否执行该操作。")
        return decision

    def decide_with_trace(
        self, text: str, context: ExecutionContext, observations: list[dict]
    ) -> AgentDecision:
        draft_context = ExecutionContext(context.user_id, confirmed=True)
        method = getattr(self.caller, "decide_with_trace", None)
        if method is None:
            return self.decide(text, context)
        decision = method(text, draft_context, observations)
        if decision.tool_call is not None:
            definition = TOOL_DEFINITIONS.get(decision.tool_call.name)
            if definition is not None and definition.mutating:
                return AgentDecision("confirm", decision.tool_call, "请确认是否执行该操作。")
        return decision


class ServiceSession:
    def __init__(self, user_id: str, caller, registry: ToolRegistry | None = None) -> None:
        self.user_id = user_id
        self.caller = caller
        self.registry = registry or ToolRegistry()
        self.pending_call: ToolCall | None = None

    def handle(self, text: str) -> SessionTurn:
        normalized = text.strip().lower()
        if self.pending_call is not None:
            if normalized in CONFIRM_WORDS:
                call = self.pending_call
                self.pending_call = None
                context = ExecutionContext(self.user_id, confirmed=True, confirmed_call=call)
                result = self.registry.execute(call, context)
                return SessionTurn(
                    "executed" if result.ok else "blocked",
                    result.message or self._render_result(result),
                    result=result,
                )
            if normalized in CANCEL_WORDS:
                self.pending_call = None
                return SessionTurn("cancelled", "已取消，未执行任何写操作。")
            # 新请求会废弃旧草案，避免“确认”绑定到过期上下文。
            self.pending_call = None

        if normalized in CONFIRM_WORDS:
            return SessionTurn("clarify", "当前没有等待确认的操作。")

        decision = self.caller.decide(text, ExecutionContext(self.user_id, confirmed=False))
        if decision.action == "confirm" and decision.tool_call is not None:
            return self._save_pending(decision, text)
        if decision.action == "call" and decision.tool_call is not None:
            definition = TOOL_DEFINITIONS.get(decision.tool_call.name)
            if definition is not None and definition.mutating:
                # 即使 caller 错误地主张直接执行，编排层仍强制转成待确认草案。
                return self._save_pending(
                    AgentDecision("confirm", decision.tool_call, "写操作需要再次确认。"), text
                )
            result = self.registry.execute(
                decision.tool_call, ExecutionContext(self.user_id, confirmed=False)
            )
            if (
                decision.tool_call.name == "get_refund_eligibility"
                and result.ok
                and result.data.get("eligible") is True
                and self._is_refund_operation(text)
            ):
                follow_method = getattr(self.caller, "decide_with_trace", None)
                if follow_method is not None:
                    observation = {
                        "tool": decision.tool_call.name,
                        "arguments": decision.tool_call.arguments,
                        "result": {"code": result.code, "data": result.data},
                    }
                    follow = follow_method(
                        text, ExecutionContext(self.user_id, confirmed=False), [observation]
                    )
                    if follow.tool_call is not None:
                        definition = TOOL_DEFINITIONS.get(follow.tool_call.name)
                        if definition is not None and definition.mutating:
                            pending = self._save_pending(follow, text)
                            return SessionTurn(
                                pending.status,
                                f"资格检查通过。{pending.message}",
                                follow,
                                result,
                                pending.pending_call,
                            )
            return SessionTurn(
                "executed" if result.ok else "blocked",
                result.message or self._render_result(result),
                decision=decision,
                result=result,
            )
        return SessionTurn(decision.action, decision.message, decision=decision)

    def _save_pending(self, decision: AgentDecision, source_text: str) -> SessionTurn:
        grounding_error = self._proposal_grounding_error(decision.tool_call, source_text)
        if grounding_error is not None:
            return SessionTurn("clarify", grounding_error, decision=decision)
        self.pending_call = decision.tool_call
        call = decision.tool_call
        summary = f"待执行：{call.name}，参数：{call.arguments}。回复“确认”或“取消”。"
        return SessionTurn("confirmation_required", summary, decision, pending_call=call)

    @staticmethod
    def _proposal_grounding_error(call: ToolCall, source_text: str) -> str | None:
        if call.name != "create_refund":
            return None
        lowered = source_text.lower()
        order_id = str(call.arguments.get("order_id", ""))
        if not order_id or order_id.lower() not in lowered:
            return "模型提出的订单号不在用户原话中，请重新提供订单号。"
        reason = call.arguments.get("reason")
        reason_evidence = {
            "not_received": ("没收到", "未收到", "没有送到", "not received"),
            "damaged": ("坏", "破", "损坏", "damaged"),
            "wrong_item": ("发错", "错货", "不是我买", "wrong item"),
            "changed_mind": ("不想要", "不需要", "改变主意", "changed my mind"),
        }
        evidence = reason_evidence.get(reason, ())
        if not evidence or not any(phrase in lowered for phrase in evidence):
            return "退款原因无法由用户原话确认，请明确说明退款原因。"
        return None

    @staticmethod
    def _is_refund_operation(text: str) -> bool:
        lowered = text.lower()
        operation_words = (
            "申请退款", "我要退款", "帮我退款", "退掉", "退款", "退货", "refund",
            "refund it", "issue a refund",
        )
        advisory_words = (
            "能不能", "是否", "资格", "条件", "先别", "咨询", "不要执行",
            "不要退款", "别退款", "eligible",
        )
        return any(word in lowered for word in operation_words) and not any(
            word in lowered for word in advisory_words
        )

    @staticmethod
    def _render_result(result: ToolResult) -> str:
        return f"{result.code}: {result.data}" if result.data else result.code
