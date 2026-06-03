"""Robin assay agent: propose, evaluate, and rank experimental assays."""

from __future__ import annotations

from datetime import UTC, datetime

from .. import ids
from ..llm.anthropic_client import AgentCallSpec, CachedBlock, CallContext
from ..llm.prompts import render
from ..llm.routing import route
from ..models import AssayEvaluation, AssayProposal, Task, TaskResult
from ..orchestrator.btl import rank_btl
from ..safety.gates import assess_safety
from ..storage.artifacts import write_json
from ..storage.repos import robin as robin_repo
from ..storage.repos import sessions as sess_repo
from .base import BaseAgent
from .robin_helpers import final_text, parse_json_array, split_evaluation_sections


class AssayAgent(BaseAgent):
    name = "assay"

    async def execute(self, task: Task) -> TaskResult:
        if task.action == "GenerateAssays":
            return await self._generate(task)
        if task.action == "EvaluateAssay":
            return await self._evaluate(task)
        if task.action == "RankAssays":
            return await self._rank(task)
        raise ValueError(f"AssayAgent does not handle action {task.action!r}")

    async def _generate(self, task: Task) -> TaskResult:
        session = await sess_repo.fetch(self.deps.db, task.session_id)
        if session is None:
            raise RuntimeError(f"session {task.session_id} missing")
        num = int(task.payload.get("num_assays", 10))
        prompt = render(
            "robin.assay_generation",
            disease_name=session.research_plan.objective,
            num_assays=num,
            assay_lit_review_output=task.payload.get("literature_summary", ""),
        )
        text = await self._call_text(task, prompt, mode="assay_generation")
        records = parse_json_array(text)
        out: list[str] = []
        for record in records[:num]:
            strategy_name = str(record.get("strategy_name") or "").strip()
            reasoning = str(record.get("reasoning") or "").strip()
            if not strategy_name:
                continue
            safety = await assess_safety(
                self.deps.cfg,
                f"{strategy_name}\n\n{reasoning}",
                label="assay_proposal",
            )
            aid = ids.artifact_id()
            artifact_path = await write_json(
                self.deps.cfg,
                session.id,
                "robin/assays",
                aid,
                {
                    "record": record,
                    "prompt": "robin.assay_generation",
                    "safety": safety.artifact(),
                },
            )
            assay = AssayProposal(
                id=aid,
                session_id=session.id,
                created_at=datetime.now(UTC),
                round_index=int(task.payload.get("round_index", 1)),
                strategy_name=strategy_name,
                reasoning=reasoning,
                artifact_path=artifact_path,
                state="quarantined" if safety.should_stop else "proposed",
            )
            if not safety.should_stop and await robin_repo.insert_assay(self.deps.db, assay):
                out.append(aid)
            elif safety.should_stop:
                await robin_repo.insert_assay(self.deps.db, assay)
        return TaskResult(kind="assay_created", assay_ids=out)

    async def _evaluate(self, task: Task) -> TaskResult:
        if not task.target_id:
            raise ValueError("EvaluateAssay requires target_id")
        session = await sess_repo.fetch(self.deps.db, task.session_id)
        assay = await robin_repo.fetch_assay(self.deps.db, task.target_id)
        if session is None or assay is None:
            raise RuntimeError("missing session or assay")
        prompt = render(
            "robin.assay_evaluation",
            disease_name=session.research_plan.objective,
            assay=assay.model_dump_json(indent=2),
        )
        text = await self._call_text(task, prompt, mode="assay_evaluation")
        sections = split_evaluation_sections(
            text,
            ("Assay Overview", "Biomedical Evidence", "Previous Use", "Overall Evaluation"),
        )
        eid = ids.artifact_id()
        artifact_path = await write_json(
            self.deps.cfg,
            session.id,
            "robin/assay_evaluations",
            eid,
            {"assay_id": assay.id, "text": text, "sections": sections},
        )
        evaluation = AssayEvaluation(
            id=eid,
            assay_id=assay.id,
            session_id=session.id,
            created_at=datetime.now(UTC),
            overview=sections["Assay Overview"],
            biomedical_evidence=sections["Biomedical Evidence"],
            previous_use=sections["Previous Use"],
            overall_evaluation=sections["Overall Evaluation"],
            artifact_path=artifact_path,
        )
        await robin_repo.insert_assay_evaluation(self.deps.db, evaluation)
        return TaskResult(kind="assay_evaluated", assay_ids=[assay.id])

    async def _rank(self, task: Task) -> TaskResult:
        assay_ids = list(task.payload.get("assay_ids") or [])
        if not assay_ids:
            assays = await robin_repo.list_assays(self.deps.db, task.session_id)
            assay_ids = [a.id for a in assays]
        comparisons = [
            tuple(item) for item in task.payload.get("comparisons", [])
            if isinstance(item, (list, tuple)) and len(item) == 3
        ]
        scores = rank_btl(assay_ids, comparisons) if comparisons else {
            aid: float(len(assay_ids) - i) for i, aid in enumerate(assay_ids)
        }
        for aid, score in scores.items():
            await robin_repo.set_assay_rank_score(self.deps.db, aid, float(score))
        winner = next(iter(scores), None)
        return TaskResult(
            kind="assays_ranked",
            assay_ids=list(scores.keys()),
            extra={"winner_assay_id": winner},
        )

    async def _call_text(self, task: Task, prompt: str, *, mode: str) -> str:
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
                mode=mode,
            ),
        )
        return final_text(resp)
