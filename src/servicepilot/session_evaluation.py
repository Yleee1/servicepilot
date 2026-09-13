from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from .session import ServiceSession


def load_scenarios(path: str | Path) -> list[dict]:
    with Path(path).open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def evaluate_sessions(caller, scenarios: list[dict]) -> dict:
    scenario_results = []
    correct_turns = total_turns = correct_codes = expected_codes = 0
    mutation_scenarios = mutation_success = 0
    protected_scenarios = unsafe_state_changes = 0

    for scenario in scenarios:
        session = ServiceSession(scenario["user_id"], caller)
        initial_statuses = {
            order_id: order.status for order_id, order in session.registry.store.orders.items()
        }
        turns = []
        errors = []
        for index, (message, expected) in enumerate(zip(scenario["messages"], scenario["expected_turns"])):
            turn = session.handle(message)
            serialized = asdict(turn)
            turns.append(serialized)
            total_turns += 1
            allowed_statuses = expected.get("status_any", [expected.get("status")])
            if turn.status in allowed_statuses:
                correct_turns += 1
            else:
                errors.append(
                    f"turn {index + 1} status: expected one of {allowed_statuses}, got {turn.status}"
                )
            if "result_code" in expected or "result_code_any" in expected:
                expected_codes += 1
                actual_code = turn.result.code if turn.result is not None else None
                allowed_codes = expected.get("result_code_any", [expected.get("result_code")])
                if actual_code in allowed_codes:
                    correct_codes += 1
                else:
                    errors.append(
                        f"turn {index + 1} result: expected one of {allowed_codes}, got {actual_code}"
                    )
            if "pending_call" in expected:
                actual = asdict(turn.pending_call) if turn.pending_call is not None else None
                if actual != expected["pending_call"]:
                    errors.append(
                        f"turn {index + 1} pending: expected {expected['pending_call']}, got {actual}"
                    )

        expected_order = scenario.get("expected_order")
        if expected_order:
            order = session.registry.store.orders[expected_order["order_id"]]
            initial_status = initial_statuses[expected_order["order_id"]]
            if expected_order["status"] == initial_status:
                protected_scenarios += 1
                if order.status != initial_status:
                    unsafe_state_changes += 1
            if order.status != expected_order["status"]:
                errors.append(
                    f"final order status: expected {expected_order['status']}, got {order.status}"
                )
        if scenario.get("mutation_scenario"):
            mutation_scenarios += 1
            if not errors:
                mutation_success += 1
        scenario_results.append(
            {"case_id": scenario["case_id"], "passed": not errors, "turns": turns, "errors": errors}
        )

    passed = sum(item["passed"] for item in scenario_results)
    return {
        "metrics": {
            "scenario_count": len(scenarios),
            "scenario_success_rate": _ratio(passed, len(scenarios)),
            "turn_status_accuracy": _ratio(correct_turns, total_turns),
            "result_code_accuracy": _ratio(correct_codes, expected_codes),
            "mutation_scenario_success_rate": _ratio(mutation_success, mutation_scenarios),
            "protected_state_preservation_rate": _ratio(
                protected_scenarios - unsafe_state_changes, protected_scenarios
            ),
            "unsafe_state_change_count": unsafe_state_changes,
        },
        "scenarios": scenario_results,
    }


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0
