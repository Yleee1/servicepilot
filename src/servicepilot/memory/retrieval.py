from __future__ import annotations

import threading
from dataclasses import dataclass
from uuid import uuid4

from .add_policy import validate_write_candidate
from .backend import AddResult, MemoryBackend
from .conflict import resolve_conflict
from .schemas import MemoryHit, MemoryPatch, MemoryQuery, MemoryRecord, utc_now


@dataclass(frozen=True)
class MemorySearchResult:
    hits: tuple[MemoryHit, ...]
    disputed_ids: tuple[str, ...]
    context_summary: str


class ConflictAwareMemoryService:
    """Governance layer shared by local and third-party storage backends."""

    def __init__(self, backend: MemoryBackend) -> None:
        self.backend = backend
        self._write_lock = threading.RLock()

    def add(self, record: MemoryRecord) -> AddResult:
        with self._write_lock:
            return self._add_locked(record)

    def _add_locked(self, record: MemoryRecord) -> AddResult:
        candidate = validate_write_candidate(record)
        existing = self.backend.find_candidates(candidate)
        decision = resolve_conflict(candidate, existing)
        if decision.kind == "duplicate" and decision.existing is not None:
            current = decision.existing
            refs = list(current.attributes.get("source_refs", [current.source_ref]))
            if candidate.source_ref not in refs:
                refs.append(candidate.source_ref)
            updated = self.backend.update(
                current.memory_id,
                MemoryPatch(
                    confidence=max(current.confidence, candidate.confidence),
                    attributes={**current.attributes, "source_refs": refs},
                ),
                reason="duplicate_merge",
            )
            return AddResult(updated, "duplicate_merged", current.memory_id)
        if decision.kind == "temporal_update" and decision.existing is not None:
            old = self.backend.update(
                decision.existing.memory_id,
                MemoryPatch(status="expired", valid_to=utc_now()),
                reason="superseded",
            )
            candidate = candidate.with_updates(supersedes=(old.memory_id,))
            result = self.backend.add(candidate, reason="temporal_update")
            return AddResult(result.record, "superseded", old.memory_id)
        if decision.kind == "true_conflict" and decision.existing is not None:
            conflict_id = decision.existing.conflict_set_id or f"conflict-{uuid4().hex}"
            self.backend.update(
                decision.existing.memory_id,
                MemoryPatch(status="disputed", conflict_set_id=conflict_id),
                reason="true_conflict",
            )
            disputed = candidate.with_updates(status="disputed", conflict_set_id=conflict_id)
            result = self.backend.add(disputed, reason="true_conflict")
            return AddResult(result.record, "disputed", decision.existing.memory_id)
        if decision.kind == "low_authority_conflict" and decision.existing is not None:
            conflict_id = decision.existing.conflict_set_id or f"conflict-{uuid4().hex}"
            disputed = candidate.with_updates(status="disputed", conflict_set_id=conflict_id)
            result = self.backend.add(disputed, reason="low_authority_conflict")
            return AddResult(result.record, "rejected_low_authority", decision.existing.memory_id)
        return self.backend.add(candidate, reason="new_memory")

    def search(self, query: MemoryQuery) -> MemorySearchResult:
        hits = self.backend.search(query)
        disputed_query = MemoryQuery(
            subject_id=query.subject_id, text=query.text, scope_ids=query.scope_ids,
            task_intent=query.task_intent, limit=max(query.limit * 2, 10),
            include_disputed=True, now=query.now,
        )
        all_visible = self.backend.search(disputed_query)
        disputed = tuple(
            hit.record.memory_id for hit in all_visible if hit.record.status == "disputed"
        )
        # Memory is quoted as data, not concatenated as executable instructions.
        summary = "\n".join(
            f"- [{hit.record.memory_id}] {hit.record.content}" for hit in hits
        )
        return MemorySearchResult(tuple(hits), disputed, summary)

    def delete(self, memory_id: str, *, subject_id: str, reason: str = "user_requested") -> None:
        record = self.backend.get(memory_id)
        if record is None:
            return
        if record.subject_id != subject_id:
            raise PermissionError("cannot delete another subject's memory")
        self.backend.invalidate(memory_id, reason)
