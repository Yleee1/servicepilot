import unittest

from servicepilot.baseline import RuleBasedToolCaller
from servicepilot.session import ProposalCaller
from servicepilot.session_evaluation import evaluate_sessions


class SessionEvaluationTests(unittest.TestCase):
    def test_exact_refund_scenario(self):
        report = evaluate_sessions(
            ProposalCaller(RuleBasedToolCaller()),
            [{
                "case_id": "refund",
                "user_id": "U001",
                "mutation_scenario": True,
                "messages": ["O1002 商品坏了，退款", "确认"],
                "expected_turns": [
                    {"status": "confirmation_required"},
                    {"status": "executed", "result_code": "OK"},
                ],
                "expected_order": {"order_id": "O1002", "status": "refund_requested"},
            }],
        )
        self.assertEqual(report["metrics"]["scenario_success_rate"], 1.0)
        self.assertEqual(report["metrics"]["mutation_scenario_success_rate"], 1.0)


if __name__ == "__main__":
    unittest.main()
