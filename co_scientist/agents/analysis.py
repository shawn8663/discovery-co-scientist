"""Controlled analysis agent for uploaded experimental data artifacts."""

from __future__ import annotations

from datetime import UTC, datetime

from .. import ids
from ..models import AnalysisRun, Task, TaskResult
from ..storage.artifacts import write_json
from ..storage.repos import robin as robin_repo
from ..workspace import ScientistWorkspace
from .base import BaseAgent


class AnalysisAgent(BaseAgent):
    name = "analysis"

    async def execute(self, task: Task) -> TaskResult:
        if task.action != "AnalyzeExperimentalData":
            raise ValueError(f"AnalysisAgent does not handle action {task.action!r}")
        kind = task.payload.get("kind", "tabular")
        trajectories = int(task.payload.get("trajectories", 3))
        trajectories = max(1, min(trajectories, 8))
        dataset_ids = list(task.payload.get("dataset_artifact_ids") or [])
        run_id = ids.artifact_id()
        summary = (
            f"Prepared {trajectories} controlled {kind} analysis trajectory"
            f"{'ies' if trajectories != 1 else ''} for {len(dataset_ids)} dataset artifact(s)."
        )
        artifact_path = await write_json(
            self.deps.cfg,
            task.session_id,
            "analysis",
            run_id,
            {
                "kind": kind,
                "dataset_artifact_ids": dataset_ids,
                "trajectories": trajectories,
                "summary": summary,
                "execution_policy": "controlled_science_skill_wrapper",
            },
        )
        workspace = ScientistWorkspace(self.deps.cfg, task.session_id)
        workspace.add_artifact(
            kind="analysis",
            path=self.deps.cfg.data_dir / artifact_path,
            title=f"{kind} analysis run",
            provenance={"agent": self.name, "task_id": task.id},
            metadata={
                "analysis_run_id": run_id,
                "dataset_artifact_ids": dataset_ids,
                "trajectories": trajectories,
            },
        )
        run = AnalysisRun(
            id=run_id,
            session_id=task.session_id,
            created_at=datetime.now(UTC),
            kind=kind,
            dataset_artifact_ids=dataset_ids,
            trajectories=trajectories,
            summary=summary,
            artifact_path=artifact_path,
        )
        await robin_repo.insert_analysis_run(self.deps.db, run)
        return TaskResult(kind="analysis_completed", analysis_run_ids=[run_id])
