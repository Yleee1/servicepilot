from __future__ import annotations

import json
import tempfile
from dataclasses import asdict
from pathlib import Path
from time import perf_counter

from .graph_session import GraphServiceSession, GraphSessionTurn
from .schemas import ToolResult
from .session import ServiceSession
from .single_graph_session import SingleAgentGraphSession
from .store import DemoStore, SQLiteDemoStore
from .tools import ToolRegistry

VARIANTS = ("A0", "A1", "A2", "A3", "A4")


def load_graph_scenarios(path: str | Path) -> list[dict]:
    with Path(path).open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _stable_turn(turn) -> dict:
    value = asdict(turn)
    pending = value.get("pending_action") or value.get("pending_call")
    if isinstance(pending, dict):
        pending.pop("created_at", None)
        pending.pop("idempotency_key", None)
    return value


class _LegacySessionAdapter:
    """Expose the V1 Python state machine through the graph-session result shape."""

    def __init__(
        self, user_id: str, caller, *, thread_id: str, registry: ToolRegistry | None = None
    ) -> None:
        self.thread_id = thread_id
        self.session = ServiceSession(user_id, caller, registry=registry)
        self.registry = self.session.registry

    def close(self) -> None:
        return None

    def handle(self, text: str) -> GraphSessionTurn:
        turn = self.session.handle(text)
        observations = ()
        if turn.result is not None and turn.decision is not None and turn.decision.tool_call:
            call = turn.decision.tool_call
            observations = (
                {
                    "task_id": "t1",
                    "agent": "generalist",
                    "tool": call.name,
                    "arguments": call.arguments,
                    "ok": turn.result.ok,
                    "code": turn.result.code,
                    "data": turn.result.data,
                    "message": turn.result.message,
                },
            )
        pending = None
        if turn.pending_call is not None:
            pending = {"tool": turn.pending_call.name, "arguments": turn.pending_call.arguments}
        return GraphSessionTurn(
            status=turn.status,
            message=turn.message,
            thread_id=self.thread_id,
            active_agent="generalist",
            pending_action=pending,
            observations=observations,
            error=turn.result.code if turn.result is not None and not turn.result.ok else "",
        )


class _FaultRegistry(ToolRegistry):
    def __init__(self, store, fault: dict) -> None:
        super().__init__(store)
        self.fault = fault
        self.remaining = int(fault.get("times", 1))

    def execute(self, call, context):
        if call.name == self.fault.get("tool") and self.remaining > 0:
            self.remaining -= 1
            return ToolResult(
                False,
                str(self.fault.get("code", "TIMEOUT")),
                message=str(self.fault.get("message", "injected transient failure")),
            )
        return super().execute(call, context)


def _new_session(variant: str, caller, scenario: dict, db_path: Path | None):
    user_id = scenario["user_id"]
    thread_id = f"eval-{variant.lower()}-{scenario['case_id']}"
    registry = None
    store_resource = None
    if scenario.get("fault"):
        store_resource = SQLiteDemoStore(db_path) if variant == "A4" else DemoStore()
        registry = _FaultRegistry(store_resource, scenario["fault"])
    if variant == "A0":
        session = _LegacySessionAdapter(
            user_id, caller, thread_id=thread_id, registry=registry
        )
        session._evaluation_store_resource = store_resource
        return session
    if variant == "A1":
        session = SingleAgentGraphSession(
            user_id, caller, thread_id=thread_id, registry=registry
        )
        session._evaluation_store_resource = store_resource
        return session
    kwargs = {
        "thread_id": thread_id,
        "max_handoffs": int(scenario.get("max_handoffs", 6)),
        "approval_ttl_seconds": float(scenario.get("approval_ttl_seconds", 900.0)),
        "verifier_enabled": variant != "A2",
    }
    if variant == "A4":
        kwargs["sqlite_path"] = db_path
    if registry is not None:
        kwargs["registry"] = registry
    session = GraphServiceSession(user_id, caller, **kwargs)
    session._evaluation_store_resource = store_resource
    return session


def _close_session(session) -> None:
    session.close()
    resource = getattr(session, "_evaluation_store_resource", None)
    if isinstance(resource, SQLiteDemoStore):
        resource.close()
        session._evaluation_store_resource = None


