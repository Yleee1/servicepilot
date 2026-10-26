from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from time import time
from uuid import uuid4

from langgraph.types import interrupt

from ..agents import (
    InventoryAgent,
    OrderAgent,
    RefundAgent,
    SupervisorAgent,
    VerifierAgent,
)
from ..schemas import ExecutionContext, ToolCall
from ..tools import ToolRegistry
from .memory_nodes import MemoryNodes
from .state import ServiceGraphState

AGENT_TOOLS = {
    "order": frozenset(("get_order", "track_package")),
    "inventory": frozenset(("check_inventory",)),
    "refund": frozenset(("get_refund_eligibility", "propose_refund")),
}


class GraphNodes:
    def __init__(
        self,
        caller,
        registry: ToolRegistry,
        *,
        max_handoffs: int = 6,
        approval_ttl_seconds: float = 900.0,
        verifier_enabled: bool = True,
        memory_service=None,
    ) -> None:
        self.registry = registry
        self.max_handoffs = max_handoffs
        self.approval_ttl_seconds = approval_ttl_seconds
        self.verifier_enabled = verifier_enabled
        self.memory_nodes = MemoryNodes(memory_service)
        client = getattr(caller, "client", None)
        specialize = getattr(caller, "for_role", None)
        order_caller = (
            specialize("order", ("get_order", "track_package"), "只能处理当前订单子任务")
            if specialize else caller
        )
        inventory_caller = (
            specialize("inventory", ("check_inventory",), "只能处理当前库存子任务")
            if specialize else caller
        )
        refund_caller = (
            specialize(
                "refund",
                ("get_refund_eligibility", "propose_refund"),
                "只能检查资格或提出退款提案，永远不能执行 create_refund",
            )
            if specialize else caller
        )
        self.supervisor_agent = SupervisorAgent(client)
        self.order_agent = OrderAgent(order_caller)
        self.inventory_agent = InventoryAgent(inventory_caller)
        self.refund_agent = RefundAgent(refund_caller)
        self.verifier_agent = VerifierAgent(client)

    def ingress(self, state: ServiceGraphState) -> dict:
        return {"status": "running", "error": "", "next_step": "supervisor"}

    def memory_read(self, state: ServiceGraphState) -> dict:
        return self.memory_nodes.read(state)

    def memory_write(self, state: ServiceGraphState) -> dict:
        return self.memory_nodes.write(state)

    def supervisor(self, state: ServiceGraphState) -> dict:
        tasks = list(state.get("task_queue", []))
        if not tasks:
            tasks = self.supervisor_agent.plan(state.get("request", ""))
        if not tasks:
            return {
                "task_queue": [],
                "active_agent": "respond",
                "status": "clarify",
                "final_answer": "我可以帮助查询订单、物流、库存或办理退款。",
            }
        handoffs = int(state.get("handoff_count", 0))
        if handoffs >= self.max_handoffs:
            return {
                "task_queue": tasks,
                "active_agent": "respond",
                "status": "escalated",
                "error": "HANDOFF_LIMIT",
                "final_answer": "多 Agent handoff 已达到上限，已转人工处理。",
            }
        current = self.supervisor_agent.choose(tasks, list(state.get("completed_tasks", [])))
        if current is None:
            if self.verifier_enabled:
                return {"task_queue": tasks, "active_agent": "verifier"}
            return {
                "task_queue": tasks,
                "active_agent": "respond",
                "status": "executed",
                "verification": {"skipped": True, "variant": "A2"},
            }
        trace = list(state.get("handoff_trace", []))
        trace.append(
            {
                "sequence": handoffs + 1,
                "from": state.get("active_agent", "supervisor"),
                "to": current["agent"],
                "task_id": current["task_id"],
                "reason": "dependencies satisfied",
            }
        )
        return {
            "task_queue": tasks,
            "current_task": current,
            "active_agent": current["agent"],
            "handoff_count": handoffs + 1,
            "handoff_trace": trace,
            "proposed_call": None,
        }

    def _agent_update(self, state: ServiceGraphState, decision) -> dict:
        if decision.tool_call is None:
            return {
                "proposed_call": None,
                "status": decision.action,
                "final_answer": decision.message,
            }
        return {"proposed_call": asdict(decision.tool_call), "status": "proposed"}

    def order(self, state: ServiceGraphState) -> dict:
        task = state.get("current_task") or {}
        return self._agent_update(
            state, self.order_agent.decide(str(task.get("instruction", "")), state["user_id"])
        )

    def inventory(self, state: ServiceGraphState) -> dict:
        task = state.get("current_task") or {}
        return self._agent_update(
            state,
            self.inventory_agent.decide(str(task.get("instruction", "")), state["user_id"]),
        )

    def refund(self, state: ServiceGraphState) -> dict:
        return self._agent_update(state, self.refund_agent.decide_for_state(state))

    def policy_gate(self, state: ServiceGraphState) -> dict:
        proposed = state.get("proposed_call")
        if not proposed:
            return {"next_step": "respond"}
        call = ToolCall(str(proposed.get("name", "")), dict(proposed.get("arguments", {})))
        agent = state.get("active_agent", "")
        if call.name not in AGENT_TOOLS.get(agent, frozenset()):
            return {
                "next_step": "respond",
                "status": "blocked",
                "error": "TOOL_PERMISSION_DENIED",
                "final_answer": f"{agent} Agent 无权调用 {call.name}。",
            }
        if call.name == "propose_refund":
            proposal_check = self.registry.execute(
                call, ExecutionContext(state["user_id"], confirmed=False)
            )
            if not proposal_check.ok:
                return {
                    "next_step": "respond",
                    "status": "blocked",
                    "error": proposal_check.code,
                    "final_answer": proposal_check.message,
                }
            executable = ToolCall("create_refund", dict(call.arguments))
            version = int(state.get("run_version", 1))
            binding = hashlib.sha256(
                json.dumps(
                    {
                        "thread_id": state["thread_id"],
                        "user_id": state["user_id"],
                        "tool": executable.name,
                        "arguments": executable.arguments,
                        "version": version,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ).encode("utf-8")
            ).hexdigest()
            return {
                "pending_action": {
                    "tool": executable.name,
                    "arguments": executable.arguments,
                    "user_id": state["user_id"],
                    "created_at": time(),
                    "version": version,
                    "binding": binding,
                    "idempotency_key": f"refund-{uuid4().hex}",
                },
                "next_step": "approval",
                "status": "confirmation_required",
            }
        return {"next_step": "execute"}

    def approval(self, state: ServiceGraphState) -> dict:
        pending = dict(state.get("pending_action") or {})
        decision = interrupt(
            {
                "type": "tool_approval",
                "thread_id": state["thread_id"],
                "tool": pending.get("tool"),
                "arguments": pending.get("arguments"),
                "binding": pending.get("binding"),
                "version": pending.get("version"),
            }
        )
        if not isinstance(decision, dict) or decision.get("approved") is not True:
            return {
                "confirmation": {"approved": False},
                "pending_action": None,
                "next_step": "respond",
                "status": "cancelled",
                "final_answer": "已取消，未执行任何写操作。",
            }
        if time() - float(pending.get("created_at", 0)) > self.approval_ttl_seconds:
            return {
                "confirmation": decision,
                "pending_action": None,
                "next_step": "respond",
                "status": "blocked",
                "error": "STALE_APPROVAL",
                "final_answer": "待确认操作已过期，请重新发起请求。",
            }
        supplied_binding = decision.get("binding")
        if supplied_binding is not None and supplied_binding != pending.get("binding"):
            return {
                "confirmation": decision,
                "pending_action": None,
                "next_step": "respond",
                "status": "blocked",
                "error": "CONFIRMATION_MISMATCH",
                "final_answer": "确认与待执行 ToolCall 不匹配，已拒绝执行。",
            }
        return {"confirmation": decision, "next_step": "execute"}

    def execute(self, state: ServiceGraphState) -> dict:
        pending = state.get("pending_action")
        retried = False
        if pending:
            call = ToolCall(str(pending["tool"]), dict(pending["arguments"]))
            context = ExecutionContext(state["user_id"], confirmed=True, confirmed_call=call)
            result = self.registry.execute_once(
                call, context, idempotency_key=str(pending["idempotency_key"])
            )
        else:
            proposed = state.get("proposed_call") or {}
            call = ToolCall(str(proposed.get("name", "")), dict(proposed.get("arguments", {})))
            result = self.registry.execute(
                call, ExecutionContext(state["user_id"], confirmed=False)
            )
            if (
                not result.ok
                and result.code in {"TIMEOUT", "TRANSIENT_ERROR"}
                and int(state.get("retry_count", 0)) < 1
            ):
                retried = True
                result = self.registry.execute(
                    call, ExecutionContext(state["user_id"], confirmed=False)
                )
        task = state.get("current_task") or {}
        observation = {
            "task_id": task.get("task_id", ""),
            "agent": state.get("active_agent", ""),
            "tool": call.name,
            "arguments": call.arguments,
            "ok": result.ok,
            "code": result.code,
            "data": result.data,
            "message": result.message,
        }
        observations = list(state.get("observations", [])) + [observation]
        updates = {
            "observations": observations,
            "proposed_call": None,
            "pending_action": None,
            "status": "executed" if result.ok else "blocked",
            "error": "" if result.ok else result.code,
            "final_answer": result.message or f"{result.code}: {result.data}",
        }
        if retried:
            updates["retry_count"] = int(state.get("retry_count", 0)) + 1
        if call.name == "get_refund_eligibility" and result.ok:
            request = str(state.get("request", "")).lower()
            operation_words = (
                "申请退款",
                "我要退款",
                "帮我退款",
                "退掉",
                "退款",
                "退货",
                "refund",
                "refund it",
                "issue a refund",
            )
            advisory_words = (
                "能不能",
                "是否",
                "资格",
                "条件",
                "先别",
                "咨询",
                "不要执行",
                "不要退款",
                "别退款",
                "eligible",
            )
            is_operation = any(word in request for word in operation_words) and not any(
                word in request for word in advisory_words
            )
            if is_operation:
                updates["next_step"] = "refund"
                return updates
        if result.ok:
            completed = list(state.get("completed_tasks", []))
            task_id = str(task.get("task_id", ""))
            if task_id and task_id not in completed:
                completed.append(task_id)
            updates["completed_tasks"] = completed
            updates["next_step"] = "supervisor"
        else:
            updates["next_step"] = "verifier" if self.verifier_enabled else "respond"
        return updates

    def verifier(self, state: ServiceGraphState) -> dict:
        verification = self.verifier_agent.verify(state)
        if verification["passed"]:
            return {"verification": verification, "active_agent": "verifier", "next_step": "respond"}
        retry_count = int(state.get("retry_count", 0))
        if retry_count < 1 and verification["missing_tasks"] and not verification["failed_observations"]:
            return {
                "verification": verification,
                "retry_count": retry_count + 1,
                "active_agent": "supervisor",
                "next_step": "supervisor",
            }
        return {
            "verification": {**verification, "next_action": "escalate"},
            "next_step": "respond",
            "status": "escalated",
            "final_answer": "任务验证未通过，已转人工处理。",
        }

    def respond(self, state: ServiceGraphState) -> dict:
        return {"active_agent": state.get("active_agent", "respond")}
