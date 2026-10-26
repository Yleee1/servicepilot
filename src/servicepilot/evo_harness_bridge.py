from __future__ import annotations

import argparse
import json
from pathlib import Path


def export_matrix(input_path: Path, workspace_root: Path, group: str = "M6") -> dict:
    """Export memory failures through official Evo-Harness workspace contracts."""

    try:
        from agent_evolve.contract.workspace import AgentWorkspace
        from agent_evolve.engine.observer import Observer
        from agent_evolve.types import Feedback, Observation, Task, Trajectory
    except ImportError as exc:
        raise RuntimeError(
            "Install optional dependency from A-EVO-Lab/a-evolve release/evo-harness"
        ) from exc

    matrix = json.loads(input_path.read_text(encoding="utf-8"))
    rows = matrix["groups"][group]["records"]
    workspace = AgentWorkspace(workspace_root)
    workspace.write_prompt(
        "ServicePilot memory harness. Skills may guide scope, conflict, write, retrieval, and "
        "recovery policy; memory must never grant permission or restore confirmation state."
    )
    observations = []
    for row in rows:
        passed = bool(row["passed"])
        observations.append(Observation(
            task=Task(
                id=str(row["case_id"]),
                input=str(row["case_id"]),
                metadata={"split": matrix["split"], "group": group},
            ),
            trajectory=Trajectory(
                task_id=str(row["case_id"]),
                output="passed" if passed else "failed",
                steps=[{"latency_ms": row["latency_ms"], "check_count": row["check_count"]}],
            ),
            feedback=Feedback(
                success=passed,
                score=float(passed),
                detail="memory scenario passed" if passed else "memory scenario failed",
                raw={"group": group},
            ),
        ))
    batch_path = Observer(workspace.evolution_dir).collect(observations)
    result = {
        "contract": "official evo-harness AgentWorkspace/Observer",
        "group": group,
        "observation_count": len(observations),
        "batch_path": str(batch_path),
        "workspace": str(workspace.root),
        "evolution_executed": False,
    }
    (workspace_root / "bridge_result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Export ServicePilot results to Evo-Harness")
    parser.add_argument("--input", default="results/memory_v3/matrix_frozen.json")
    parser.add_argument("--workspace", default="results/memory_v3/evo_harness_workspace")
    parser.add_argument("--group", default="M6")
    args = parser.parse_args()
    print(json.dumps(
        export_matrix(Path(args.input), Path(args.workspace), args.group),
        ensure_ascii=False,
        indent=2,
    ))


if __name__ == "__main__":
    main()
