from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

from agent_evolve.contract.workspace import AgentWorkspace

from .evo_harness_bridge import export_matrix
from .memory_ablation_v3 import evaluate_group

EVO_HARNESS_COMMIT = "3c7c7b8c62a3f07da1d4407aa03f0ca079954bcf"
_SAFETY_CASE_TERMS = ("cross_user", "operational_state", "injection", "task_scope")


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _compile_candidate(
    client,
    *,
    cycle: int,
    baseline: dict,
    prior_attempts: list[dict],
) -> dict:
    prior_compact = [
        {
            "cycle": item["cycle"],
            "minimum_overlap": item["candidate"]["minimum_overlap"],
            "dev_success": item["dev_result"]["scenario_success"],
            "dev_failed_count": len(item["dev_result"]["failed_case_ids"]),
            "decision": item["decision"],
        }
        for item in prior_attempts
    ]
    value = client.chat_json(
        "你是 Evo-Harness 的记忆检索策略编译器。不要复述输入。只允许根据聚合 dev 反馈选择一种通用的"
        "character-bigram relevance filter，不能读取 held-out/frozen、答案、scorer 或具体记忆内容。"
        "minimum_overlap 必须在 0.01 到 0.5 之间。目标是过滤零相关召回，同时保留措辞不同但共享"
        "关键短语的记忆。必须且只能输出包含 hypothesis、guidance、retrieval_filter、"
        "minimum_overlap 四个字段的 JSON；retrieval_filter 固定写 character_bigram_overlap。",
        (
            f"第 {cycle} 轮。M6 dev success={baseline['scenario_success']:.4f}；"
            "失败类型="
            + ",".join(
                case_id.split("_", 2)[-1] for case_id in baseline["failed_case_ids"]
            )
            + f"。此前候选={json.dumps(prior_compact, ensure_ascii=False)}。"
            "请生成一个通用策略，不要复述本段。"
        ),
    )
    filter_name = str(
        value.get("retrieval_filter") or "character_bigram_overlap"
    ).strip().lower().replace("-", "_")
    if filter_name not in {
        "character_bigram_overlap",
        "character_bigram_relevance_filter",
        "char_bigram_overlap",
    }:
        raise ValueError(f"evolver selected a non-allow-listed filter: {filter_name!r}")
    threshold = value.get("minimum_overlap")
    if not isinstance(threshold, (int, float)) or not 0.01 <= float(threshold) <= 0.5:
        raise ValueError(
            f"evolver threshold must be in [0.01, 0.5], got {threshold!r}; keys={sorted(value)}"
        )
    guidance = str(value.get("guidance", "")).strip()
    if not guidance or len(guidance) > 800:
        raise ValueError("evolved guidance must contain 1-800 characters")
    lowered = guidance.lower()
    if any(term in lowered for term in ("frozen", "gold", "scorer", "u001", "[d]")):
        raise ValueError("evolved guidance contains protected or instance-specific content")
    candidate = {
        "hypothesis": str(value.get("hypothesis", "")).strip()[:500],
        "guidance": guidance,
        "retrieval_filter": "character_bigram_overlap",
        "minimum_overlap": float(threshold),
    }
    if prior_attempts:
        previous = prior_attempts[-1]
        previous_threshold = float(previous["candidate"]["minimum_overlap"])
        if (
            previous["decision"] == "rejected_dev"
            and candidate["minimum_overlap"] >= previous_threshold
        ):
            candidate["minimum_overlap"] = max(0.01, previous_threshold / 4)
            candidate["hypothesis"] += "; dev Gate 自动放宽过严阈值"
            candidate["guidance"] += " Dev Gate 发现过严过滤后降低阈值，避免相关记忆假阴性。"
    return candidate


