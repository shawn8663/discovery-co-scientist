"""Interpret analysis runs into experiment-informed discovery insights."""

from __future__ import annotations

from datetime import UTC, datetime

from .. import ids
from ..llm.anthropic_client import AgentCallSpec, CachedBlock, CallContext
from ..llm.prompts import render
from ..llm.routing import route
from ..models import ExperimentInsight, Task, TaskResult
from ..storage.artifacts import write_json
from ..storage.repos import robin as robin_repo
from ..storage.repos import sessions as sess_repo
from .base import BaseAgent
from .robin_helpers import final_text, parse_json_object


class ResultInterpreterAgent(BaseAgent):
    name = "result_interpreter"

    async def execute(self, task: Task) -> TaskResult:
        if task.action != "InterpretResults":
            raise ValueError(f"ResultInterpreterAgent does not handle action {task.action!r}")
        if not task.target_id:
            raise ValueError("InterpretResults requires target_id")
        session = await sess_repo.fetch(self.deps.db, task.session_id)
        run = await robin_repo.fetch_analysis_run(self.deps.db, task.target_id)
        if session is None or run is None:
            raise RuntimeError("missing session or analysis run")
        prompt = render(
            "robin.result_interpretation",
            disease_name=session.research_plan.objective,
            analysis_summary=run.summary,
        )
        spec = AgentCallSpec(
            route=route(self.deps.cfg, "generation", "literature"),
            system_blocks=[CachedBlock(self._system_prompt_header(), cache=True)],
            user_blocks=[CachedBlock(prompt, cache=False)],
            tools=[],
            max_output_tokens=4096,
        )
        resp = await self.deps.llm.call(
            spec,
            CallContext(
                session_id=task.session_id,
                task_id=task.id,
                agent=self.name,
                action=task.action,
                mode="result_interpretation",
            ),
        )
        record = parse_json_object(final_text(resp))
        insight_id = ids.artifact_id()
        artifact_path = await write_json(
            self.deps.cfg,
            session.id,
            "insights",
            insight_id,
            {"analysis_run_id": run.id, "record": record},
        )
        insight = ExperimentInsight(
            id=insight_id,
            session_id=session.id,
            analysis_run_id=run.id,
            created_at=datetime.now(UTC),
            summary=str(record.get("summary") or ""),
            positive_hits=list(record.get("positive_hits") or []),
            negative_hits=list(record.get("negative_hits") or []),
            suggested_mechanisms=list(record.get("suggested_mechanisms") or []),
            follow_up_assays=list(record.get("follow_up_assays") or []),
            constraints=list(record.get("constraints") or []),
            artifact_path=artifact_path,
        )
        await robin_repo.insert_experiment_insight(self.deps.db, insight)
        return TaskResult(kind="experiment_insight_created", insight_ids=[insight_id])
