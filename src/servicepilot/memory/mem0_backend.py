from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from .backend import AddResult
from .conflict import AUTHORITY
from .mem0_local import build_local_mem0
from .schemas import (
    MemoryHit,
    MemoryPatch,
    MemoryQuery,
    MemoryRecord,
    MemoryVersion,
    utc_now,
)


def _rows(value) -> list[dict]:
    if isinstance(value, dict):
        result = value.get("results", value.get("memories", []))
        return result if isinstance(result, list) else []
    return value if isinstance(value, list) else []


class Mem0MemoryBackend:
    """Mem0 OSS baseline with ServicePilot record normalization and audit history."""

    def __init__(self, client=None, *, runtime_dir: str | Path | None = None) -> None:
        if client is None and runtime_dir is None:
            raise RuntimeError("Mem0 backend requires a client or an explicit local runtime_dir")
        self.client = client or build_local_mem0(runtime_dir)
        self._records: dict[str, MemoryRecord] = {}
        self._mem0_ids: dict[str, str] = {}
        self._history: dict[str, list[MemoryVersion]] = {}

    @classmethod
    def local(cls, runtime_dir: str | Path) -> Mem0MemoryBackend:
        return cls(runtime_dir=runtime_dir)

    @staticmethod
    def _metadata(record: MemoryRecord) -> dict:
        return {
            "servicepilot_memory_id": record.memory_id,
            "scope_type": record.scope_type,
            "scope_id": record.scope_id,
            "status": record.status,
            "memory_type": record.memory_type,
            "source_type": record.source_type,
            "record_json": json.dumps(asdict(record), ensure_ascii=False, sort_keys=True),
        }

    def _append_history(self, record: MemoryRecord, operation: str, reason: str) -> None:
        history = self._history.setdefault(record.memory_id, [])
        history.append(MemoryVersion(
            memory_id=record.memory_id,
            revision=len(history) + 1,
            operation=operation,
            record=record,
            changed_at=utc_now(),
            reason=reason,
        ))

    def add(self, record: MemoryRecord, *, reason: str = "") -> AddResult:
        record.validate()
        response = self.client.add(
            record.content,
            user_id=record.subject_id,
            metadata=self._metadata(record),
            infer=False,
        )
        rows = _rows(response)
        if not rows or not rows[0].get("id"):
            raise RuntimeError(f"Mem0 did not return a memory id: {response!r}")
        self._records[record.memory_id] = record
        self._mem0_ids[record.memory_id] = str(rows[0]["id"])
        self._append_history(record, "add", reason)
        return AddResult(record, "added")

    def search(self, query: MemoryQuery) -> list[MemoryHit]:
        if not query.subject_id:
            raise ValueError("trusted subject_id is required before Mem0 search")
        allowed = [{"scope_type": "user", "scope_id": query.subject_id}]
        for scope_type, scope_id in query.scope_ids.items():
            if scope_type in {"organization", "project", "task"} and scope_id:
                allowed.append({"scope_type": scope_type, "scope_id": scope_id})
        response = self.client.search(
            query.text,
            user_id=query.subject_id,
            filters={"OR": allowed},
            limit=max(query.limit * 3, query.limit),
            rerank=False,
        )
        hits = []
        scope_priority = {"user": 0.2, "organization": 0.3, "project": 0.5, "task": 0.6}
        for row in _rows(response):
            metadata = dict(row.get("metadata") or {})
            memory_id = str(metadata.get("servicepilot_memory_id", ""))
            record = self._records.get(memory_id)
            if record is None and metadata.get("record_json"):
                payload = json.loads(metadata["record_json"])
                payload["supersedes"] = tuple(payload.get("supersedes", ()))
                record = MemoryRecord(**payload).validate()
                self._records[record.memory_id] = record
            if record is None:
                continue
            allowed_status = {"active", "disputed"} if query.include_disputed else {"active"}
            if record.status not in allowed_status:
                continue
            if record.valid_from > query.now or (record.valid_to and record.valid_to <= query.now):
                continue
            semantic = float(row.get("score", 0.0) or 0.0)
            scope = scope_priority[record.scope_type]
            authority = AUTHORITY[record.source_type] / max(AUTHORITY.values())
            conflict = 1.0 if record.status == "disputed" else 0.0
            score = semantic + scope + authority * 0.2 + record.confidence * 0.1 - conflict
            hits.append(MemoryHit(record, score, semantic, 0.0, scope, authority, conflict))
        hits.sort(key=lambda item: (-item.score, item.record.memory_id))
        return hits[: query.limit]

    def update(self, memory_id: str, patch: MemoryPatch, *, reason: str = "") -> MemoryRecord:
        record = self._records.get(memory_id)
        if record is None:
            raise KeyError(memory_id)
        changes = {key: value for key, value in asdict(patch).items() if value is not None}
        updated = record.with_updates(**changes)
        self.client.update(
            self._mem0_ids[memory_id],
            updated.content,
            metadata=self._metadata(updated),
        )
        self._records[memory_id] = updated
        self._append_history(updated, "update", reason)
        return updated

    def invalidate(self, memory_id: str, reason: str) -> None:
        record = self._records.get(memory_id)
        if record is None:
            return
        deleted = record.with_updates(status="deleted", valid_to=utc_now())
        self.client.delete(self._mem0_ids[memory_id])
        self._records[memory_id] = deleted
        self._append_history(deleted, "invalidate", reason)

    def get(self, memory_id: str) -> MemoryRecord | None:
        return self._records.get(memory_id)

    def get_history(self, memory_id: str) -> list[MemoryVersion]:
        return list(self._history.get(memory_id, ()))

    def find_candidates(self, record: MemoryRecord) -> list[MemoryRecord]:
        return [
            item for item in self._records.values()
            if item.subject_id == record.subject_id
            and item.scope_type == record.scope_type
            and item.scope_id == record.scope_id
            and item.memory_type == record.memory_type
            and item.status in {"active", "disputed"}
        ]