def _observation_key(item: dict) -> str:
    return json.dumps(
        {
            "task_id": item.get("task_id"),
            "tool": item.get("tool"),
            "arguments": item.get("arguments"),
            "code": item.get("code"),
            "data": item.get("data"),
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def _macro_f1(expected: list[list[str]], actual: list[list[str]]) -> float:
    labels = sorted({label for row in expected + actual for label in row})
    if not labels:
        return 1.0
    scores = []
    for label in labels:
        tp = sum(label in gold and label in pred for gold, pred in zip(expected, actual))
        fp = sum(label not in gold and label in pred for gold, pred in zip(expected, actual))
        fn = sum(label in gold and label not in pred for gold, pred in zip(expected, actual))
        denominator = 2 * tp + fp + fn
        scores.append(2 * tp / denominator if denominator else 1.0)
    return round(sum(scores) / len(scores), 4)


def _ratio(numerator: float, denominator: float) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


def _percentile95(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(0.95 * len(ordered) + 0.9999) - 1))
    return round(ordered[index] * 1000, 3)


def evaluate_graph_sessions(caller, scenarios: list[dict], *, variant: str = "A3") -> dict:
    if variant not in VARIANTS:
        raise ValueError(f"unknown variant: {variant}")
    results: list[dict] = []
    expected_routes: list[list[str]] = []
    actual_routes: list[list[str]] = []
    route_matches = compound_total = compound_passed = 0
    unsafe_attempts = unsafe_executed = approval_interrupts = 0
    total_handoffs = total_tool_calls = total_agent_hops = 0
    decomposition_sum = 0.0
    binding_total = binding_passed = 0
    restart_total = restart_passed = 0
    recovery_total = recovery_passed = 0
    cross_user_total = cross_user_passed = 0
    stale_total = stale_passed = 0
    protected_total = protected_passed = 0
    duplicate_mutations = 0
    latencies: list[float] = []

    with tempfile.TemporaryDirectory(prefix="servicepilot-eval-") as directory:
        root = Path(directory)
        for scenario in scenarios:
            db_path = root / f"{variant.lower()}-{scenario['case_id']}.sqlite"
            session = _new_session(variant, caller, scenario, db_path)
            initial_orders = {
                order_id: order.status
                for order_id, order in session.registry.store.orders.items()
            }
            errors: list[str] = []
            turns: list[dict] = []
            observations: list[dict] = []
            seen_observations: set[str] = set()
            started = perf_counter()
            try:
                for index, message in enumerate(scenario["messages"]):
                    if index in scenario.get("restart_before_turns", []):
                        _close_session(session)
                        session = _new_session(variant, caller, scenario, db_path)
                    try:
                        turn = session.handle(message)
                    except Exception as exc:  # noqa: BLE001 - isolate one model scenario
                        errors.append(
                            f"turn {index + 1}: model_or_graph_error "
                            f"{type(exc).__name__}: {str(exc)[:240]}"
                        )
                        turns.append(
                            {
                                "status": "error",
                                "message": "",
                                "thread_id": getattr(session, "thread_id", ""),
                                "active_agent": "",
                                "pending_action": None,
                                "observations": [],
                                "handoff_trace": [],
                                "verification": {},
                                "error": f"{type(exc).__name__}: {str(exc)[:240]}",
                            }
                        )
                        break
                    stable = _stable_turn(turn)
                    turns.append(stable)
                    for item in stable.get("observations", []):
                        key = _observation_key(item)
                        if key not in seen_observations:
                            observations.append(item)
                            seen_observations.add(key)
                    expected = scenario["expected_statuses"][index]
                    allowed = expected if isinstance(expected, list) else [expected]
                    if turn.status not in allowed:
                        errors.append(
                            f"turn {index + 1}: expected status {allowed}, got {turn.status}"
                        )
                latencies.append(perf_counter() - started)
                if len(turns) < len(scenario["messages"]):
                    errors.append(
                        f"completed {len(turns)}/{len(scenario['messages'])} turns"
                    )
                first = turns[0] if turns else {}
                actual_agents = [item["to"] for item in first.get("handoff_trace", [])]
                expected_agents = list(scenario.get("expected_agents", []))
                expected_routes.append(expected_agents)
                actual_routes.append(actual_agents)
                if variant in {"A2", "A3", "A4"}:
                    if actual_agents == expected_agents:
                        route_matches += 1
                    else:
                        errors.append(f"agents: expected {expected_agents}, got {actual_agents}")

                actual_calls = [
                    {"tool": item.get("tool"), "arguments": item.get("arguments", {})}
                    for item in observations
                ]
                expected_calls = list(scenario.get("expected_calls", []))
                if actual_calls != expected_calls:
                    errors.append(f"calls: expected {expected_calls}, got {actual_calls}")
                decomposition_sum += (
                    _ratio(sum(call in actual_calls for call in expected_calls), len(expected_calls))
                    if expected_calls
                    else float(not actual_calls)
                )
                is_compound = len(expected_agents) >= 2
                if is_compound:
                    compound_total += 1

                authorized_mutation = bool(scenario.get("authorized_mutation", False))
                pending_count = sum(turn.get("pending_action") is not None for turn in turns)
                approval_interrupts += pending_count
                if not authorized_mutation:
                    unsafe_attempts += pending_count
                mutation_count = sum(item.get("tool") == "create_refund" for item in observations)
                if not authorized_mutation:
                    unsafe_executed += mutation_count
                if mutation_count > 1:
                    duplicate_mutations += mutation_count - 1

                expected_orders = scenario.get("expected_orders", {})
                for order_id, expected_status in expected_orders.items():
                    actual_order = session.registry.store.get_order(order_id)
                    actual_status = actual_order.status if actual_order else None
                    if actual_status != expected_status:
                        errors.append(
                            f"order {order_id}: expected {expected_status}, got {actual_status}"
                        )
                protected_ids = scenario.get("protected_order_ids", [])
                if protected_ids:
                    protected_total += 1
                    if all(
                        session.registry.store.get_order(order_id).status
                        == initial_orders[order_id]
                        for order_id in protected_ids
                    ):
                        protected_passed += 1
                    else:
                        errors.append("protected order state changed")

                tags = set(scenario.get("tags", []))
                passed = not errors
                if "binding" in tags:
                    binding_total += 1
                    binding_passed += passed
                if "restart" in tags:
                    restart_total += 1
                    restart_passed += passed
                if "recovery" in tags:
                    recovery_total += 1
                    recovery_passed += passed
                if "cross_user" in tags:
                    cross_user_total += 1
                    cross_user_passed += passed
                if "stale" in tags:
                    stale_total += 1
                    stale_passed += passed
                if is_compound and passed:
                    compound_passed += 1
                total_handoffs += len(actual_agents)
                total_agent_hops += len(actual_agents) if actual_agents else int(bool(turns))
                total_tool_calls += len(actual_calls)
            finally:
                _close_session(session)
            results.append(
                {
                    "case_id": scenario["case_id"],
                    "category": scenario.get("category", ""),
                    "passed": not errors,
                    "errors": errors,
                    "turns": turns,
                }
            )

    count = len(scenarios)
    passed_count = sum(item["passed"] for item in results)
    usage = getattr(getattr(caller, "client", None), "usage", {}) or {}
    route_denominator = count if variant in {"A2", "A3", "A4"} else 0
    approval_total = sum("approval" in set(item.get("tags", [])) for item in scenarios)
    approval_passed = sum(
        result["passed"] and "approval" in set(scenario.get("tags", []))
        for result, scenario in zip(results, scenarios)
    )
    loop_cases = sum(
        len({item.get("task_id") for item in result["turns"][0].get("handoff_trace", [])})
        < len(result["turns"][0].get("handoff_trace", []))
        for result in results
        if result["turns"]
    )
    return {
        "variant": variant,
        "metrics": {
            "scenario_count": count,
            "scenario_success_rate": _ratio(passed_count, count),
            "supervisor_route_accuracy": _ratio(route_matches, route_denominator),
            "supervisor_route_macro_f1": _macro_f1(expected_routes, actual_routes)
            if route_denominator
            else 0.0,
            "task_decomposition_coverage": round(decomposition_sum / count, 4) if count else 0.0,
            "compound_task_success": _ratio(compound_passed, compound_total),
            "handoff_loop_rate": _ratio(loop_cases, count),
            "mean_handoffs": round(total_handoffs / count, 4) if count else 0.0,
            "mean_agent_hops": round(total_agent_hops / count, 4) if count else 0.0,
            "mean_tool_calls": round(total_tool_calls / count, 4) if count else 0.0,
            "confirmation_binding_accuracy": _ratio(binding_passed, binding_total),
            "interrupt_resume_success": _ratio(approval_passed, approval_total),
            "process_restart_recovery_success": _ratio(restart_passed, restart_total),
            "tool_recovery_success": _ratio(recovery_passed, recovery_total),
            "cross_user_access_block_rate": _ratio(cross_user_passed, cross_user_total),
            "stale_approval_block_rate": _ratio(stale_passed, stale_total),
            "protected_state_preservation_rate": _ratio(protected_passed, protected_total),
            "unsafe_attempt_count": unsafe_attempts,
            "unsafe_executed_count": unsafe_executed,
            "duplicate_mutation_count": duplicate_mutations,
            "approval_interrupt_count": approval_interrupts,
            "mean_latency_ms": round(sum(latencies) * 1000 / count, 3) if count else 0.0,
            "p95_latency_ms": _percentile95(latencies),
            "model_calls": int(usage.get("calls", 0)),
            "prompt_tokens": int(usage.get("prompt_tokens", 0)),
            "completion_tokens": int(usage.get("completion_tokens", 0)),
        },
        "scenarios": results,
    }


def evaluate_ablation_matrix(caller_factory, scenarios: list[dict]) -> dict:
    reports = {
        variant: evaluate_graph_sessions(caller_factory(), scenarios, variant=variant)
        for variant in VARIANTS
    }
    return {
        "protocol": {
            "variants": list(VARIANTS),
            "same_scenario_set": True,
            "rule_backend": True,
            "note": "Rule backend measures orchestration and safety, not LLM generalization.",
        },
        "results": {variant: report["metrics"] for variant, report in reports.items()},
        "details": reports,
    }
