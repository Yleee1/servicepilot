from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ServiceTraceEvent:
    """Replayable, redacted trace for one completed graph turn."""

    trace_id: str
    thread_id: str
    run_version: int
    status: str
    active_agent: str
    memory_hit_ids: tuple[str, ...] = ()
    memory_audit_events: tuple[dict[str, Any], ...] = ()
    handoff_trace: tuple[dict[str, Any], ...] = ()
    observation_refs: tuple[dict[str, Any], ...] = ()
    verification: dict[str, Any] = field(default_factory=dict)
    latency_ms: float = 0.0
    error_code: str = ""
    harness_version: str = "servicepilot-memory-v3"
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class JsonlTraceSink:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def emit(self, event: ServiceTraceEvent) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")

    def flush(self) -> None:
        return None
