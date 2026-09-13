from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Order:
    order_id: str
    user_id: str
    sku_id: str
    amount: float
    status: str
    tracking_no: str = ""
    refund_id: str = ""


class DemoStore:
    def __init__(self) -> None:
        self.orders = {
            "O1001": Order("O1001", "U001", "SKU-RED-42", 299.0, "shipped", "SF123456"),
            "O1002": Order("O1002", "U001", "SKU-BLUE-40", 159.0, "delivered", "YT888888"),
            "O2001": Order("O2001", "U002", "SKU-RED-42", 299.0, "paid"),
            "O3001": Order("O3001", "U003", "SKU-GREEN-38", 99.0, "refunded", "", "R3001"),
        }
        self.inventory = {
            "SKU-RED-42": 8,
            "SKU-BLUE-40": 0,
            "SKU-GREEN-38": 21,
        }
        self._refund_counter = 4000

    def create_refund(self, order: Order) -> str:
        self._refund_counter += 1
        order.refund_id = f"R{self._refund_counter}"
        order.status = "refund_requested"
        return order.refund_id
