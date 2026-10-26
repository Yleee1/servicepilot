from __future__ import annotations

import re

from ..memory import ConflictAwareMemoryService, MemoryQuery, MemoryRecord
from .state import ServiceGraphState

_PREFERENCE_PATTERNS = (
    re.compile(r"(?:以后)?(?:请)?用中文(?:回复)?", re.IGNORECASE),
    re.compile(r"(?:以后)?(?:请)?用英文(?:回复)?", re.IGNORECASE),
    re.compile(r"(?:以后)?(?:请)?(?:邮件|email|slack)通知", re.IGNORECASE),
    re.compile(r"(?:以后)?(?:请)?(?:简洁|详细)(?:回复|解释)?", re.IGNORECASE),
)


class MemoryNodes:
    def __init__(self, service: ConflictAwareMemoryService | None) -> None:
        self.service = service

    def read(self, state: ServiceGraphState) -> dict:
        if self.service is None:
            return {
                "memory_query": state.get("request", ""),
                "memory_hit_ids": [],
                "memory_context_summary": "",
                "memory_audit_events": [{"action": "read", "decision": "disabled"}],
            }
        scope_ids = {}
        for scope_type, state_key in (
            ("organization", "organization_id"), ("project", "project_id"), ("task", "task_id")
        ):
            if state.get(state_key):
                scope_ids[scope_type] = str(state[state_key])
        result = self.service.search(MemoryQuery(
            subject_id=str(state["user_id"]), text=str(state.get("request", "")),
            scope_ids=scope_ids, task_intent=str(state.get("active_agent", "")), limit=4,
        ))
        return {
            "memory_query": state.get("request", ""),
            "memory_hit_ids": [hit.record.memory_id for hit in result.hits],
            "memory_context_summary": result.context_summary,
            "memory_audit_events": [{
                "action": "read", "hit_ids": [hit.record.memory_id for hit in result.hits],
                "disputed_ids": list(result.disputed_ids),
            }],
        }

    @staticmethod
    def _preference_candidates(request: str) -> list[str]:
        return list(dict.fromkeys(
            match.group(0).strip() for pattern in _PREFERENCE_PATTERNS
            for match in pattern.finditer(request)
        ))

    def write(self, state: ServiceGraphState) -> dict:
        audit = list(state.get("memory_audit_events", []))
        candidates = self._preference_candidates(str(state.get("request", "")))
        if self.service is None or state.get("verification", {}).get("passed") is not True:
            audit.append({"action": "write", "decision": "skipped_unverified_or_disabled"})
            return {"memory_write_candidates": candidates, "memory_audit_events": audit}
        for content in candidates:
            try:
                result = self.service.add(MemoryRecord(
                    subject_id=str(state["user_id"]), memory_type="semantic", content=content,
                    scope_type="project" if state.get("project_id") else "user",
                    scope_id=str(state.get("project_id") or state["user_id"]),
                    source_type="user_statement", source_ref=str(state.get("thread_id", "")),
                    confidence=1.0, attributes={"verified": True},
                ))
                audit.append({
                    "action": "write", "decision": result.decision,
                    "memory_id": result.record.memory_id,
                })
            except (ValueError, PermissionError) as exc:
                audit.append({"action": "write", "decision": "rejected", "reason": str(exc)})
        if not candidates:
            audit.append({"action": "write", "decision": "no_stable_candidate"})
        return {"memory_write_candidates": candidates, "memory_audit_events": audit}
