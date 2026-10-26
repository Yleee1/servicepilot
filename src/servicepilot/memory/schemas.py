from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

MEMORY_TYPES = {"semantic", "episodic", "procedural", "negative_experience"}
SCOPE_TYPES = {"user", "project", "task", "organization"}
SOURCE_TYPES = {"user_statement", "tool_result", "verifier", "agent_inference"}
STATUSES = {"active", "disputed", "expired", "deleted"}


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class MemoryRecord:
    subject_id: str
    memory_type: str
    content: str
    scope_type: str
    scope_id: str
    source_type: str
    source_ref: str
    confidence: float
    memory_id: str = field(default_factory=lambda: f"mem-{uuid4().hex}")
    created_at: str = field(default_factory=utc_now)
    valid_from: str = field(default_factory=utc_now)
    valid_to: str | None = None
    supersedes: tuple[str, ...] = ()
    conflict_set_id: str | None = None
    status: str = "active"
    embedding_ref: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)

    def validate(self) -> MemoryRecord:
        if not self.subject_id or not self.scope_id:
            raise ValueError("subject_id and scope_id are required")
        if self.memory_type not in MEMORY_TYPES:
            raise ValueError("invalid memory_type")
        if self.scope_type not in SCOPE_TYPES:
            raise ValueError("invalid scope_type")
        if self.source_type not in SOURCE_TYPES:
            raise ValueError("invalid source_type")
        if self.status not in STATUSES:
            raise ValueError("invalid memory status")
        if not 0 <= self.confidence <= 1:
            raise ValueError("confidence must be between 0 and 1")
        if not self.content.strip():
            raise ValueError("memory content must not be empty")
        return self

    def with_updates(self, **changes: Any) -> MemoryRecord:
        return replace(self, **changes).validate()


@dataclass(frozen=True)
class MemoryPatch:
    content: str | None = None
    confidence: float | None = None
    valid_to: str | None = None
    status: str | None = None
    conflict_set_id: str | None = None
    attributes: dict[str, Any] | None = None


@dataclass(frozen=True)
class MemoryVersion:
    memory_id: str
    revision: int
    operation: str
    record: MemoryRecord
    changed_at: str
    reason: str = ""


@dataclass(frozen=True)
class MemoryQuery:
    subject_id: str
    text: str
    scope_ids: dict[str, str] = field(default_factory=dict)
    task_intent: str = ""
    limit: int = 5
    include_disputed: bool = False
    now: str = field(default_factory=utc_now)


@dataclass(frozen=True)
class MemoryHit:
    record: MemoryRecord
    score: float
    semantic_score: float
    keyword_score: float
    scope_score: float
    authority_score: float
    conflict_penalty: float
