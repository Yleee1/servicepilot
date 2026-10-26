from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from threading import Lock

from .schemas import ToolResult


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

    def get_order(self, order_id: str) -> Order | None:
        return self.orders.get(order_id)

    def get_inventory(self, sku_id: str) -> int | None:
        return self.inventory.get(sku_id)

    def create_refund(self, order: Order) -> str:
        self._refund_counter += 1
        order.refund_id = f"R{self._refund_counter}"
        order.status = "refund_requested"
        return order.refund_id

    def execute_refund_once(
        self,
        *,
        order_id: str,
        user_id: str,
        idempotency_key: str,
    ) -> ToolResult | None:
        """In-memory stores use ToolRegistry's process-local serialization."""

        return None


class SQLiteDemoStore:
    """Durable local business store with transactional mutation idempotency.

    LangGraph checkpoints and business data deliberately use different SQLite
    connections. ``BEGIN IMMEDIATE`` plus the unique idempotency key makes two
    workers racing the same approval converge on the first committed result.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(
            self.path, timeout=30, check_same_thread=False, isolation_level=None
        )
        self.connection.row_factory = sqlite3.Row
        self._lock = Lock()
        self._initialize()

    def close(self) -> None:
        self.connection.close()

    def _initialize(self) -> None:
        self.connection.execute("PRAGMA busy_timeout=30000")
        try:
            self.connection.execute("PRAGMA journal_mode=WAL")
        except sqlite3.OperationalError as exc:
            # A second worker may connect while the first one is switching the
            # database to WAL. Schema creation below still honours busy_timeout.
            if "locked" not in str(exc).lower():
                raise
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS servicepilot_orders (
                order_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                sku_id TEXT NOT NULL,
                amount REAL NOT NULL,
                status TEXT NOT NULL,
                tracking_no TEXT NOT NULL DEFAULT '',
                refund_id TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS servicepilot_inventory (
                sku_id TEXT PRIMARY KEY,
                available INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS servicepilot_idempotency (
                idempotency_key TEXT PRIMARY KEY,
                operation TEXT NOT NULL,
                result_json TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS servicepilot_counters (
                name TEXT PRIMARY KEY,
                value INTEGER NOT NULL
            );
            """
        )
        orders = (
            ("O1001", "U001", "SKU-RED-42", 299.0, "shipped", "SF123456", ""),
            ("O1002", "U001", "SKU-BLUE-40", 159.0, "delivered", "YT888888", ""),
            ("O2001", "U002", "SKU-RED-42", 299.0, "paid", "", ""),
            ("O3001", "U003", "SKU-GREEN-38", 99.0, "refunded", "", "R3001"),
        )
        inventory = (("SKU-RED-42", 8), ("SKU-BLUE-40", 0), ("SKU-GREEN-38", 21))
        self.connection.executemany(
            """INSERT OR IGNORE INTO servicepilot_orders
               (order_id, user_id, sku_id, amount, status, tracking_no, refund_id)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            orders,
        )
        self.connection.executemany(
            "INSERT OR IGNORE INTO servicepilot_inventory (sku_id, available) VALUES (?, ?)",
            inventory,
        )
        self.connection.execute(
            "INSERT OR IGNORE INTO servicepilot_counters (name, value) VALUES ('refund', 4000)"
        )

    @staticmethod
    def _to_order(row: sqlite3.Row | None) -> Order | None:
        if row is None:
            return None
        return Order(
            order_id=str(row["order_id"]),
            user_id=str(row["user_id"]),
            sku_id=str(row["sku_id"]),
            amount=float(row["amount"]),
            status=str(row["status"]),
            tracking_no=str(row["tracking_no"]),
            refund_id=str(row["refund_id"]),
        )

    def get_order(self, order_id: str) -> Order | None:
        row = self.connection.execute(
            "SELECT * FROM servicepilot_orders WHERE order_id = ?", (order_id,)
        ).fetchone()
        return self._to_order(row)

    @property
    def orders(self) -> dict[str, Order]:
        rows = self.connection.execute("SELECT * FROM servicepilot_orders").fetchall()
        return {str(row["order_id"]): self._to_order(row) for row in rows}

    def get_inventory(self, sku_id: str) -> int | None:
        row = self.connection.execute(
            "SELECT available FROM servicepilot_inventory WHERE sku_id = ?", (sku_id,)
        ).fetchone()
        return int(row["available"]) if row is not None else None

    @property
    def inventory(self) -> dict[str, int]:
        rows = self.connection.execute("SELECT sku_id, available FROM servicepilot_inventory").fetchall()
        return {str(row["sku_id"]): int(row["available"]) for row in rows}

    def create_refund(self, order: Order) -> str:
        """Non-idempotent helper retained for the ToolRegistry protocol."""

        with self._lock:
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                refund_id = self._next_refund_id()
                self.connection.execute(
                    "UPDATE servicepilot_orders SET status = 'refund_requested', refund_id = ? "
                    "WHERE order_id = ?",
                    (refund_id, order.order_id),
                )
                self.connection.execute("COMMIT")
                return refund_id
            except Exception:
                self.connection.execute("ROLLBACK")
                raise

    def _next_refund_id(self) -> str:
        self.connection.execute(
            "UPDATE servicepilot_counters SET value = value + 1 WHERE name = 'refund'"
        )
        row = self.connection.execute(
            "SELECT value FROM servicepilot_counters WHERE name = 'refund'"
        ).fetchone()
        return f"R{int(row['value'])}"

    @staticmethod
    def _serialize_result(result: ToolResult) -> str:
        return json.dumps(
            {
                "ok": result.ok,
                "code": result.code,
                "data": result.data,
                "message": result.message,
            },
            ensure_ascii=False,
            sort_keys=True,
        )

    @staticmethod
    def _deserialize_result(payload: str) -> ToolResult:
        value = json.loads(payload)
        return ToolResult(
            bool(value["ok"]),
            str(value["code"]),
            dict(value.get("data", {})),
            str(value.get("message", "")),
        )

    def execute_refund_once(
        self,
        *,
        order_id: str,
        user_id: str,
        idempotency_key: str,
    ) -> ToolResult:
        with self._lock:
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                cached = self.connection.execute(
                    "SELECT result_json FROM servicepilot_idempotency WHERE idempotency_key = ?",
                    (idempotency_key,),
                ).fetchone()
                if cached is not None:
                    result = self._deserialize_result(str(cached["result_json"]))
                    self.connection.execute("COMMIT")
                    return result

                row = self.connection.execute(
                    "SELECT * FROM servicepilot_orders WHERE order_id = ?", (order_id,)
                ).fetchone()
                if row is None:
                    result = ToolResult(False, "ORDER_NOT_FOUND", message="订单不存在")
                elif str(row["user_id"]) != user_id:
                    result = ToolResult(False, "FORBIDDEN", message="无权访问其他用户订单")
                elif str(row["status"]) not in {"paid", "shipped", "delivered"}:
                    result = ToolResult(
                        False,
                        "REFUND_NOT_ALLOWED",
                        message=f"订单状态{row['status']}不可退款",
                    )
                else:
                    refund_id = self._next_refund_id()
                    self.connection.execute(
                        "UPDATE servicepilot_orders SET status = 'refund_requested', refund_id = ? "
                        "WHERE order_id = ?",
                        (refund_id, order_id),
                    )
                    result = ToolResult(
                        True,
                        "OK",
                        {"refund_id": refund_id, "status": "refund_requested"},
                    )
                self.connection.execute(
                    "INSERT INTO servicepilot_idempotency "
                    "(idempotency_key, operation, result_json) VALUES (?, 'create_refund', ?)",
                    (idempotency_key, self._serialize_result(result)),
                )
                self.connection.execute("COMMIT")
                return result
            except Exception:
                self.connection.execute("ROLLBACK")
                raise
