from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from ..store import DemoStore
from ..tools import ToolRegistry
from .nodes import GraphNodes
from .routing import route_after_verifier, route_next_step, route_supervisor
from .state import ServiceGraphState


def build_service_graph(
    caller,
    *,
    registry: ToolRegistry | None = None,
    checkpointer=None,
    max_handoffs: int = 6,
    approval_ttl_seconds: float = 900.0,
    verifier_enabled: bool = True,
    memory_service=None,
):
    nodes = GraphNodes(
        caller,
        registry or ToolRegistry(DemoStore()),
        max_handoffs=max_handoffs,
        approval_ttl_seconds=approval_ttl_seconds,
        verifier_enabled=verifier_enabled,
        memory_service=memory_service,
    )
    workflow = StateGraph(ServiceGraphState)
    workflow.add_node("ingress", nodes.ingress)
    workflow.add_node("supervisor", nodes.supervisor)
    workflow.add_node("memory_read", nodes.memory_read)
    workflow.add_node("memory_write", nodes.memory_write)
    workflow.add_node("order", nodes.order)
    workflow.add_node("inventory", nodes.inventory)
    workflow.add_node("refund", nodes.refund)
    workflow.add_node("policy_gate", nodes.policy_gate)
    workflow.add_node("approval", nodes.approval)
    workflow.add_node("execute", nodes.execute)
    workflow.add_node("verifier", nodes.verifier)
    workflow.add_node("respond", nodes.respond)

    workflow.add_edge(START, "ingress")
    workflow.add_edge("ingress", "memory_read")
    workflow.add_edge("memory_read", "supervisor")
    workflow.add_conditional_edges(
        "supervisor",
        route_supervisor,
        {"order": "order", "inventory": "inventory", "refund": "refund", "verifier": "verifier", "respond": "respond"},
    )
    workflow.add_edge("order", "policy_gate")
    workflow.add_edge("inventory", "policy_gate")
    workflow.add_edge("refund", "policy_gate")
    workflow.add_conditional_edges(
        "policy_gate",
        route_next_step,
        {"execute": "execute", "approval": "approval", "supervisor": "supervisor", "refund": "refund", "verifier": "verifier", "respond": "respond"},
    )
    workflow.add_conditional_edges(
        "approval",
        route_next_step,
        {"execute": "execute", "approval": "approval", "supervisor": "supervisor", "refund": "refund", "verifier": "verifier", "respond": "respond"},
    )
    workflow.add_conditional_edges(
        "execute",
        route_next_step,
        {"execute": "execute", "approval": "approval", "supervisor": "supervisor", "refund": "refund", "verifier": "verifier", "respond": "respond"},
    )
    workflow.add_conditional_edges(
        "verifier", route_after_verifier, {"supervisor": "supervisor", "memory_write": "memory_write"}
    )
    workflow.add_edge("memory_write", "respond")
    workflow.add_edge("respond", END)
    return workflow.compile(checkpointer=checkpointer)
