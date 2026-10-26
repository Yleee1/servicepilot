from __future__ import annotations

import json
from dataclasses import dataclass

SUPERVISOR_SYSTEM_PROMPT = (
    "你是 Supervisor Agent，没有任何业务工具。只把请求拆成有依赖的任务并路由到"
    "order、inventory、refund。只输出JSON，不得声称执行成功。"
)


@dataclass(frozen=True)
class PlannedTask:
    task_id: str
    agent: str
    instruction: str
    depends_on: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "agent": self.agent,
            "instruction": self.instruction,
            "depends_on": list(self.depends_on),
        }


class SupervisorAgent:
    """Routes and decomposes requests; deliberately has no tool registry."""

    allowed_tools: tuple[str, ...] = ()

    def __init__(self, client=None) -> None:
        self.client = client

    def plan(self, request: str) -> list[dict]:
        if self.client is not None:
            value = self.client.chat_json(
                SUPERVISOR_SYSTEM_PROMPT,
                json.dumps(
                    {
                        "request": request,
                        "output_schema": {
                            "tasks": [
                                {
                                    "task_id": "t1",
                                    "agent": "order",
                                    "instruction": "...",
                                    "depends_on": [],
                                }
                            ]
                        },
                    },
                    ensure_ascii=False,
                ),
            )
            raw_tasks = value.get("tasks", [])
            if not isinstance(raw_tasks, list):
                raise ValueError("supervisor tasks must be a list")
            tasks: list[dict] = []
            for index, item in enumerate(raw_tasks[:6], 1):
                if not isinstance(item, dict) or item.get("agent") not in {
                    "order", "inventory", "refund"
                }:
                    raise ValueError("supervisor returned an invalid agent")
                dependencies = item.get("depends_on", [])
                tasks.append(
                    {
                        "task_id": str(item.get("task_id") or f"t{index}"),
                        "agent": str(item["agent"]),
                        "instruction": str(item.get("instruction") or request),
                        "depends_on": [str(dep) for dep in dependencies]
                        if isinstance(dependencies, list)
                        else [],
                    }
                )
            return tasks
        lowered = request.lower()
        tasks: list[PlannedTask] = []
        has_refund = any(
            word in lowered
            for word in ("退款", "退货", "refund", "能不能退", "可以退", "是否能退")
        )
        has_tracking = any(
            word in lowered for word in ("物流", "快递", "到哪", "tracking", "package")
        )
        has_inventory = any(word in lowered for word in ("库存", "有货", "stock", "inventory"))
        has_order = any(word in lowered for word in ("订单", "order", "状态", "买了什么"))

        if has_order or has_tracking or has_refund:
            instruction = request
            if has_refund and not has_tracking:
                instruction = f"查询订单状态：{request}"
            tasks.append(PlannedTask("t1", "order", instruction))
        if has_tracking:
            tasks[0] = PlannedTask("t1", "order", f"查询物流：{request}")
        if has_inventory:
            tasks.append(PlannedTask(f"t{len(tasks) + 1}", "inventory", request))
        if has_refund:
            dependency = ("t1",) if tasks and tasks[0].agent == "order" else ()
            tasks.append(
                PlannedTask(f"t{len(tasks) + 1}", "refund", request, dependency)
            )
        return [task.as_dict() for task in tasks]

    def choose(self, tasks: list[dict], completed: list[str]) -> dict | None:
        done = set(completed)
        for task in tasks:
            if task["task_id"] in done:
                continue
            if set(task.get("depends_on", [])).issubset(done):
                return task
        return None
