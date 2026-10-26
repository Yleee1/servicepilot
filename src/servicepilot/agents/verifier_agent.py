from __future__ import annotations

import json

VERIFIER_SYSTEM_PROMPT = (
    "你是无工具权限的 Verifier Agent。检查原任务是否由 observation 完成，工具失败后不得"
    "宣称成功。只输出JSON：passed、missing_tasks、unsupported_claims、next_action。"
)


class VerifierAgent:
    allowed_tools: tuple[str, ...] = ()

    def __init__(self, client=None) -> None:
        self.client = client

    def verify(self, state: dict) -> dict:
        task_ids = {task["task_id"] for task in state.get("task_queue", [])}
        completed = set(state.get("completed_tasks", []))
        missing = sorted(task_ids - completed)
        failed = [
            item for item in state.get("observations", []) if item.get("ok") is False
        ]
        unsupported: list[str] = []
        final_answer = str(state.get("final_answer", ""))
        if "成功" in final_answer and failed:
            unsupported.append("tool failed but draft claimed success")
        deterministic_passed = not missing and not failed and not unsupported
        model_value = None
        if self.client is not None:
            model_value = self.client.chat_json(
                VERIFIER_SYSTEM_PROMPT,
                json.dumps(
                    {
                        "request": state.get("request", ""),
                        "tasks": state.get("task_queue", []),
                        "observations": state.get("observations", []),
                        "draft": final_answer,
                    },
                    ensure_ascii=False,
                ),
            )
            if not isinstance(model_value.get("passed"), bool):
                raise ValueError("verifier passed must be boolean")
        passed = deterministic_passed and (
            model_value.get("passed") is True if model_value is not None else True
        )
        if model_value is not None:
            raw_missing = model_value.get("missing_tasks", [])
            raw_unsupported = model_value.get("unsupported_claims", [])
            if isinstance(raw_missing, list):
                missing = sorted(set(missing) | {str(item) for item in raw_missing})
            if isinstance(raw_unsupported, list):
                unsupported = list(
                    dict.fromkeys(unsupported + [str(item) for item in raw_unsupported])
                )
        return {
            "passed": passed,
            "missing_tasks": missing,
            "unsupported_claims": unsupported,
            "failed_observations": [item.get("code") for item in failed],
            "next_action": "pass" if passed else "retry",
        }
