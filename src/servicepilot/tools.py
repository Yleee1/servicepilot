from __future__ import annotations

from collections.abc import Callable

from .schemas import ExecutionContext, ToolCall, ToolDefinition, ToolResult
from .store import DemoStore
from .validation import ToolValidationError, validate_arguments


ORDER_ID = {"type": "string", "pattern": "^O[0-9]+$", "minLength": 2}
USER_ID = {"type": "string", "pattern": "^U[0-9]+$", "minLength": 2}


TOOL_DEFINITIONS = {
    "get_order": ToolDefinition(
        name="get_order",
        description="查询订单金额、商品和当前状态",
        parameters={
            "type": "object",
            "properties": {"order_id": ORDER_ID},
            "required": ["order_id"],
            "additionalProperties": False,
        },
    ),
    "track_package": ToolDefinition(
        name="track_package",
        description="查询已发货订单的物流单号和状态",
        parameters={
            "type": "object",
            "properties": {"order_id": ORDER_ID},
            "required": ["order_id"],
            "additionalProperties": False,
        },
    ),
    "check_inventory": ToolDefinition(
        name="check_inventory",
        description="查询SKU的可售库存",
        parameters={
            "type": "object",
            "properties": {"sku_id": {"type": "string", "minLength": 3}},
            "required": ["sku_id"],
            "additionalProperties": False,
        },
    ),
    "get_refund_eligibility": ToolDefinition(
        name="get_refund_eligibility",
        description="检查订单是否满足退款申请条件，不执行退款",
        parameters={
            "type": "object",
            "properties": {"order_id": ORDER_ID},
            "required": ["order_id"],
            "additionalProperties": False,
        },
    ),
    "create_refund": ToolDefinition(
        name="create_refund",
        description="在用户明确确认后创建退款申请",
        parameters={
            "type": "object",
            "properties": {
                "order_id": ORDER_ID,
                "reason": {
                    "type": "string",
                    "enum": ["not_received", "damaged", "wrong_item", "changed_mind"],
                },
            },
            "required": ["order_id", "reason"],
            "additionalProperties": False,
        },
        mutating=True,
    ),
}


class ToolRegistry:
    def __init__(self, store: DemoStore | None = None) -> None:
        self.store = store or DemoStore()
        self.handlers: dict[str, Callable[[dict, ExecutionContext], ToolResult]] = {
            "get_order": self._get_order,
            "track_package": self._track_package,
            "check_inventory": self._check_inventory,
            "get_refund_eligibility": self._get_refund_eligibility,
            "create_refund": self._create_refund,
        }

    def execute(self, call: ToolCall, context: ExecutionContext) -> ToolResult:
        definition = TOOL_DEFINITIONS.get(call.name)
        if definition is None:
            return ToolResult(False, "UNKNOWN_TOOL", message=f"unknown tool: {call.name}")
        try:
            validate_arguments(definition, call.arguments)
        except ToolValidationError as exc:
            return ToolResult(False, "INVALID_ARGUMENTS", message=str(exc))
        if definition.mutating:
            if not context.confirmed:
                return ToolResult(False, "CONFIRMATION_REQUIRED", message="用户尚未明确确认该操作")
            if context.confirmed_call != call:
                return ToolResult(
                    False,
                    "CONFIRMATION_MISMATCH",
                    message="确认凭证未绑定到当前工具名及参数",
                )
        return self.handlers[call.name](call.arguments, context)

    def _owned_order(self, order_id: str, context: ExecutionContext):
        order = self.store.orders.get(order_id)
        if order is None:
            return None, ToolResult(False, "ORDER_NOT_FOUND", message="订单不存在")
        if order.user_id != context.user_id:
            return None, ToolResult(False, "FORBIDDEN", message="无权访问其他用户订单")
        return order, None

    def _get_order(self, arguments: dict, context: ExecutionContext) -> ToolResult:
        order, error = self._owned_order(arguments["order_id"], context)
        if error:
            return error
        return ToolResult(
            True,
            "OK",
            {"order_id": order.order_id, "sku_id": order.sku_id, "amount": order.amount, "status": order.status},
        )

    def _track_package(self, arguments: dict, context: ExecutionContext) -> ToolResult:
        order, error = self._owned_order(arguments["order_id"], context)
        if error:
            return error
        if not order.tracking_no:
            return ToolResult(False, "NOT_SHIPPED", message="订单尚未发货")
        return ToolResult(True, "OK", {"tracking_no": order.tracking_no, "status": order.status})

    def _check_inventory(self, arguments: dict, context: ExecutionContext) -> ToolResult:
        sku_id = arguments["sku_id"]
        if sku_id not in self.store.inventory:
            return ToolResult(False, "SKU_NOT_FOUND", message="SKU不存在")
        return ToolResult(True, "OK", {"sku_id": sku_id, "available": self.store.inventory[sku_id]})

    def _get_refund_eligibility(self, arguments: dict, context: ExecutionContext) -> ToolResult:
        order, error = self._owned_order(arguments["order_id"], context)
        if error:
            return error
        eligible = order.status in {"paid", "shipped", "delivered"}
        return ToolResult(True, "OK", {"eligible": eligible, "status": order.status})

    def _create_refund(self, arguments: dict, context: ExecutionContext) -> ToolResult:
        order, error = self._owned_order(arguments["order_id"], context)
        if error:
            return error
        if order.status not in {"paid", "shipped", "delivered"}:
            return ToolResult(False, "REFUND_NOT_ALLOWED", message=f"订单状态{order.status}不可退款")
        refund_id = self.store.create_refund(order)
        return ToolResult(True, "OK", {"refund_id": refund_id, "status": order.status})
