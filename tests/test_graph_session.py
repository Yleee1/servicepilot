import json
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from servicepilot.agents import (
    InventoryAgent,
    OrderAgent,
    RefundAgent,
    SupervisorAgent,
    VerifierAgent,
)
from servicepilot.baseline import RuleBasedToolCaller
from servicepilot.graph_session import GraphServiceSession
from servicepilot.model_caller import build_system_prompt
from servicepilot.schemas import ExecutionContext, ToolCall, ToolResult
from servicepilot.single_graph_session import SingleAgentGraphSession
from servicepilot.store import SQLiteDemoStore
from servicepilot.tools import ToolRegistry


class AgentBoundaryTests(unittest.TestCase):
    def test_role_prompt_exposes_only_allowed_tools(self):
        prompt = build_system_prompt("refund", ("get_refund_eligibility", "propose_refund"), "no writes")
        self.assertIn("propose_refund", prompt)
        self.assertNotIn('"name": "create_refund"', prompt)

    def test_supervisor_and_verifier_have_no_business_tools(self):
        self.assertEqual(SupervisorAgent.allowed_tools, ())
        self.assertEqual(VerifierAgent.allowed_tools, ())

    def test_experts_have_disjoint_least_privilege_tools(self):
        self.assertEqual(OrderAgent.boundary.allowed_tools, ("get_order", "track_package"))
        self.assertEqual(InventoryAgent.boundary.allowed_tools, ("check_inventory",))
        self.assertNotIn("create_refund", RefundAgent.boundary.allowed_tools)


