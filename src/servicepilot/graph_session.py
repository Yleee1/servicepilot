from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from time import perf_counter
from typing import Any
from uuid import uuid4

from langgraph.types import Command

from .graph.builder import build_service_graph
from .graph.persistence import create_checkpointer
from .memory import ConflictAwareMemoryService, SQLiteMemoryBackend
from .session import CANCEL_WORDS, CONFIRM_WORDS
from .store import SQLiteDemoStore
from .tools import ToolRegistry
from .trace_events import ServiceTraceEvent

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GraphSessionTurn:
    status: str
    message: str
    thread_id: str
    active_agent: str = ""
    pending_action: dict[str, Any] | None = None
    observations: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    handoff_trace: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    verification: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    memory_hit_ids: tuple[str, ...] = field(default_factory=tuple)
    memory_audit_events: tuple[dict[str, Any], ...] = field(default_factory=tuple)


class GraphServiceSession:
    """LangGraph V2 session; the original ServiceSession remains the A0 baseline."""

    def __init__(
        self,
        user_id: str,
        caller,
        *,
        thread_id: str | None = None,
        registry: ToolRegistry | None = None,
        sqlite_path: str | Path | None = None,
        checkpointer=None,
        max_handoffs: int = 6,
        approval_ttl_seconds: float = 900.0,
        verifier_enabled: bool = True,
        memory_service: ConflictAwareMemoryService | None = None,
        project_id: str = "",
        organization_id: str = "",
        trace_sinks: tuple[Any, ...] = (),
    ) -> None:
        self.user_id = user_id
        self.thread_id = thread_id or f"thread-{uuid4().hex}"
        self.project_id = project_id
        self.organization_id = organization_id
        self.trace_sinks = trace_sinks
        self._store_resource = None
        if registry is not None:
            self.registry = registry
        elif sqlite_path is not None:
            self._store_resource = SQLiteDemoStore(sqlite_path)
            self.registry = ToolRegistry(self._store_resource)
        else:
            self.registry = ToolRegistry()
        self._resource = None
        self._memory_resource = None
        if memory_service is not None:
            self.memory_service = memory_service
        elif sqlite_path is not None:
            self._memory_resource = SQLiteMemoryBackend(sqlite_path)
            self.memory_service = ConflictAwareMemoryService(self._memory_resource)
        else:
            self.memory_service = None
        if checkpointer is None:
            checkpointer, self._resource = create_checkpointer(sqlite_path)
        self.graph = build_service_graph(
            caller,
            registry=self.registry,
            checkpointer=checkpointer,
            max_handoffs=max_handoffs,
            approval_ttl_seconds=approval_ttl_seconds,
            verifier_enabled=verifier_enabled,
            memory_service=self.memory_service,
        )
        self.config = {"configurable": {"thread_id": self.thread_id}}
        self._run_version = 0

    def close(self) -> None:
        if self._resource is not None:
            self._resource.close()
            self._resource = None
        if self._store_resource is not None:
            self._store_resource.close()
            self._store_resource = None
        if self._memory_resource is not None:
            self._memory_resource.close()
            self._memory_resource = None

    def _snapshot(self):
        return self.graph.get_state(self.config)

    def _is_waiting_for_approval(self) -> bool:
        snapshot = self._snapshot()
        return "approval" in tuple(snapshot.next)

    def _checkpoint_user_matches(self) -> bool:
        snapshot = self._snapshot()
        saved_user = snapshot.values.get("user_id") if snapshot.values else None
        return saved_user in {None, self.user_id}

    def handle(self, text: str) -> GraphSessionTurn:
        started = perf_counter()
        normalized = text.strip().lower()
        waiting = self._is_waiting_for_approval()
        if waiting:
            if not self._checkpoint_user_matches():
                return GraphSessionTurn(
                    "blocked",
                    "当前登录用户与 pending action 的可信身份不匹配。",
                    self.thread_id,
                    error="CROSS_USER_CONFIRMATION",
                )
            if normalized in CONFIRM_WORDS:
                state = self.graph.invoke(Command(resume={"approved": True}), self.config)
                return self._turn(state, latency_ms=(perf_counter() - started) * 1000)
            if normalized in CANCEL_WORDS:
                state = self.graph.invoke(Command(resume={"approved": False}), self.config)
                return self._turn(state, latency_ms=(perf_counter() - started) * 1000)
            # A new request invalidates the old pending action before a fresh run.
            self.graph.invoke(Command(resume={"approved": False}), self.config)
        elif normalized in CONFIRM_WORDS:
            return GraphSessionTurn(
                "clarify", "当前没有等待确认的操作。", self.thread_id
            )

        self._run_version += 1
        initial = {
            "thread_id": self.thread_id,
            "user_id": self.user_id,
            "request": text,
            "task_queue": [],
            "current_task": None,
            "active_agent": "supervisor",
            "observations": [],
            "proposed_call": None,
            "pending_action": None,
            "confirmation": None,
            "handoff_trace": [],
            "completed_tasks": [],
            "retry_count": 0,
            "handoff_count": 0,
            "verification": {},
            "final_answer": "",
            "error": "",
            "status": "running",
            "next_step": "supervisor",
            "run_version": self._run_version,
            "project_id": self.project_id,
            "organization_id": self.organization_id,
            "memory_query": "",
            "memory_hit_ids": [],
            "memory_context_summary": "",
            "memory_write_candidates": [],
            "memory_audit_events": [],
        }
        state = self.graph.invoke(initial, self.config)
        return self._turn(state, latency_ms=(perf_counter() - started) * 1000)

    def resume(self, approval: dict[str, Any]) -> GraphSessionTurn:
        started = perf_counter()
        if not self._is_waiting_for_approval():
            return GraphSessionTurn(
                "clarify", "当前没有等待确认的操作。", self.thread_id
            )
        if not self._checkpoint_user_matches():
            return GraphSessionTurn(
                "blocked",
                "当前登录用户与 pending action 的可信身份不匹配。",
                self.thread_id,
                error="CROSS_USER_CONFIRMATION",
            )
        state = self.graph.invoke(Command(resume=approval), self.config)
        return self._turn(state, latency_ms=(perf_counter() - started) * 1000)

    def _turn(self, state: dict, *, latency_ms: float = 0.0) -> GraphSessionTurn:
        pending = state.get("pending_action")
        message = str(state.get("final_answer", ""))
        if pending:
            message = (
                f"待执行：{pending['tool']}，参数：{pending['arguments']}。"
                "回复“确认”或“取消”。"
            )
        turn = GraphSessionTurn(
            status=str(state.get("status", "respond")),
            message=message,
            thread_id=self.thread_id,
            active_agent=str(state.get("active_agent", "")),
            pending_action=dict(pending) if pending else None,
            observations=tuple(state.get("observations", [])),
            handoff_trace=tuple(state.get("handoff_trace", [])),
            verification=dict(state.get("verification", {})),
            error=str(state.get("error", "")),
            memory_hit_ids=tuple(state.get("memory_hit_ids", [])),
            memory_audit_events=tuple(state.get("memory_audit_events", [])),
        )
        self._emit_trace(state, turn, latency_ms)
        return turn

    def _emit_trace(self, state: dict, turn: GraphSessionTurn, latency_ms: float) -> None:
        if not self.trace_sinks:
            return
        observation_refs = tuple({
            "task_id": item.get("task_id", ""),
            "agent": item.get("agent", ""),
            "tool": item.get("tool", ""),
            "ok": bool(item.get("ok")),
            "code": item.get("code", ""),
        } for item in state.get("observations", []))
        event = ServiceTraceEvent(
            trace_id=f"{self.thread_id}:{state.get('run_version', self._run_version)}:{uuid4().hex}",
            thread_id=self.thread_id,
            run_version=int(state.get("run_version", self._run_version)),
            status=turn.status,
            active_agent=turn.active_agent,
            memory_hit_ids=turn.memory_hit_ids,
            memory_audit_events=turn.memory_audit_events,
            handoff_trace=turn.handoff_trace,
            observation_refs=observation_refs,
            verification=turn.verification,
            latency_ms=latency_ms,
            error_code=turn.error,
        )
        for sink in self.trace_sinks:
            try:
                sink.emit(event)
            except Exception:
                # Observability is optional and must not change execution semantics.
                logger.exception("Optional trace sink failed")
