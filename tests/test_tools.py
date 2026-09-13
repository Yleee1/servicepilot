import unittest

from servicepilot.schemas import ExecutionContext, ToolCall
from servicepilot.tools import ToolRegistry


class ToolRegistryTests(unittest.TestCase):
    def setUp(self):
        self.registry = ToolRegistry()

    def test_reads_owned_order(self):
        result = self.registry.execute(ToolCall("get_order", {"order_id": "O1001"}), ExecutionContext("U001"))
        self.assertTrue(result.ok)
        self.assertEqual(result.data["status"], "shipped")

    def test_blocks_cross_user_access(self):
        result = self.registry.execute(ToolCall("get_order", {"order_id": "O2001"}), ExecutionContext("U001"))
        self.assertFalse(result.ok)
        self.assertEqual(result.code, "FORBIDDEN")

    def test_rejects_invalid_arguments_before_execution(self):
        result = self.registry.execute(
            ToolCall("get_order", {"order_id": "bad", "extra": True}), ExecutionContext("U001")
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.code, "INVALID_ARGUMENTS")

    def test_mutation_requires_confirmation(self):
        result = self.registry.execute(
            ToolCall("create_refund", {"order_id": "O1002", "reason": "damaged"}),
            ExecutionContext("U001", confirmed=False),
        )
        self.assertEqual(result.code, "CONFIRMATION_REQUIRED")
        self.assertEqual(self.registry.store.orders["O1002"].status, "delivered")

    def test_confirmed_refund_changes_state_once(self):
        call = ToolCall("create_refund", {"order_id": "O1002", "reason": "damaged"})
        context = ExecutionContext("U001", confirmed=True, confirmed_call=call)
        first = self.registry.execute(call, context)
        second = self.registry.execute(call, context)
        self.assertTrue(first.ok)
        self.assertEqual(first.data["status"], "refund_requested")
        self.assertEqual(second.code, "REFUND_NOT_ALLOWED")

    def test_confirmation_is_bound_to_exact_arguments(self):
        confirmed = ToolCall("create_refund", {"order_id": "O1002", "reason": "damaged"})
        changed = ToolCall("create_refund", {"order_id": "O1002", "reason": "wrong_item"})
        result = self.registry.execute(
            changed, ExecutionContext("U001", confirmed=True, confirmed_call=confirmed)
        )
        self.assertEqual(result.code, "CONFIRMATION_MISMATCH")
        self.assertEqual(self.registry.store.orders["O1002"].status, "delivered")

    def test_inventory_can_report_zero(self):
        result = self.registry.execute(
            ToolCall("check_inventory", {"sku_id": "SKU-BLUE-40"}), ExecutionContext("U001")
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.data["available"], 0)


if __name__ == "__main__":
    unittest.main()
