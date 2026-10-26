from __future__ import annotations


def route_supervisor(state: dict) -> str:
    value = state.get("active_agent", "respond")
    return value if value in {"order", "inventory", "refund", "verifier"} else "respond"


def route_next_step(state: dict) -> str:
    value = state.get("next_step", "respond")
    return value if value in {"execute", "approval", "supervisor", "refund", "verifier"} else "respond"


def route_after_verifier(state: dict) -> str:
    return "supervisor" if state.get("next_step") == "supervisor" else "memory_write"