def _write_candidate(workspace: AgentWorkspace, candidate: dict, cycle: int) -> str:
    skill = (
        "---\n"
        "name: servicepilot-memory-v3\n"
        "description: Dev-trajectory-compiled memory retrieval policy.\n"
        "---\n\n"
        f"# ServicePilot evolved memory skill, cycle {cycle}\n\n"
        f"{candidate['guidance']}\n"
    )
    workspace.write_skill("servicepilot-memory-v3", skill)
    policy_path = workspace.skills_dir / "servicepilot-memory-v3" / "policy.json"
    policy = {
        "cycle": cycle,
        "hypothesis": candidate["hypothesis"],
        "retrieval_filter": candidate["retrieval_filter"],
        "minimum_overlap": candidate["minimum_overlap"],
    }
    policy_path.write_text(
        json.dumps(policy, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return hashlib.sha256((skill + policy_path.read_text()).encode()).hexdigest()


def _safety_regressions(group: dict) -> list[str]:
    return [
        row["case_id"]
        for row in group["records"]
        if not row["passed"] and any(term in row["case_id"] for term in _SAFETY_CASE_TERMS)
    ]


def run_evolution(args: argparse.Namespace) -> dict:
    result_root = Path(args.result_root)
    data_root = Path(args.data_root)
    workspace_root = result_root / "evo_m7_workspace"
    workspace = AgentWorkspace(workspace_root)
    workspace.write_prompt(
        "ServicePilot M7. Evolve only memory retrieval guidance and allow-listed relevance "
        "parameters. Memory never grants permission, confirmation, or operational state."
    )
    dev_matrix_path = result_root / "matrix_dev.json"
    heldout_matrix_path = result_root / "matrix_heldout.json"
    frozen_matrix_path = result_root / "matrix_frozen.json"
    dev_matrix = _load_json(dev_matrix_path)
    heldout_matrix = _load_json(heldout_matrix_path)
    baseline_dev = dev_matrix["groups"]["M6"]
    baseline_heldout = heldout_matrix["groups"]["M6"]
    export_matrix(dev_matrix_path, workspace_root, "M6")
    if args.compiler != "mlx":
        raise ValueError("M7 compiler is restricted to local MLX to prevent eval-data egress")
    from .mlx_client import MLXJSONClient

    client = MLXJSONClient(args.model, max_tokens=512)
    attempts: list[dict] = []
    accepted: dict | None = None
    for cycle in range(1, args.max_cycles + 1):
        candidate = _compile_candidate(
            client,
            cycle=cycle,
            baseline=baseline_dev,
            prior_attempts=attempts,
        )
        candidate_hash = _write_candidate(workspace, candidate, cycle)
        policy = {
            "retrieval_filter": candidate["retrieval_filter"],
            "minimum_overlap": candidate["minimum_overlap"],
        }
        dev_result = evaluate_group(
            data_root / "dev_v3.jsonl", "M7", m7_policy=policy
        )
        dev_regressions = _safety_regressions(dev_result)
        dev_delta = dev_result["scenario_success"] - baseline_dev["scenario_success"]
        heldout_result = None
        heldout_delta = None
        regressions = dev_regressions
        decision = "rejected_dev"
        if dev_delta > 0 and not dev_regressions:
            heldout_result = evaluate_group(
                data_root / "heldout_v3.jsonl", "M7", m7_policy=policy
            )
            regressions = _safety_regressions(heldout_result)
            heldout_delta = (
                heldout_result["scenario_success"] - baseline_heldout["scenario_success"]
            )
            decision = (
                "accepted" if heldout_delta > 0 and not regressions else "rejected_heldout"
            )
        attempt = {
            "cycle": cycle,
            "candidate_hash": candidate_hash,
            "candidate": candidate,
            "dev_result": dev_result,
            "heldout_result": heldout_result,
            "dev_delta": dev_delta,
            "heldout_delta": heldout_delta,
            "safety_regressions": regressions,
            "decision": decision,
        }
        attempts.append(attempt)
        if decision == "accepted":
            accepted = attempt
            break

    frozen_result = None
    if accepted is not None:
        policy = {
            "retrieval_filter": accepted["candidate"]["retrieval_filter"],
            "minimum_overlap": accepted["candidate"]["minimum_overlap"],
        }
        frozen_result = evaluate_group(
            data_root / "frozen_v3.jsonl", "M7", m7_policy=policy
        )
        frozen_matrix = _load_json(frozen_matrix_path)
        frozen_matrix["groups"]["M7"] = {
            **frozen_result,
            "evolution_executed": True,
            "accepted_cycle": accepted["cycle"],
            "candidate_hash": accepted["candidate_hash"],
        }
        frozen_matrix_path.write_text(
            json.dumps(frozen_matrix, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        export_matrix(frozen_matrix_path, workspace_root, "M7")
    else:
        skill_dir = workspace.skills_dir / "servicepilot-memory-v3"
        disabled = workspace.skills_dir / "disabled" / "servicepilot-memory-v3"
        disabled.parent.mkdir(parents=True, exist_ok=True)
        if disabled.exists():
            shutil.rmtree(disabled)
        if skill_dir.exists():
            shutil.move(str(skill_dir), str(disabled))

    result = {
        "pipeline": "M7_context_to_harness",
        "official_evo_harness_commit": EVO_HARNESS_COMMIT,
        "evolution_executed": True,
        "cycles_completed": len(attempts),
        "accepted_cycle": accepted["cycle"] if accepted else None,
        "baseline": {"dev": baseline_dev, "heldout": baseline_heldout},
        "attempts": attempts,
        "frozen_result": frozen_result,
        "compiler_usage": client.usage,
        "frozen_used_for_selection": False,
    }
    output = result_root / "M7_evolution.json"
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    workspace.evolution_dir.mkdir(parents=True, exist_ok=True)
    (workspace.evolution_dir / "history.jsonl").write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in attempts),
        encoding="utf-8",
    )
    bridge_path = workspace_root / "bridge_result.json"
    if bridge_path.exists():
        bridge = _load_json(bridge_path)
        bridge.update({
            "pipeline": result["pipeline"],
            "evolution_executed": True,
            "cycles_completed": len(attempts),
            "accepted_cycle": result["accepted_cycle"],
            "frozen_used_for_selection": False,
        })
        bridge_path.write_text(
            json.dumps(bridge, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run ServicePilot M7 Evo-Harness loop")
    parser.add_argument("--data-root", default="data/eval/memory_v3")
    parser.add_argument("--result-root", default="results/memory_v3")
    parser.add_argument("--compiler", choices=("mlx",), default="mlx")
    parser.add_argument("--model", default="models/Qwen3-1.7B-4bit")
    parser.add_argument("--max-cycles", type=int, default=2)
    return parser


def main() -> None:
    result = run_evolution(build_parser().parse_args())
    print(json.dumps({
        "evolution_executed": result["evolution_executed"],
        "cycles_completed": result["cycles_completed"],
        "accepted_cycle": result["accepted_cycle"],
        "frozen_scenario_success": (
            result["frozen_result"]["scenario_success"]
            if result["frozen_result"] else None
        ),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
