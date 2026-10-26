from __future__ import annotations

from .trace_events import ServiceTraceEvent


class OptionalLangfuseSink:
    """Langfuse v4 sink that exports IDs and decisions, never raw customer data."""

    def __init__(self, client=None, *, enabled: bool = False) -> None:
        self.client = client
        self.enabled = enabled

    def emit(self, event: ServiceTraceEvent) -> None:
        if not self.enabled or self.client is None:
            return
        start = getattr(self.client, "start_as_current_observation", None)
        if start is None:
            raise RuntimeError("Langfuse v4 client with start_as_current_observation is required")
        metadata = {
            "trace_id": event.trace_id,
            "thread_id": event.thread_id,
            "run_version": event.run_version,
            "harness_version": event.harness_version,
            "status": event.status,
            "active_agent": event.active_agent,
            "memory_hit_ids": list(event.memory_hit_ids),
            "memory_audit_events": list(event.memory_audit_events),
            "handoff_trace": list(event.handoff_trace),
            "observation_refs": list(event.observation_refs),
            "verification": event.verification,
            "latency_ms": event.latency_ms,
            "error_code": event.error_code,
        }
        with start(
            name="servicepilot.turn",
            as_type="span",
            input={"thread_id": event.thread_id, "run_version": event.run_version},
            output={"status": event.status, "error_code": event.error_code},
            metadata=metadata,
            version=event.harness_version,
            level="ERROR" if event.error_code else "DEFAULT",
            status_message=event.error_code or None,
        ):
            pass

    def flush(self) -> None:
        if not self.enabled or self.client is None:
            return
        flush = getattr(self.client, "flush", None)
        if flush is not None:
            flush()
