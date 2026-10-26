from __future__ import annotations

import json
import re
import sqlite3
import threading
from dataclasses import asdict
from pathlib import Path

from .backend import AddResult
from .conflict import AUTHORITY
from .schemas import (
    MemoryHit,
    MemoryPatch,
    MemoryQuery,
    MemoryRecord,
    MemoryVersion,
    utc_now,
)


def _record(payload: str | dict) -> MemoryRecord:
    value = json.loads(payload) if isinstance(payload, str) else dict(payload)
    value["supersedes"] = tuple(value.get("supersedes", ()))
    value["attributes"] = dict(value.get("attributes", {}))
    return MemoryRecord(**value).validate()


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]{2,}", text.lower()))


class SQLiteMemoryBackend:
    """Versioned SQLite backend with ACL filtering performed in SQL before ranking."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = str(path)
        self.connection = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self.connection.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self.connection.executescript(
            """
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS memories (
                memory_id TEXT PRIMARY KEY,
                subject_id TEXT NOT NULL,
                scope_type TEXT NOT NULL,
                scope_id TEXT NOT NULL,
                status TEXT NOT NULL,
                valid_from TEXT NOT NULL,
                valid_to TEXT,
                record_json TEXT NOT NULL,
                revision INTEGER NOT NULL DEFAULT 1
            );
            CREATE INDEX IF NOT EXISTS idx_memory_acl
                ON memories(subject_id, scope_type, scope_id, status);
            CREATE TABLE IF NOT EXISTS memory_history (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                memory_id TEXT NOT NULL,
                revision INTEGER NOT NULL,
                operation TEXT NOT NULL,
                record_json TEXT NOT NULL,
                changed_at TEXT NOT NULL,
                reason TEXT NOT NULL
            );
            """
        )

    def close(self) -> None:
        self.connection.close()

    def _history(self, record: MemoryRecord, revision: int, operation: str, reason: str) -> None:
        self.connection.execute(
            "INSERT INTO memory_history(memory_id,revision,operation,record_json,changed_at,reason) VALUES(?,?,?,?,?,?)",
            (record.memory_id, revision, operation, json.dumps(asdict(record), ensure_ascii=False, sort_keys=True), utc_now(), reason),
        )

    def add(self, record: MemoryRecord, *, reason: str = "") -> AddResult:
        record.validate()
        payload = json.dumps(asdict(record), ensure_ascii=False, sort_keys=True)
        with self._lock:
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                self.connection.execute(
                    "INSERT INTO memories VALUES(?,?,?,?,?,?,?,?,1)",
                    (record.memory_id, record.subject_id, record.scope_type, record.scope_id,
                     record.status, record.valid_from, record.valid_to, payload),
                )
                self._history(record, 1, "add", reason)
                self.connection.execute("COMMIT")
            except Exception:
                self.connection.execute("ROLLBACK")
                raise
        return AddResult(record, "added")

    def get(self, memory_id: str) -> MemoryRecord | None:
        row = self.connection.execute(
            "SELECT record_json FROM memories WHERE memory_id=?", (memory_id,)
        ).fetchone()
        return _record(row["record_json"]) if row else None

    def update(self, memory_id: str, patch: MemoryPatch, *, reason: str = "") -> MemoryRecord:
        with self._lock:
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                row = self.connection.execute(
                    "SELECT record_json,revision FROM memories WHERE memory_id=?", (memory_id,)
                ).fetchone()
                if row is None:
                    raise KeyError(memory_id)
                record = _record(row["record_json"])
                changes = {
                    key: value for key, value in asdict(patch).items() if value is not None
                }
                updated = record.with_updates(**changes)
                revision = int(row["revision"]) + 1
                payload = json.dumps(asdict(updated), ensure_ascii=False, sort_keys=True)
                self.connection.execute(
                    "UPDATE memories SET status=?,valid_to=?,record_json=?,revision=? WHERE memory_id=?",
                    (updated.status, updated.valid_to, payload, revision, memory_id),
                )
                self._history(updated, revision, "update", reason)
                self.connection.execute("COMMIT")
                return updated
            except Exception:
                self.connection.execute("ROLLBACK")
                raise

    def invalidate(self, memory_id: str, reason: str) -> None:
        self.update(memory_id, MemoryPatch(status="deleted", valid_to=utc_now()), reason=reason)

    def get_history(self, memory_id: str) -> list[MemoryVersion]:
        rows = self.connection.execute(
            "SELECT * FROM memory_history WHERE memory_id=? ORDER BY revision,event_id",
            (memory_id,),
        ).fetchall()
        return [MemoryVersion(
            memory_id=row["memory_id"], revision=int(row["revision"]),
            operation=row["operation"], record=_record(row["record_json"]),
            changed_at=row["changed_at"], reason=row["reason"],
        ) for row in rows]

    def find_candidates(self, record: MemoryRecord) -> list[MemoryRecord]:
        rows = self.connection.execute(
            "SELECT record_json FROM memories WHERE subject_id=? AND scope_type=? AND scope_id=? AND status IN ('active','disputed')",
            (record.subject_id, record.scope_type, record.scope_id),
        ).fetchall()
        return [item for row in rows if (item := _record(row["record_json"])).memory_type == record.memory_type]

    def search(self, query: MemoryQuery) -> list[MemoryHit]:
        if not query.subject_id:
            raise ValueError("trusted subject_id is required before memory search")
        clauses = ["(scope_type='user' AND scope_id=?)"]
        params: list[str] = [query.subject_id, query.now, query.now, query.subject_id]
        for scope_type in ("organization", "project", "task"):
            scope_id = query.scope_ids.get(scope_type)
            if scope_id:
                clauses.append("(scope_type=? AND scope_id=?)")
                params.extend((scope_type, scope_id))
        statuses = "('active','disputed')" if query.include_disputed else "('active')"
        sql = (
            "SELECT record_json FROM memories WHERE subject_id=? AND valid_from<=? "
            "AND (valid_to IS NULL OR valid_to>?) AND status IN " + statuses +
            " AND (" + " OR ".join(clauses) + ")"
        )
        rows = self.connection.execute(sql, params).fetchall()
        query_tokens = _tokens(query.text + " " + query.task_intent)
        scope_priority = {"user": 0.2, "organization": 0.3, "project": 0.5, "task": 0.6}
        hits = []
        for row in rows:
            record = _record(row["record_json"])
            memory_tokens = _tokens(record.content)
            keyword = len(query_tokens & memory_tokens) / max(1, len(query_tokens | memory_tokens))
            semantic = keyword
            scope = scope_priority[record.scope_type]
            authority = AUTHORITY[record.source_type] / max(AUTHORITY.values())
            conflict_penalty = 1.0 if record.status == "disputed" else 0.0
            score = semantic + keyword + scope + authority * 0.2 + record.confidence * 0.1 - conflict_penalty
            hits.append(MemoryHit(record, score, semantic, keyword, scope, authority, conflict_penalty))
        hits.sort(key=lambda item: (-item.score, item.record.memory_id))
        return hits[: max(0, query.limit)]
