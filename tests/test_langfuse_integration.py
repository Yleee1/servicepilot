import json
import tempfile
import unittest
from pathlib import Path

from servicepilot.baseline import RuleBasedToolCaller
from servicepilot.graph_session import GraphServiceSession
from servicepilot.observability import OptionalLangfuseSink
from servicepilot.trace_events import JsonlTraceSink, ServiceTraceEvent


class LangfuseIntegrationTests(unittest.TestCase):
    def test_offline_sdk_contract_accepts_redacted_trace(self):
        from langfuse import Langfuse

        client = Langfuse(tracing_enabled=False)
        sink = OptionalLangfuseSink(client, enabled=True)
        sink.emit(ServiceTraceEvent(
            trace_id="trace-1",
            thread_id="thread-1",
            run_version=1,
            status="executed",
            active_agent="verifier",
            memory_hit_ids=("memory-1",),
            observation_refs=({"tool": "get_order", "ok": True},),
        ))
        sink.flush()

    def test_graph_writes_replayable_redacted_jsonl(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "turns.jsonl"
            session = GraphServiceSession(
                "U001",
                RuleBasedToolCaller(),
                thread_id="trace-local",
                trace_sinks=(JsonlTraceSink(path),),
            )
            try:
                session.handle("查询订单 O1001")
            finally:
                session.close()
            record = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(record["thread_id"], "trace-local")
            self.assertEqual(record["observation_refs"][0]["tool"], "get_order")
            serialized = json.dumps(record, ensure_ascii=False)
            self.assertNotIn("O1001", serialized)
            self.assertNotIn("arguments", serialized)
            self.assertNotIn("data", serialized)

    def test_old_client_shape_is_rejected(self):
        with self.assertRaises(RuntimeError):
            OptionalLangfuseSink(object(), enabled=True).emit(ServiceTraceEvent(
                trace_id="trace-1",
                thread_id="thread-1",
                run_version=1,
                status="blocked",
                active_agent="policy_gate",
            ))


if __name__ == "__main__":
    unittest.main()
