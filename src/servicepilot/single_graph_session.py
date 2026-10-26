from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from .graph.persistence import create_checkpointer
from .graph_session import GraphSessionTurn
from .schemas import ExecutionContext, ToolCall
from .session import CANCEL_WORDS, CONFIRM_WORDS
from .store import SQLiteDemoStore
from .tools import TOOL_DEFINITIONS, ToolRegistry


class SingleAgentState(TypedDict, total=False):
    thread_id: str
    user_id: str
    request: str
    proposed_call: dict[str, Any] | None
    pending_action: dict[str, Any] | None
    observations: list[dict[str, Any]]
    final_answer: str
    status: str
    error: str
    next_step: str
    run_version: int


class SingleAgentGraphSession:
    """A1 baseline: one generalist Tool Caller orchestrated by LangGraph."""

    def __init__(
        self,
        user_id: str,
        caller,
        *,
        thread_id: str,
        registry: ToolRegistry | None = None,
        sqlite_path: str | Path | None = None,
    ) -> None:
        self.user_id = user_id
        self.caller = caller
        self.thread_id = thread_id
        self._store_resource = None
        if registry is not None:
            self.registry = registry
        elif sqlite_path is not None:
            self._store_resource = SQLiteDemoStore(sqlite_path)
            self.registry = ToolRegistry(self._store_resource)
        else:
            self.registry = ToolRegistry()
        checkpointer, self._resource = create_checkpointer(sqlite_path)
        self.graph = self._build_graph(checkpointer)
        self.config = {"configurable": {"thread_id": thread_id}}
        self._run_version = 0

    def _build_graph(self, checkpointer):
        workflow = StateGraph(SingleAgentState)
        workflow.add_node("agent", self._agent)
        workflow.add_node("approval", self._approval)
        workflow.add_node("execute", self._execute)
        workflow.add_node("respond", lambda state: {})
        workflow.add_edge(START, "agent")
        workflow.add_conditional_edges(
            "agent",
            lambda state: state.get("next_step", "respond"),
            {"approval": "approval", "execute": "execute", "respond": "respond"},
        )
        workflow.add_conditional_edges(
            "approval",
            lambda state: state.get("next_step", "respond"),
            {"execute": "execute", "respond": "respond"},
        )
        workflow.add_edge("execute", "respond")
        workflow.add_edge("respond", END)
        return workflow.compile(checkpointer=checkpointer)

    def close(self) -> None:
        if self._resource is not None:
            self._resource.close()
            self._resource = None
        if self._store_resource is not None:
            self._store_resource.close()
            self._store_resource = None

    def _snapshot(self):
        return self.graph.get_state(self.config)

    def _waiting(self) -> bool:
        return "approval" in tuple(self._snapshot().next)

    def _agent(self, state: SingleAgentState) -> dict:
        decision = self.caller.decide(
            state["request"], ExecutionContext(state["user_id"], confirmed=True)
        )
        if decision.tool_call is None:
            return {
                "status": decision.action,
                "final_answer": decision.message,
                "next_step": "respond",
            }
        call = decision.tool_call
        definition = TOOL_DEFINITIONS.get(call.name)
        if definition is None:
            return {
                "status": "blocked",
                "error": "UNKNOWN_TOOL",
                "final_answer": f"unknown tool: {call.name}",
                "next_step": "respond",
            }
        proposed = {"name": call.name, "arguments": call.arguments}
        if not definition.mutating:
            return {"proposed_call": proposed, "next_step": "execute"}
        version = int(state.get("run_version", 1))
        binding = hashlib.sha256(
            json.dumps(
                {
                    "thread_id": state["thread_id"],
                    "user_id": state["user_id"],
                    "tool": call.name,
                    "arguments": call.arguments,
                    "version": version,
                },
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        return {
            "proposed_call": proposed,
            "pending_action": {
                "tool": call.name,
                "arguments": call.arguments,
                "user_id": state["user_id"],
                "version": version,
                "binding": binding,
                "idempotency_key": f"a1-{binding}",
            },
            "status": "confirmation_required",
            "next_step": "approval",
        }

    @staticmethod
    def _approval(state: SingleAgentState) -> dict:
        pending = dict(state.get("pending_action") or {})
        decision = interrupt(
            {
                "type": "tool_approval",
                "thread_id": state["thread_id"],
                "tool": pending.get("tool"),
                "arguments": pending.get("arguments"),
                "binding": pending.get("binding"),
            }
        )
        if not isinstance(decision, dict) or decision.get("approved") is not True:
            return {
                "pending_action": None,
                "status": "cancelled",
                "final_answer": "已取消，未执行任何写操作。",
                "next_step": "respond",
            }
        if decision.get("binding") not in {None, pending.get("binding")}:
            return {
                "pending_action": None,
                "status": "blocked",
                "error": "CONFIRMATION_MISMATCH",
                "final_answer": "确认与待执行 ToolCall 不匹配。",
                "next_step": "respond",
            }
        return {"next_step": "execute"}

    def _execute(self, state: SingleAgentState) -> dict:
        pending = state.get("pending_action")
        proposed = state.get("proposed_call") or {}
        if pending:
            call = ToolCall(str(pending["tool"]), dict(pending["arguments"]))
            result = self.registry.execute_once(
                call,
                ExecutionContext(state["user_id"], confirmed=True, confirmed_call=call),
                idempotency_key=str(pending["idempotency_key"]),
            )
        else:
            call = ToolCall(str(proposed.get("name", "")), dict(proposed.get("arguments", {})))
            result = self.registry.execute(
                call, ExecutionContext(state["user_id"], confirmed=False)
            )
        observation = {
            "task_id": "t1",
            "agent": "generalist",
            "tool": call.name,
            "arguments": call.arguments,
            "ok": result.ok,
            "code": result.code,
            "data": result.data,
            "message": result.message,
        }
        return {
            "observations": list(state.get("observations", [])) + [observation],
            "pending_action": None,
            "status": "executed" if result.ok else "blocked",
            "error": "" if result.ok else result.code,
            "final_answer": result.message or f"{result.code}: {result.data}",
        }

    def handle(self, text: str) -> GraphSessionTurn:
        normalized = text.strip().lower()
        if self._waiting():
            saved_user = self._snapshot().values.get("user_id")
            if saved_user not in {None, self.user_id}:
                return GraphSessionTurn(
                    "blocked",
                    "当前登录用户与 pending action 不匹配。",
                    self.thread_id,
                    error="CROSS_USER_CONFIRMATION",
                )
            if normalized in CONFIRM_WORDS:
                state = self.graph.invoke(Command(resume={"approved": True}), self.config)
                return self._turn(state)
            if normalized in CANCEL_WORDS:
                state = self.graph.invoke(Command(resume={"approved": False}), self.config)
                return self._turn(state)
            self.graph.invoke(Command(resume={"approved": False}), self.config)
        elif normalized in CONFIRM_WORDS:
            return GraphSessionTurn("clarify", "当前没有等待确认的操作。", self.thread_id)

        self._run_version += 1
        state = self.graph.invoke(
            {
                "thread_id": self.thread_id,
                "user_id": self.user_id,
                "request": text,
                "proposed_call": None,
                "pending_action": None,
                "observations": [],
                "final_answer": "",
                "status": "running",
                "error": "",
                "next_step": "agent",
                "run_version": self._run_version,
            },
            self.config,
        )
        return self._turn(state)

    def _turn(self, state: dict) -> GraphSessionTurn:
        pending = state.get("pending_action")
        message = str(state.get("final_answer", ""))
        if pending:
            message = (
                f"待执行：{pending['tool']}，参数：{pending['arguments']}。"
                "回复“确认”或“取消”。"
            )
        return GraphSessionTurn(
            status=str(state.get("status", "respond")),
            message=message,
            thread_id=self.thread_id,
            active_agent="generalist",
            pending_action=dict(pending) if pending else None,
            observations=tuple(state.get("observations", [])),
            error=str(state.get("error", "")),
        )
