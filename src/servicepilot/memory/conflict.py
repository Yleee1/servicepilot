from __future__ import annotations

import re
from dataclasses import dataclass

from .schemas import MemoryRecord

AUTHORITY = {
    "agent_inference": 1,
    "verifier": 2,
    "tool_result": 3,
    "user_statement": 4,
}


def canonical(text: str) -> str:
    lowered = text.lower()
    replacements = {
        "请用中文": "language=zh", "中文回复": "language=zh", "用中文": "language=zh",
        "reply in chinese": "language=zh", "use chinese": "language=zh",
        "请用英文": "language=en", "英文回复": "language=en", "reply in english": "language=en",
        "邮件通知": "notify=email", "email notification": "notify=email", "email": "notify=email",
        "slack通知": "notify=slack", "slack": "notify=slack",
        "简洁": "detail=brief", "brief": "detail=brief", "详细": "detail=detailed", "detailed": "detail=detailed",
    }
    for source, target in replacements.items():
        if source in lowered:
            return target
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", lowered)


def preference_key(text: str) -> str:
    value = canonical(text)
    return value.split("=", 1)[0] if "=" in value else value


@dataclass(frozen=True)
class ConflictDecision:
    kind: str
    existing: MemoryRecord | None = None


def resolve_conflict(candidate: MemoryRecord, existing: list[MemoryRecord]) -> ConflictDecision:
    candidate_value = canonical(candidate.content)
    candidate_key = preference_key(candidate.content)
    for record in existing:
        if canonical(record.content) == candidate_value:
            return ConflictDecision("duplicate", record)
    for record in existing:
        if preference_key(record.content) != candidate_key:
            continue
        if AUTHORITY[candidate.source_type] < AUTHORITY[record.source_type]:
            return ConflictDecision("low_authority_conflict", record)
        if candidate.attributes.get("update", False):
            return ConflictDecision("temporal_update", record)
        return ConflictDecision("true_conflict", record)
    return ConflictDecision("new")