class GraphServiceSessionTests(unittest.TestCase):
    def test_a1_single_agent_langgraph_has_no_handoffs(self):
        session = SingleAgentGraphSession(
            "U001", RuleBasedToolCaller(), thread_id="a1-single"
        )
        try:
            turn = session.handle("查询订单 O1001 和库存 SKU-RED-42")
            self.assertEqual(turn.status, "executed")
            self.assertEqual(turn.handoff_trace, ())
            self.assertEqual([item["tool"] for item in turn.observations], ["check_inventory"])
        finally:
            session.close()

    def test_a2_skips_verifier_explicitly(self):
        session = GraphServiceSession(
            "U001",
            RuleBasedToolCaller(),
            thread_id="a2-no-verifier",
            verifier_enabled=False,
        )
        try:
            turn = session.handle("查询订单 O1001")
            self.assertEqual(turn.status, "executed")
            self.assertEqual(turn.verification, {"skipped": True, "variant": "A2"})
        finally:
            session.close()

    def test_handoff_limit_escalates_compound_request(self):
        session = GraphServiceSession(
            "U001", RuleBasedToolCaller(), thread_id="loop-limit", max_handoffs=1
        )
        try:
            turn = session.handle("查询订单 O1001 和库存 SKU-RED-42")
            self.assertEqual(turn.status, "escalated")
            self.assertEqual(turn.error, "HANDOFF_LIMIT")
        finally:
            session.close()

    def test_transient_read_error_retries_once(self):
        class FlakyRegistry(ToolRegistry):
            def __init__(self):
                super().__init__()
                self.calls = 0

            def execute(self, call, context):
                self.calls += 1
                if self.calls == 1:
                    return ToolResult(False, "TIMEOUT", message="temporary")
                return super().execute(call, context)

        registry = FlakyRegistry()
        session = GraphServiceSession(
            "U001", RuleBasedToolCaller(), thread_id="retry", registry=registry
        )
        try:
            turn = session.handle("查询订单 O1001")
            self.assertEqual(turn.status, "executed")
            self.assertEqual(registry.calls, 2)
        finally:
            session.close()

    def test_compound_refund_runs_dependency_then_interrupts(self):
        session = GraphServiceSession("U001", RuleBasedToolCaller(), thread_id="compound")
        try:
            first = session.handle("O1002 商品坏了，帮我退款")
            self.assertEqual(first.status, "confirmation_required")
            self.assertEqual([item["tool"] for item in first.observations], [
                "get_order", "get_refund_eligibility"
            ])
            self.assertEqual([item["to"] for item in first.handoff_trace], ["order", "refund"])
            self.assertEqual(first.pending_action["tool"], "create_refund")
            second = session.handle("确认")
            self.assertEqual(second.status, "executed")
            self.assertTrue(second.verification["passed"])
            self.assertEqual(session.registry.store.orders["O1002"].status, "refund_requested")
        finally:
            session.close()

    def test_binding_mismatch_fails_closed(self):
        session = GraphServiceSession("U001", RuleBasedToolCaller(), thread_id="binding")
        try:
            first = session.handle("O1002 商品坏了，帮我退款")
            turn = session.resume({"approved": True, "binding": first.pending_action["binding"] + "x"})
            self.assertEqual(turn.status, "blocked")
            self.assertEqual(turn.error, "CONFIRMATION_MISMATCH")
            self.assertEqual(session.registry.store.orders["O1002"].status, "delivered")
        finally:
            session.close()

    def test_stale_approval_fails_closed(self):
        session = GraphServiceSession(
            "U001",
            RuleBasedToolCaller(),
            thread_id="expired",
            approval_ttl_seconds=0,
        )
        try:
            session.handle("O1002 商品坏了，帮我退款")
            turn = session.handle("确认")
            self.assertEqual(turn.status, "blocked")
            self.assertEqual(turn.error, "STALE_APPROVAL")
            self.assertEqual(session.registry.store.orders["O1002"].status, "delivered")
        finally:
            session.close()

    def test_duplicate_confirmation_does_not_repeat_mutation(self):
        session = GraphServiceSession("U001", RuleBasedToolCaller(), thread_id="duplicate")
        try:
            session.handle("O1002 商品坏了，帮我退款")
            first = session.handle("确认")
            refund_id = first.observations[-1]["data"]["refund_id"]
            second = session.handle("确认")
            self.assertEqual(second.status, "clarify")
            self.assertEqual(session.registry.store.orders["O1002"].refund_id, refund_id)
        finally:
            session.close()

    def test_new_request_invalidates_pending_action(self):
        session = GraphServiceSession("U001", RuleBasedToolCaller(), thread_id="stale")
        try:
            session.handle("O1002 商品坏了，帮我退款")
            order = session.handle("查询订单 O1001")
            self.assertEqual(order.status, "executed")
            confirm = session.handle("确认")
            self.assertEqual(confirm.status, "clarify")
            self.assertEqual(session.registry.store.orders["O1002"].status, "delivered")
        finally:
            session.close()

    def test_sqlite_checkpoint_resumes_after_session_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "graph.sqlite"
            first_session = GraphServiceSession(
                "U001", RuleBasedToolCaller(), thread_id="restart", sqlite_path=path
            )
            first = first_session.handle("O1002 商品坏了，帮我退款")
            first_session.close()
            self.assertEqual(first.status, "confirmation_required")

            resumed = GraphServiceSession(
                "U001", RuleBasedToolCaller(), thread_id="restart", sqlite_path=path
            )
            try:
                turn = resumed.handle("确认")
                self.assertEqual(turn.status, "executed")
                self.assertEqual(turn.observations[-1]["tool"], "create_refund")
                self.assertEqual(
                    resumed.registry.store.get_order("O1002").status,
                    "refund_requested",
                )
            finally:
                resumed.close()

            third = GraphServiceSession(
                "U001", RuleBasedToolCaller(), thread_id="restart-read", sqlite_path=path
            )
            try:
                self.assertEqual(
                    third.registry.store.get_order("O1002").status,
                    "refund_requested",
                )
            finally:
                third.close()

    def test_checkpoint_and_business_state_resume_in_a_new_process(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "process.sqlite"
            base = [
                sys.executable,
                "-m",
                "servicepilot.restart_probe",
                "--database",
                str(path),
                "--thread-id",
                "process-restart",
                "--message",
            ]
            first = subprocess.run(
                [*base, "O1002 商品坏了，帮我退款"],
                check=True,
                capture_output=True,
                text=True,
            )
            first_value = json.loads(first.stdout)
            self.assertEqual(first_value["status"], "confirmation_required")
            second = subprocess.run(
                [*base, "确认"],
                check=True,
                capture_output=True,
                text=True,
            )
            second_value = json.loads(second.stdout)
            self.assertEqual(second_value["status"], "executed")
            self.assertEqual(second_value["order_status"], "refund_requested")

    def test_durable_idempotency_survives_registry_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "durable.sqlite"
            call = ToolCall("create_refund", {"order_id": "O1002", "reason": "damaged"})
            context = ExecutionContext("U001", confirmed=True, confirmed_call=call)
            first_store = SQLiteDemoStore(path)
            first = ToolRegistry(first_store).execute_once(call, context, idempotency_key="same-key")
            first_store.close()
            second_store = SQLiteDemoStore(path)
            try:
                second = ToolRegistry(second_store).execute_once(
                    call, context, idempotency_key="same-key"
                )
                self.assertEqual(first, second)
                self.assertEqual(second_store.get_order("O1002").refund_id, first.data["refund_id"])
            finally:
                second_store.close()

    def test_concurrent_confirmations_commit_one_refund(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "concurrent.sqlite"
            call = ToolCall("create_refund", {"order_id": "O1002", "reason": "damaged"})
            context = ExecutionContext("U001", confirmed=True, confirmed_call=call)

            def execute():
                store = SQLiteDemoStore(path)
                try:
                    return ToolRegistry(store).execute_once(
                        call, context, idempotency_key="concurrent-key"
                    )
                finally:
                    store.close()

            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(lambda _: execute(), range(2)))
            self.assertEqual(results[0], results[1])
            store = SQLiteDemoStore(path)
            try:
                rows = store.connection.execute(
                    "SELECT COUNT(*) AS count FROM servicepilot_idempotency"
                ).fetchone()
                self.assertEqual(int(rows["count"]), 1)
            finally:
                store.close()

    def test_other_thread_cannot_reuse_confirmation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "graph.sqlite"
            first = GraphServiceSession(
                "U001", RuleBasedToolCaller(), thread_id="original", sqlite_path=path
            )
            first.handle("O1002 商品坏了，帮我退款")
            first.close()
            other = GraphServiceSession(
                "U001", RuleBasedToolCaller(), thread_id="other", sqlite_path=path
            )
            try:
                turn = other.handle("确认")
                self.assertEqual(turn.status, "clarify")
            finally:
                other.close()

    def test_other_user_cannot_resume_same_thread(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "graph.sqlite"
            first = GraphServiceSession(
                "U001", RuleBasedToolCaller(), thread_id="shared", sqlite_path=path
            )
            first.handle("O1002 商品坏了，帮我退款")
            first.close()
            attacker = GraphServiceSession(
                "U002", RuleBasedToolCaller(), thread_id="shared", sqlite_path=path
            )
            try:
                turn = attacker.handle("确认")
                self.assertEqual(turn.status, "blocked")
                self.assertEqual(turn.error, "CROSS_USER_CONFIRMATION")
            finally:
                attacker.close()


if __name__ == "__main__":
    unittest.main()
