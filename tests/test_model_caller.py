import unittest

from servicepilot.model_caller import ModelToolCaller
from servicepilot.schemas import ExecutionContext


class FakeClient:
    def __init__(self, response):
        self.response = response

    def chat_json(self, system, user):
        self.last_user = user
        return self.response


class ModelToolCallerTests(unittest.TestCase):
    def test_parses_tool_call(self):
        caller = ModelToolCaller(
            FakeClient({"action": "call", "tool": "get_order", "arguments": {"order_id": "O1001"}})
        )
        decision = caller.decide("查订单", ExecutionContext("U001"))
        self.assertEqual(decision.tool_call.name, "get_order")

    def test_invalid_call_shape_fails_closed(self):
        caller = ModelToolCaller(FakeClient({"action": "call", "tool": "get_order"}))
        decision = caller.decide("查订单", ExecutionContext("U001"))
        self.assertEqual(decision.action, "respond")

    def test_trace_is_forwarded_to_model(self):
        client = FakeClient({"action": "respond", "message": "done"})
        caller = ModelToolCaller(client)
        caller.decide_with_trace(
            "退款", ExecutionContext("U001"), [{"tool": "get_refund_eligibility", "result": {}}]
        )
        self.assertIn("tool_observations", client.last_user)


if __name__ == "__main__":
    unittest.main()
