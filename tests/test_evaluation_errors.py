import unittest

from servicepilot.evaluation import evaluate


class BrokenCaller:
    def decide(self, text, context):
        raise ValueError("bad json")


class EvaluationErrorTests(unittest.TestCase):
    def test_caller_error_is_recorded_not_raised(self):
        report = evaluate(
            BrokenCaller(),
            [{"case_id": "bad", "text": "x", "user_id": "U001", "expected_action": "respond"}],
        )
        self.assertEqual(report["metrics"]["task_success_rate"], 0.0)
        self.assertEqual(report["cases"][0]["predicted"]["action"], "error")


if __name__ == "__main__":
    unittest.main()
