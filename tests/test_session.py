import unittest

from servicepilot.baseline import RuleBasedToolCaller
from servicepilot.schemas import AgentDecision, ToolCall
from servicepilot.session import ProposalCaller, ServiceSession


class RecklessCaller:
    def decide(self, text, context):
        return AgentDecision(
            "call", ToolCall("create_refund", {"order_id": "O1002", "reason": "damaged"})
        )


class HallucinatedCaller:
    def decide(self, text, context):
        return AgentDecision(
            "call", ToolCall("create_refund", {"order_id": "O999999", "reason": "changed_mind"})
        )


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.session = ServiceSession("U001", ProposalCaller(RuleBasedToolCaller()))

    def test_refund_requires_second_turn_and_executes_exact_pending_call(self):
        first = self.session.handle("O1002 商品坏了，帮我退款")
        self.assertEqual(first.status, "confirmation_required")
        self.assertEqual(self.session.registry.store.orders["O1002"].status, "delivered")
        second = self.session.handle("确认")
        self.assertEqual(second.status, "executed")
        self.assertEqual(self.session.registry.store.orders["O1002"].status, "refund_requested")

    def test_cancel_discards_pending_call(self):
        self.session.handle("O1002 商品坏了，帮我退款")
        turn = self.session.handle("取消")
        self.assertEqual(turn.status, "cancelled")
        self.assertEqual(self.session.registry.store.orders["O1002"].status, "delivered")

    def test_new_request_invalidates_old_pending_call(self):
        self.session.handle("O1002 商品坏了，帮我退款")
        self.session.handle("帮我查订单 O1001")
        turn = self.session.handle("确认")
        self.assertEqual(turn.status, "clarify")
        self.assertEqual(self.session.registry.store.orders["O1002"].status, "delivered")

    def test_reckless_mutation_is_downgraded_to_pending(self):
        session = ServiceSession("U001", RecklessCaller())
        turn = session.handle("O1002 商品坏了，立即退款")
        self.assertEqual(turn.status, "confirmation_required")
        self.assertEqual(session.registry.store.orders["O1002"].status, "delivered")

    def test_hallucinated_refund_arguments_cannot_enter_pending(self):
        session = ServiceSession("U001", HallucinatedCaller())
        turn = session.handle("O1002 商品坏了，帮我退款")
        self.assertEqual(turn.status, "clarify")
        self.assertIsNone(session.pending_call)

    def test_wrong_reason_cannot_enter_pending(self):
        class WrongReasonCaller:
            def decide(self, text, context):
                return AgentDecision(
                    "call", ToolCall("create_refund", {"order_id": "O1002", "reason": "wrong_item"})
                )

        session = ServiceSession("U001", WrongReasonCaller())
        turn = session.handle("O1002 商品坏了，帮我退款")
        self.assertEqual(turn.status, "clarify")
        self.assertIsNone(session.pending_call)

    def test_read_tool_executes_without_confirmation(self):
        turn = self.session.handle("查一下订单 O1001")
        self.assertEqual(turn.status, "executed")
        self.assertEqual(turn.result.code, "OK")

    def test_confirmation_without_pending_call_does_not_execute(self):
        turn = self.session.handle("确认")
        self.assertEqual(turn.status, "clarify")


if __name__ == "__main__":
    unittest.main()
