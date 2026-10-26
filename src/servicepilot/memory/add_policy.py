from __future__ import annotations

import re

from .redaction import sanitize_memory_content
from .schemas import MemoryRecord

_OPERATIONAL = re.compile(
    r"(?:订单.{0,8}(?:状态|属于)|退款.{0,8}(?:成功|已执行|资格)|pending|"
    r"confirmed\s*=|idempotency|authorization|order status|refund (?:completed|status|eligible))",
    re.IGNORECASE,
)


def validate_write_candidate(record: MemoryRecord) -> MemoryRecord:
    record.validate()
    if _OPERATIONAL.search(record.content):
        raise ValueError("operational state belongs in the business database/checkpoint")
    if record.attributes.get("frozen_eval") or record.attributes.get("scorer_detail"):
        raise ValueError("evaluation answers and scorer details cannot enter memory")
    if record.source_type == "agent_inference" and not record.attributes.get("verified", False):
        raise ValueError("unverified agent inference cannot enter memory")
    if record.memory_type in {"episodic", "procedural"}:
        verified = record.attributes.get("verified", False)
        if record.source_type not in {"user_statement", "tool_result"} and not verified:
            raise ValueError("experience memory requires a verified event")
    clean = sanitize_memory_content(record.content)
    return record.with_updates(content=clean)
