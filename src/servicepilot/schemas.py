from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ToolCall:
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ToolResult:
    ok: bool
    code: str
    data: dict[str, Any] = field(default_factory=dict)
    message: str = ""


@dataclass(frozen=True)
class ExecutionContext:
    user_id: str
    confirmed: bool = False
    confirmed_call: ToolCall | None = None


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    parameters: dict[str, Any]
    mutating: bool = False


@dataclass(frozen=True)
class AgentDecision:
    """Agent 的结构化决策；只有 action=call 时才允许进入执行器。"""

    action: str
    tool_call: ToolCall | None = None
    message: str = ""
