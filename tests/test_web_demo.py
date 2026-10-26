import unittest

from servicepilot.baseline import RuleBasedToolCaller
from servicepilot.web_demo import PAGE, DemoState


class WebDemoTests(unittest.TestCase):
    def test_page_contains_demo_controls(self):
        self.assertIn("ServicePilot", PAGE)
        self.assertIn("申请退款", PAGE)
        self.assertIn("执行轨迹", PAGE)

    def test_demo_state_refund_flow(self):
        state = DemoState(RuleBasedToolCaller())
        first = state.message("O1002 商品坏了，帮我退款")
        self.assertEqual(first["turn"]["status"], "confirmation_required")
        second = state.message("确认")
        self.assertEqual(second["turn"]["status"], "executed")
        self.assertEqual(second["orders"]["O1002"]["status"], "refund_requested")


if __name__ == "__main__":
    unittest.main()
