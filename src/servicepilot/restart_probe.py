from __future__ import annotations

import argparse
import json
from dataclasses import asdict

from .baseline import RuleBasedToolCaller
from .graph_session import GraphServiceSession


def main() -> None:
    parser = argparse.ArgumentParser(description="Cross-process ServicePilot recovery probe")
    parser.add_argument("--database", required=True)
    parser.add_argument("--thread-id", required=True)
    parser.add_argument("--user-id", default="U001")
    parser.add_argument("--message", required=True)
    args = parser.parse_args()
    session = GraphServiceSession(
        args.user_id,
        RuleBasedToolCaller(),
        thread_id=args.thread_id,
        sqlite_path=args.database,
    )
    try:
        turn = session.handle(args.message)
        order = session.registry.store.get_order("O1002")
        value = asdict(turn)
        value["order_status"] = order.status if order else None
        value["refund_id"] = order.refund_id if order else None
        pending = value.get("pending_action")
        if isinstance(pending, dict):
            pending.pop("created_at", None)
            pending.pop("idempotency_key", None)
        print(json.dumps(value, ensure_ascii=False))
    finally:
        session.close()


if __name__ == "__main__":
    main()
