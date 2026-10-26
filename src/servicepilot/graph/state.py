from __future__ import annotations

from typing import Any, TypedDict


class ServiceGraphState(TypedDict, total=False):
    thread_id: str
    user_id: str
    request: str
    task_queue: list[dict[str, Any]]
    current_task: dict[str, Any] | None
    active_agent: str
    observations: list[dict[str, Any]]
    proposed_call: dict[str, Any] | None
    pending_action: dict[str, Any] | None
    confirmation: dict[str, Any] | None
    handoff_trace: list[dict[str, Any]]
    completed_tasks: list[str]
    retry_count: int
    handoff_count: int
    verification: dict[str, Any]
    final_answer: str
    error: str
    status: str
    next_step: str
    run_version: int
    project_id: str
    organization_id: str
    memory_query: str
    memory_hit_ids: list[str]
    memory_context_summary: str
    memory_write_candidates: list[str]
    memory_audit_events: list[dict[str, Any]]
