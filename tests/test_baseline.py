import unittest

from servicepilot.baseline import RuleBasedToolCaller
from servicepilot.schemas import ExecutionContext


class RuleBasedToolCallerTests(unittest.TestCase):
    def setUp(self):
        self.caller = RuleBasedToolCaller()

    def test_selects_tracking_and_extracts_order(self):
        decision = self.caller.decide("o1001 的快递到哪了", ExecutionContext("U001"))
        self.assertEqual(decision.action, "call")
        self.assertEqual(decision.tool_call.name, "track_package")
        self.assertEqual(decision.tool_call.arguments, {"order_id": "O1001"})

    def test_refund_without_confirmation_is_clarification(self):
        decision = self.caller.decide("O1002 坏了，退款", ExecutionContext("U001"))
        self.assertEqual(decision.action, "clarify")

    def test_confirmed_refund_emits_enum_reason(self):
        decision = self.caller.decide("O1002 坏了，退款", ExecutionContext("U001", confirmed=True))
        self.assertEqual(decision.tool_call.arguments["reason"], "damaged")


if __name__ == "__main__":
    unittest.main()
